"""
CIM Event Schema — Appendix B.1 of the paper.

Table 8: Minimal CIM event fields.
Fields marked Req are required for delegation-aware reconstruction;
others are optional but recommended when available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import uuid


# ---------------------------------------------------------------------------
# Enumerations  (Tier-1 verb set + Tier-2 qualifiers from §4 and Appendix B.2)
# ---------------------------------------------------------------------------

class ActionSemantic(str, Enum):
    """
    Tier-1 normalized semantic action vocabulary.
    CIM normalises actions in two tiers.
    Tier-1 defines a required minimal verb set: read, write, share, invoke.
    Implementations may extend with additional Tier-2 verbs.
    """
    # Read and discovery
    READ    = "read"
    SEARCH  = "search"
    ENUMERATE = "enumerate"

    # Write and modification
    WRITE   = "write"
    DELETE  = "delete"
    MOVE    = "move"

    # Sharing and authorization-relevant actions
    SHARE   = "share"
    REVOKE  = "revoke"
    INVITE  = "invite"

    # Execution and delegation
    INVOKE  = "invoke"
    SPAWN   = "spawn"

    # Extended Tier-2 verbs (documented in paper as allowed extensions)
    DOWNLOAD  = "download"
    UPLOAD    = "upload"
    EXECUTE   = "execute"
    PERMISSION_CHANGE = "permission_change"
    ADMIN_CONFIG      = "admin_config"

    # Fallback when normalization is ambiguous  (paper §B.2: record unknown)
    UNKNOWN = "unknown"


class ActionOutcome(str, Enum):
    """Outcome of the tool invocation. (Table 8: {success, failure, unknown})"""
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


class ActionDirection(str, Enum):
    """
    Tier-2 qualifier for boundary crossing.
    {internal, external, unknown}.  Used in §4 queries (e.g., externalization).
    """
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNKNOWN  = "unknown"


class PrincipalScope(str, Enum):
    """
    Tier-2 qualifier for the target principal category.
    {self, org, external, unknown}.  Used for 'shared with external'-style analyses.
    """
    SELF     = "self"
    ORG      = "org"
    EXTERNAL = "external"
    UNKNOWN  = "unknown"


class ResourceType(str, Enum):
    """
    Resource category.
    Paper: document, repo, channel, ticket — treat resource_class the same.
    """
    DOCUMENT  = "document"
    REPO      = "repo"
    CHANNEL   = "channel"
    TICKET    = "ticket"
    FILE      = "file"
    DATABASE  = "database"
    API       = "api"
    EMAIL     = "email"
    CALENDAR  = "calendar"
    UNKNOWN   = "unknown"


class SensitivityLabel(str, Enum):
    """
    Tier-2 qualifier for sensitivity.
    Paper: {public, internal, confidential, restricted, unknown}.
    """
    PUBLIC       = "public"
    INTERNAL     = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED   = "restricted"
    UNKNOWN      = "unknown"


# ---------------------------------------------------------------------------
# Nested models
# ---------------------------------------------------------------------------

class PrincipalField(BaseModel):
    """
    Human principal that originated the delegated authority.
    Paper field: principal.user_id (Req, string).
    """
    user_id: str = Field(..., description="Human principal that originated the delegated authority (stable ID).")


class AgentField(BaseModel):
    """
    Agent identification.
    Paper fields: agent.agent_id (Req), agent.instance_id (Opt).
    """
    agent_id:     str           = Field(..., description="Logical agent entity; stable across runs.")
    instance_id:  Optional[str] = Field(None, description="Concrete agent process/session instance (restart-sensitive).")


class ToolField(BaseModel):
    """
    Tool information.
    Paper fields: tool.name (Req), tool.operation (Opt).
    """
    name:      str           = Field(..., description="Tool family name (e.g., gdocs, github, drive, slack).")
    operation: Optional[str] = Field(None, description="Tool-native operation name (e.g., API endpoint, RPC method).")


class ActionField(BaseModel):
    """
    Normalized and raw action information.
    Paper fields: action.semantic (Req), action.outcome (Opt),
                  action.tier2.direction (Opt), action.tier2.principal_scope (Opt).
    """
    semantic:  ActionSemantic            = Field(..., description="Tier-1 normalized semantic action.")
    outcome:   Optional[ActionOutcome]   = Field(None, description="{success, failure, unknown}.")
    tier2: Optional[ActionTier2Field]    = Field(None, description="Tier-2 qualifiers.")


class ActionTier2Field(BaseModel):
    """Tier-2 action qualifiers (optional enrichment from §4)."""
    direction:       Optional[ActionDirection] = Field(None, description="Boundary crossing direction.")
    principal_scope: Optional[PrincipalScope]  = Field(None, description="Target principal category.")


# Fix forward reference
ActionField.model_rebuild()


class ResourceField(BaseModel):
    """
    Resource information.
    Paper fields: resource.type (Opt), resource.id (Opt), resource.uri (Opt),
                  resource.sensitivity_label (Opt).
    """
    type:              Optional[ResourceType]      = Field(None, description="Resource category.")
    id:                Optional[str]               = Field(None, description="Tool-scoped identifier.")
    uri:               Optional[str]               = Field(None, description="Canonical URI/locator.")
    sensitivity_label: Optional[SensitivityLabel]  = Field(None, description="Sensitivity tier-2 qualifier.")


class TraceField(BaseModel):
    """
    Workflow correlation / tracing identifiers.
    Paper fields: trace.trace_id (Opt), trace.span_id (Opt), trace.parent_span_id (Opt).
    Tracing fields are optional — CIM preserves authority-flow even when
    control-flow metadata is absent.
    """
    trace_id:      Optional[str] = Field(None, description="Tracing identifier if present.")
    span_id:       Optional[str] = Field(None, description="Span identifier if present.")
    parent_span_id: Optional[str] = Field(None, description="Parent span identifier if present.")


class WorkflowField(BaseModel):
    """
    Optional higher-level workflow correlation.
    Paper field: workflow.workflow_id.
    """
    workflow_id: Optional[str] = Field(None, description="Optional higher-level workflow correlation ID.")


# ---------------------------------------------------------------------------
# Core CIM Event (Table 8 — Appendix B.1)
# ---------------------------------------------------------------------------

class CIMEvent(BaseModel):
    """
    CIM event record — Appendix B.1, Table 8.

    Each CIM record is a single event describing one tool-relevant action
    (e.g., read, write, share, invoke).  Fields marked Req are required
    for delegation-aware reconstruction.

    Required fields for the core claims:
      time, event_id, delegation_id, principal.user_id,
      agent.agent_id, tool.name, action.semantic
    """
    # --- Required fields ----
    time:          str   = Field(..., description="Event timestamp at capture point (RFC3339 string).")
    event_id:      str   = Field(..., description="Unique identifier for this CIM event (UUID or equivalent).")
    delegation_id: str   = Field(..., description="Durable authorization context identifier; defines the delegation partition over events.")

    principal: PrincipalField = Field(..., description="Human principal that originated the delegated authority.")
    agent:     AgentField     = Field(..., description="Agent identification.")
    tool:      ToolField      = Field(..., description="Tool information.")
    action:    ActionField    = Field(..., description="Normalized and raw action information.")

    # --- Optional fields ----
    delegation_parent_id: Optional[str]        = Field(None, description="Parent delegation ID for lineage; None for root delegations.")
    resource:             Optional[ResourceField] = Field(None, description="Resource information.")
    target_principal_id:  Optional[str]        = Field(None, description="Recipient/target principal for sharing/invites.")
    trace:                Optional[TraceField]  = Field(None, description="Tracing correlation identifiers.")
    workflow:             Optional[WorkflowField] = Field(None, description="Workflow correlation.")
    labels:               Optional[dict[str, str]] = Field(None, description="Free-form labels for deployment-specific tagging.")

    # --- Gateway provenance metadata (not in original tool payload) ----
    gateway_captured_at:  Optional[str]  = Field(None, description="Timestamp when the gateway captured this event.")
    gateway_session_id:   Optional[str]  = Field(None, description="Gateway session binding.")
    minting_context:      Optional[str]  = Field(None, description="Context in which delegation_id was minted/bound.")

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        """Validate RFC3339 timestamp."""
        try:
            # Accept common ISO formats
            v_clean = v.replace("Z", "+00:00")
            datetime.fromisoformat(v_clean)
        except (ValueError, AttributeError):
            raise ValueError(f"time must be RFC3339 / ISO8601 format, got: {v!r}")
        return v

    @field_validator("event_id", "delegation_id")
    @classmethod
    def validate_non_empty_string(cls, v: str, info: Any) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v

    @model_validator(mode="after")
    def validate_delegation_parent_not_self(self) -> "CIMEvent":
        if self.delegation_parent_id and self.delegation_parent_id == self.delegation_id:
            raise ValueError("delegation_parent_id must not equal delegation_id (self-referential lineage)")
        return self

    def parsed_time(self) -> datetime:
        """Return the event time as a timezone-aware datetime."""
        v_clean = self.time.replace("Z", "+00:00")
        dt = datetime.fromisoformat(v_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def to_flat_dict(self) -> dict[str, Any]:
        """
        Return a flat dict suitable for DataFrame rows or SQLite storage.
        Nested fields are flattened with dot notation to match paper field names.
        """
        d: dict[str, Any] = {
            "event_id":           self.event_id,
            "time":               self.time,
            "delegation_id":      self.delegation_id,
            "delegation_parent_id": self.delegation_parent_id,
            "principal_user_id":  self.principal.user_id,
            "agent_id":           self.agent.agent_id,
            "agent_instance_id":  self.agent.instance_id,
            "tool_name":          self.tool.name,
            "tool_operation":     self.tool.operation,
            "action_semantic":    self.action.semantic.value,
            "action_outcome":     self.action.outcome.value if self.action.outcome else None,
            "action_direction":   None,
            "action_principal_scope": None,
            "resource_type":      None,
            "resource_id":        None,
            "resource_uri":       None,
            "resource_sensitivity": None,
            "target_principal_id": self.target_principal_id,
            "trace_id":           None,
            "span_id":            None,
            "parent_span_id":     None,
            "workflow_id":        None,
            "labels":             str(self.labels) if self.labels else None,
            "gateway_captured_at": self.gateway_captured_at,
            "gateway_session_id":  self.gateway_session_id,
            "minting_context":     self.minting_context,
        }
        if self.action.tier2:
            d["action_direction"]       = self.action.tier2.direction.value if self.action.tier2.direction else None
            d["action_principal_scope"] = self.action.tier2.principal_scope.value if self.action.tier2.principal_scope else None
        if self.resource:
            d["resource_type"]        = self.resource.type.value if self.resource.type else None
            d["resource_id"]          = self.resource.id
            d["resource_uri"]         = self.resource.uri
            d["resource_sensitivity"] = self.resource.sensitivity_label.value if self.resource.sensitivity_label else None
        if self.trace:
            d["trace_id"]       = self.trace.trace_id
            d["span_id"]        = self.trace.span_id
            d["parent_span_id"] = self.trace.parent_span_id
        if self.workflow:
            d["workflow_id"] = self.workflow.workflow_id
        return d

    @classmethod
    def from_flat_dict(cls, d: dict[str, Any]) -> "CIMEvent":
        """Reconstruct a CIMEvent from a flattened dict (e.g., from SQLite row)."""
        action_tier2 = None
        if d.get("action_direction") or d.get("action_principal_scope"):
            action_tier2 = ActionTier2Field(
                direction=ActionDirection(d["action_direction"]) if d.get("action_direction") else None,
                principal_scope=PrincipalScope(d["action_principal_scope"]) if d.get("action_principal_scope") else None,
            )
        resource = None
        if any(d.get(k) for k in ["resource_type", "resource_id", "resource_uri", "resource_sensitivity"]):
            resource = ResourceField(
                type=ResourceType(d["resource_type"]) if d.get("resource_type") else None,
                id=d.get("resource_id"),
                uri=d.get("resource_uri"),
                sensitivity_label=SensitivityLabel(d["resource_sensitivity"]) if d.get("resource_sensitivity") else None,
            )
        trace = None
        if any(d.get(k) for k in ["trace_id", "span_id", "parent_span_id"]):
            trace = TraceField(
                trace_id=d.get("trace_id"),
                span_id=d.get("span_id"),
                parent_span_id=d.get("parent_span_id"),
            )
        workflow = None
        if d.get("workflow_id"):
            workflow = WorkflowField(workflow_id=d["workflow_id"])

        return cls(
            time=d["time"],
            event_id=d["event_id"],
            delegation_id=d["delegation_id"],
            delegation_parent_id=d.get("delegation_parent_id"),
            principal=PrincipalField(user_id=d["principal_user_id"]),
            agent=AgentField(agent_id=d["agent_id"], instance_id=d.get("agent_instance_id")),
            tool=ToolField(name=d["tool_name"], operation=d.get("tool_operation")),
            action=ActionField(
                semantic=ActionSemantic(d["action_semantic"]),
                outcome=ActionOutcome(d["action_outcome"]) if d.get("action_outcome") else None,
                tier2=action_tier2,
            ),
            resource=resource,
            target_principal_id=d.get("target_principal_id"),
            trace=trace,
            workflow=workflow,
            labels=None,
            gateway_captured_at=d.get("gateway_captured_at"),
            gateway_session_id=d.get("gateway_session_id"),
            minting_context=d.get("minting_context"),
        )


# ---------------------------------------------------------------------------
# Delegation record (authority graph node)
# ---------------------------------------------------------------------------

class DelegationRecord(BaseModel):
    """
    A delegation is a durable authorization context issued by a principal
    to an agent, spanning asynchronous actions and multiple execution subgraphs.
    """
    delegation_id:        str           = Field(..., description="Stable delegation identifier.")
    parent_delegation_id: Optional[str] = Field(None, description="Parent delegation; None for root.")
    principal_user_id:    str           = Field(..., description="Originating human principal.")
    agent_id:             str           = Field(..., description="Executing agent.")
    issued_at:            str           = Field(..., description="Delegation issuance timestamp (RFC3339).")
    closed_at:            Optional[str] = Field(None, description="Delegation closure timestamp; None if open.")
    scope_description:    Optional[str] = Field(None, description="Human-readable description of the delegated scope.")
    authority_envelope:   Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Structured authority envelope: tool_class, action_tier1, "
            "resource_class, sensitivity_label as defined in §5.4."
        ),
    )
    workflow_id:          Optional[str] = Field(None, description="Associated workflow if known at issuance.")
    labels:               Optional[dict[str, str]] = Field(None, description="Free-form labels.")

    @field_validator("delegation_id", "principal_user_id", "agent_id")
    @classmethod
    def non_empty(cls, v: str, info: Any) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v

    @model_validator(mode="after")
    def parent_not_self(self) -> "DelegationRecord":
        if self.parent_delegation_id and self.parent_delegation_id == self.delegation_id:
            raise ValueError("parent_delegation_id must not equal delegation_id")
        return self


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

class ValidationResult(BaseModel):
    """Result of validating a raw event dict against the CIM schema."""
    valid:   bool
    event:   Optional[CIMEvent] = None
    errors:  list[str]          = Field(default_factory=list)
    warnings: list[str]         = Field(default_factory=list)
    raw:     Optional[dict[str, Any]] = None
