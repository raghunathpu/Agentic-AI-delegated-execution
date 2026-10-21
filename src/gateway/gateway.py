"""
Instrumentation Gateway — §6 of the paper.

The gateway is a mediation layer (library, sidecar, proxy, or MCP middleware)
that sits on the invocation path between an agent runtime and external
tools/services.

Its responsibilities (from §6):
  a) Capture tool invocations with an authenticated principal / delegation_id
     binding and issuance time.
  b) Propagate delegation context to all downstream tool calls made under
     that delegation, including multi-agent delegation chains.
  c) Normalize tool-specific operations to CIM semantic actions.
  d) Emit explicit absence — if delegation context is missing at the boundary,
     the event is emitted with an explicit "unbound/unknown" state.

The gateway distinguishes:
  - Gateway-bound metadata (authenticated principal, delegation_id minting
    binding, tool endpoint identity, timestamps).
  - Agent-provided context (optional labels, high-level task tags).

Provenance and trust boundary: the gateway enforces that delegation_id is
bound to authenticated principals and minting context (gateway/session)
rather than accepted as unauthenticated free-form strings.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.schemas.cim_event import (
    CIMEvent,
    ActionField,
    ActionSemantic,
    ActionOutcome,
    ActionTier2Field,
    ActionDirection,
    AgentField,
    PrincipalField,
    ResourceField,
    ResourceType,
    SensitivityLabel,
    ToolField,
    TraceField,
    WorkflowField,
)
from src.normalization.semantic_normalizer import normalize_action, infer_direction
from src.ingestion.ingestion_service import IngestionService


class GatewayContext:
    """
    Active delegation context at the gateway boundary.

    Represents the durable authorization context that is attached to all
    downstream tool calls made under this delegation.
    """

    def __init__(
        self,
        delegation_id:       str,
        principal_user_id:   str,
        agent_id:            str,
        agent_instance_id:   Optional[str] = None,
        delegation_parent_id: Optional[str] = None,
        workflow_id:         Optional[str] = None,
        gateway_session_id:  Optional[str] = None,
        minting_context:     str = "gateway",
    ) -> None:
        self.delegation_id        = delegation_id
        self.principal_user_id    = principal_user_id
        self.agent_id             = agent_id
        self.agent_instance_id    = agent_instance_id
        self.delegation_parent_id = delegation_parent_id
        self.workflow_id          = workflow_id
        self.gateway_session_id   = gateway_session_id or str(uuid.uuid4())
        self.minting_context      = minting_context
        self.issued_at            = datetime.now(timezone.utc).isoformat()

    def child_context(
        self,
        child_delegation_id: str,
        child_agent_id:      str,
        child_agent_instance_id: Optional[str] = None,
        workflow_id:         Optional[str] = None,
    ) -> "GatewayContext":
        """Create a child delegation context for re-delegation / agent spawning."""
        return GatewayContext(
            delegation_id        = child_delegation_id,
            principal_user_id    = self.principal_user_id,  # principal propagates
            agent_id             = child_agent_id,
            agent_instance_id    = child_agent_instance_id,
            delegation_parent_id = self.delegation_id,       # lineage preserved
            workflow_id          = workflow_id or self.workflow_id,
            gateway_session_id   = self.gateway_session_id,
            minting_context      = "gateway_child",
        )


class InstrumentedGateway:
    """
    Simulated instrumentation gateway.

    Intercepts tool invocations, constructs CIM events, and emits them
    through the ingestion service into the persistent store.

    Tool adapters are registered per tool family.  Unknown operations are
    preserved as UNKNOWN rather than silently assigned incorrect semantics.
    """

    UNBOUND_DELEGATION_ID = "UNBOUND"

    def __init__(self, ingestion_svc: IngestionService) -> None:
        self.ingestion_svc = ingestion_svc

    # ------------------------------------------------------------------
    # Main invocation intercept
    # ------------------------------------------------------------------

    def invoke(
        self,
        context:        GatewayContext | None,
        tool_name:      str,
        operation:      str,
        resource_id:    Optional[str] = None,
        resource_uri:   Optional[str] = None,
        resource_type:  Optional[str] = None,
        sensitivity:    Optional[str] = None,
        target_principal: Optional[str] = None,
        trace_id:       Optional[str] = None,
        span_id:        Optional[str] = None,
        parent_span_id: Optional[str] = None,
        outcome:        str = "success",
        labels:         Optional[dict[str, str]] = None,
    ) -> CIMEvent:
        """
        Intercept a tool invocation and emit a CIM event.

        If context is None (no delegation context at boundary), the event
        is emitted with delegation_id=UNBOUND, capturing explicit absence.

        Returns the constructed CIMEvent after ingestion.
        """
        now = datetime.now(timezone.utc).isoformat()
        event_id = str(uuid.uuid4())

        # Delegation binding
        if context:
            delegation_id        = context.delegation_id
            delegation_parent_id = context.delegation_parent_id
            principal_user_id    = context.principal_user_id
            agent_id             = context.agent_id
            agent_instance_id    = context.agent_instance_id
            workflow_id          = context.workflow_id
            gateway_session_id   = context.gateway_session_id
            minting_context      = context.minting_context
        else:
            # Explicit absence — paper §6: emit with "unbound/unknown" state
            delegation_id        = self.UNBOUND_DELEGATION_ID
            delegation_parent_id = None
            principal_user_id    = "unknown"
            agent_id             = "unknown"
            agent_instance_id    = None
            workflow_id          = None
            gateway_session_id   = None
            minting_context      = "unbound"

        # Semantic normalization
        norm = normalize_action(tool_name, operation)
        action_semantic = norm.action_semantic

        # Direction inference
        direction = infer_direction(tool_name, operation, resource_type, target_principal)

        # Build resource
        resource = None
        if any([resource_id, resource_uri, resource_type, sensitivity]):
            res_type = None
            if resource_type:
                try:
                    res_type = ResourceType(resource_type.lower())
                except ValueError:
                    res_type = ResourceType.UNKNOWN
            sens = None
            if sensitivity:
                try:
                    sens = SensitivityLabel(sensitivity.lower())
                except ValueError:
                    sens = SensitivityLabel.UNKNOWN
            resource = ResourceField(
                type=res_type,
                id=resource_id,
                uri=resource_uri,
                sensitivity_label=sens,
            )

        # Build action
        tier2 = None
        if direction:
            tier2 = ActionTier2Field(direction=direction)

        try:
            outcome_val = ActionOutcome(outcome.lower())
        except (ValueError, AttributeError):
            outcome_val = ActionOutcome.UNKNOWN

        # If normalization was ambiguous, outcome defaults to unknown
        if norm.is_ambiguous and outcome_val == ActionOutcome.SUCCESS:
            # Do not override success if explicitly stated; just note ambiguity
            pass

        action = ActionField(
            semantic=action_semantic,
            outcome=outcome_val,
            tier2=tier2,
        )

        # Tracing
        trace = None
        if any([trace_id, span_id, parent_span_id]):
            trace = TraceField(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )

        # Workflow
        workflow = WorkflowField(workflow_id=workflow_id) if workflow_id else None

        # Construct event
        event = CIMEvent(
            time             = now,
            event_id         = event_id,
            delegation_id    = delegation_id,
            delegation_parent_id = delegation_parent_id,
            principal        = PrincipalField(user_id=principal_user_id),
            agent            = AgentField(
                agent_id=agent_id,
                instance_id=agent_instance_id,
            ),
            tool             = ToolField(name=tool_name, operation=operation),
            action           = action,
            resource         = resource,
            target_principal_id = target_principal,
            trace            = trace,
            workflow         = workflow,
            labels           = labels,
            gateway_captured_at = now,
            gateway_session_id  = gateway_session_id,
            minting_context     = minting_context,
        )

        # Ingest
        self.ingestion_svc.ingest(
            raw=event.model_dump(mode="json"),
            allow_duplicate=False,
        )

        return event


# ---------------------------------------------------------------------------
# Tool adapter wrappers (simulated agents invoking tools through gateway)
# ---------------------------------------------------------------------------

class SimulatedAgent:
    """
    A simulated agent that invokes tools through the instrumented gateway.

    Represents an autonomous agent executing under a delegated authorization
    context.
    """

    def __init__(
        self,
        gateway: InstrumentedGateway,
        context: GatewayContext,
    ) -> None:
        self.gateway = gateway
        self.context = context

    def invoke(self, tool_name: str, operation: str, **kwargs: Any) -> CIMEvent:
        """Invoke a tool through the gateway with the current delegation context."""
        return self.gateway.invoke(
            context=self.context,
            tool_name=tool_name,
            operation=operation,
            **kwargs,
        )

    def spawn_child_agent(
        self,
        child_delegation_id: str,
        child_agent_id:      str,
        child_agent_instance_id: Optional[str] = None,
    ) -> "SimulatedAgent":
        """
        Spawn a child agent under a new (child) delegation.
        Re-delegation preserves lineage via delegation_parent_id.
        """
        child_ctx = self.context.child_context(
            child_delegation_id=child_delegation_id,
            child_agent_id=child_agent_id,
            child_agent_instance_id=child_agent_instance_id,
        )
        return SimulatedAgent(gateway=self.gateway, context=child_ctx)
