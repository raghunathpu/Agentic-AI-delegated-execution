"""
Audit and consistency engine — §2.4 (invariants I1–I5) and §5 of the paper.

Implements:
  I1: Delegation consistency — events sharing a delegation_id must correspond
      to a stable authority context.
      Failure mode: delegation_id reused or repurposed across incompatible contexts.

  I2: Authority-causality separation — delegation_id must not be forced to
      coincide with trace_id; asynchronous fanout and re-delegation are
      misrepresented otherwise.
      Failure mode: delegation_id == trace_id for events.

  I3: Lineage closure under authority propagation — re-delegation and agent
      spawning must preserve attribution through lineage (via inheritance or
      explicit linkage).
      Failure mode: missing parent delegation records.

  I4: Cross-system semantic normalization — stable action/resource vocabulary
      must be preserved across tools.
      Failure mode: action.semantic=UNKNOWN for events that should be normalizable.

  I5 (Evidentiary settings): Binding integrity — delegation_id should be
      bound to authenticated principals and minting context rather than accepted
      as unauthenticated free-form strings.
      Failure mode: minting_context is absent or "unbound".

Additional checks from §5:
  - Authority-causality divergence: Comp(d) > 1 for a delegation (§5.2).
  - Execution drift under fixed authorization: envelope expansion without
      authority expansion event (§5.4).
  - Orphan events (UNBOUND delegation).
  - Incomplete closure evidence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from src.storage.event_store import EventStore
from src.delegation.delegation_graph import DelegationGraph
from src.schemas.cim_event import ActionSemantic


# ---------------------------------------------------------------------------
# Finding data structure
# ---------------------------------------------------------------------------

@dataclass
class AuditFinding:
    """
    A single audit finding with stable ID, rule, severity, evidence, and
    recommended next step.
    """
    finding_id:       str
    rule_id:          str
    rule_name:        str
    severity:         str           # "error", "warning", "info"
    status:           str           # "confirmed", "potential", "unresolved"
    delegation_id:    Optional[str]
    event_ids:        list[str]
    explanation:      str
    evidence:         list[str]     # supporting evidence strings
    next_step:        Optional[str]
    is_confirmed:     bool          # True=confirmed inconsistency; False=potential/missing evidence

    @classmethod
    def make(
        cls,
        rule_id:       str,
        rule_name:     str,
        severity:      str,
        status:        str,
        delegation_id: Optional[str],
        event_ids:     list[str],
        explanation:   str,
        evidence:      list[str],
        next_step:     Optional[str] = None,
        is_confirmed:  bool = False,
    ) -> "AuditFinding":
        return cls(
            finding_id   = str(uuid.uuid4()),
            rule_id      = rule_id,
            rule_name    = rule_name,
            severity     = severity,
            status       = status,
            delegation_id = delegation_id,
            event_ids    = event_ids,
            explanation  = explanation,
            evidence     = evidence,
            next_step    = next_step,
            is_confirmed = is_confirmed,
        )


# ---------------------------------------------------------------------------
# Audit engine
# ---------------------------------------------------------------------------

class AuditEngine:
    """
    Runs audit and consistency checks over stored CIM events and delegation
    records.

    Each check is self-contained and returns a list of AuditFindings.
    The engine collects all findings and makes them available for inspection.
    """

    def __init__(
        self,
        store:            EventStore,
        delegation_graph: DelegationGraph,
    ) -> None:
        self.store            = store
        self.delegation_graph = delegation_graph
        self._findings: list[AuditFinding] = []

    def run_all_checks(self) -> list[AuditFinding]:
        """Run all audit checks and return the collected findings."""
        self._findings.clear()
        self.delegation_graph.build()

        self._check_i1_delegation_consistency()
        self._check_i2_authority_causality_separation()
        self._check_i3_lineage_closure()
        self._check_i4_semantic_normalization()
        self._check_i5_binding_integrity()
        self._check_orphan_events()
        self._check_authority_causality_divergence()
        self._check_execution_drift()
        self._check_delegation_lifecycle()

        return list(self._findings)

    # ------------------------------------------------------------------
    # I1: Delegation consistency
    # ------------------------------------------------------------------

    def _check_i1_delegation_consistency(self) -> None:
        """
        I1: Events sharing a delegation_id must correspond to a stable
        authority context (same principal, agent family).

        Failure mode: delegation_id reused across different principal/agent
        combinations suggests corruption or re-use.
        """
        events = self.store.all_events()
        # Group by delegation_id → collect (principal, agent) pairs
        del_contexts: dict[str, set[tuple[str, str]]] = {}
        del_event_ids: dict[str, list[str]] = {}
        for ev in events:
            d_id = ev.get("delegation_id", "")
            if not d_id or d_id == "UNBOUND":
                continue
            principal = ev.get("principal_user_id", "unknown")
            agent     = ev.get("agent_id", "unknown")
            del_contexts.setdefault(d_id, set()).add((principal, agent))
            del_event_ids.setdefault(d_id, []).append(ev["event_id"])

        for d_id, contexts in del_contexts.items():
            principals = {c[0] for c in contexts}
            if len(principals) > 1:
                self._findings.append(AuditFinding.make(
                    rule_id      = "I1",
                    rule_name    = "Delegation Consistency",
                    severity     = "error",
                    status       = "confirmed",
                    delegation_id = d_id,
                    event_ids    = del_event_ids[d_id],
                    explanation  = (
                        f"Delegation '{d_id}' is shared by multiple principals: "
                        f"{sorted(principals)}. "
                        "A delegation_id must correspond to a single durable authority context. "
                        "This may indicate delegation_id reuse or spoofing."
                    ),
                    evidence     = [
                        f"Principal-agent pairs under this delegation: {sorted(contexts)}"
                    ],
                    next_step    = "Investigate whether delegation_id was reused, spoofed, or incorrectly propagated.",
                    is_confirmed = True,
                ))

    # ------------------------------------------------------------------
    # I2: Authority-causality separation
    # ------------------------------------------------------------------

    def _check_i2_authority_causality_separation(self) -> None:
        """
        I2: delegation_id must not be forced to coincide with trace_id.

        Failure mode: if delegation_id == trace_id, asynchronous fanout and
        re-delegation are misrepresented and divergence predicates become
        undefined.
        """
        events = self.store.all_events()
        violations: list[str] = []
        for ev in events:
            d_id = ev.get("delegation_id", "")
            t_id = ev.get("trace_id")
            if d_id and t_id and d_id == t_id:
                violations.append(ev["event_id"])

        if violations:
            self._findings.append(AuditFinding.make(
                rule_id      = "I2",
                rule_name    = "Authority-Causality Separation",
                severity     = "warning",
                status       = "confirmed",
                delegation_id = None,
                event_ids    = violations,
                explanation  = (
                    f"{len(violations)} event(s) have delegation_id == trace_id. "
                    "Authority structure (delegation) and causal structure (traces) "
                    "are independent; forcing them to coincide misrepresents "
                    "asynchronous fanout and re-delegation."
                ),
                evidence     = [f"Affected event_ids: {violations[:10]}{'...' if len(violations) > 10 else ''}"],
                next_step    = "Review delegation_id minting to ensure it is independent of trace identifiers.",
                is_confirmed = True,
            ))

    # ------------------------------------------------------------------
    # I3: Lineage closure under authority propagation
    # ------------------------------------------------------------------

    def _check_i3_lineage_closure(self) -> None:
        """
        I3: Re-delegation and agent spawning must preserve attribution through
        lineage (via inheritance or explicit linkage).

        Checks for missing parent delegation records.
        """
        events = self.store.all_events()
        known_delegations = {n for n in self.delegation_graph.all_nodes()}

        for ev in events:
            parent = ev.get("delegation_parent_id")
            if parent and parent not in known_delegations:
                self._findings.append(AuditFinding.make(
                    rule_id      = "I3",
                    rule_name    = "Lineage Closure",
                    severity     = "warning",
                    status       = "potential",
                    delegation_id = ev.get("delegation_id"),
                    event_ids    = [ev["event_id"]],
                    explanation  = (
                        f"Event {ev['event_id']!r} references parent delegation "
                        f"'{parent}' which has no corresponding delegation record. "
                        "Lineage closure (I3) cannot be verified for this delegation chain. "
                        "This may be due to missing telemetry, out-of-band delegation, "
                        "or lineage fragmentation."
                    ),
                    evidence     = [
                        f"delegation_parent_id={parent!r} not in delegation store",
                        f"delegation_id={ev.get('delegation_id')!r}",
                    ],
                    next_step    = "Retrieve the delegation issuance event for the parent delegation.",
                    is_confirmed = False,
                ))

    # ------------------------------------------------------------------
    # I4: Cross-system semantic normalization
    # ------------------------------------------------------------------

    def _check_i4_semantic_normalization(self) -> None:
        """
        I4: A stable action/resource vocabulary must be preserved across tools.

        Failure mode: action.semantic=UNKNOWN indicates a normalization gap.
        """
        events = self.store.all_events()
        unknown_events = [
            ev for ev in events
            if ev.get("action_semantic") == ActionSemantic.UNKNOWN.value
        ]
        if unknown_events:
            by_tool: dict[str, list[str]] = {}
            for ev in unknown_events:
                tool = ev.get("tool_name", "unknown")
                by_tool.setdefault(tool, []).append(ev["event_id"])

            self._findings.append(AuditFinding.make(
                rule_id      = "I4",
                rule_name    = "Cross-System Semantic Normalization",
                severity     = "warning",
                status       = "confirmed",
                delegation_id = None,
                event_ids    = [ev["event_id"] for ev in unknown_events],
                explanation  = (
                    f"{len(unknown_events)} event(s) have action.semantic=unknown. "
                    "Without normalization, delegation-scoped queries are not portable "
                    "across tool boundaries (I4)."
                ),
                evidence     = [
                    f"Tool '{t}': {len(ids)} unknown-semantic events" 
                    for t, ids in by_tool.items()
                ],
                next_step    = "Extend the normalization taxonomy for the affected tool/operation combinations.",
                is_confirmed = True,
            ))

    # ------------------------------------------------------------------
    # I5: Binding integrity
    # ------------------------------------------------------------------

    def _check_i5_binding_integrity(self) -> None:
        """
        I5 (Evidentiary settings): delegation_id should be bound to
        authenticated principals and minting context.

        Failure mode: minting_context is absent or 'unbound' — compromised
        components can mis-attribute actions.
        """
        events = self.store.all_events()
        suspect: list[str] = []
        for ev in events:
            ctx = ev.get("minting_context", "")
            d_id = ev.get("delegation_id", "")
            if d_id == "UNBOUND" or ctx in ("unbound", None, ""):
                suspect.append(ev["event_id"])

        if suspect:
            self._findings.append(AuditFinding.make(
                rule_id      = "I5",
                rule_name    = "Binding Integrity",
                severity     = "warning",
                status       = "potential",
                delegation_id = None,
                event_ids    = suspect,
                explanation  = (
                    f"{len(suspect)} event(s) have no gateway-bound minting context "
                    "or are marked UNBOUND. These events lack the evidentiary binding "
                    "required for reliable delegation-scoped attribution (I5). "
                    "This may indicate events were emitted outside the gateway boundary "
                    "(out-of-band channels) or that delegation context was unavailable."
                ),
                evidence     = [
                    f"Affected event_ids: {suspect[:10]}{'...' if len(suspect) > 10 else ''}",
                ],
                next_step    = "Verify that all tool invocations pass through the instrumented gateway boundary.",
                is_confirmed = False,
            ))

    # ------------------------------------------------------------------
    # Orphan events
    # ------------------------------------------------------------------

    def _check_orphan_events(self) -> None:
        """
        Detect events with delegation_id=UNBOUND or empty — orphan events
        that cannot be attributed to any delegation.
        """
        events = self.store.all_events()
        orphans = [
            ev for ev in events
            if not ev.get("delegation_id") or ev.get("delegation_id") == "UNBOUND"
        ]
        if orphans:
            self._findings.append(AuditFinding.make(
                rule_id      = "ORPHAN",
                rule_name    = "Orphan Events",
                severity     = "warning",
                status       = "confirmed",
                delegation_id = None,
                event_ids    = [ev["event_id"] for ev in orphans],
                explanation  = (
                    f"{len(orphans)} event(s) have no valid delegation_id (UNBOUND or empty). "
                    "These events cannot be attributed to any delegated authority context. "
                    "Delegation-scoped queries will not cover these events."
                ),
                evidence     = [
                    f"Orphan event_id: {ev['event_id']!r}, "
                    f"tool={ev.get('tool_name')!r}, "
                    f"action={ev.get('action_semantic')!r}"
                    for ev in orphans[:5]
                ],
                next_step    = "Determine why delegation context was not available at capture time.",
                is_confirmed = True,
            ))

    # ------------------------------------------------------------------
    # Authority-causality divergence (§5.2)
    # ------------------------------------------------------------------

    def _check_authority_causality_divergence(self) -> None:
        """
        §5.2: Authority-causality divergence.
        Comp(d) counts the causal connected components spanned by delegation d.
        High Comp(d) indicates delegation-causality mismatch.
        """
        for d_id, node in self.delegation_graph.all_nodes().items():
            if d_id == "UNBOUND":
                continue
            comp = self.delegation_graph.compute_authority_causality_divergence(d_id)
            if comp > 1:
                event_ids = self.delegation_graph.events_under_delegation(d_id)
                self._findings.append(AuditFinding.make(
                    rule_id      = "ACD",
                    rule_name    = "Authority-Causality Divergence",
                    severity     = "info",
                    status       = "confirmed",
                    delegation_id = d_id,
                    event_ids    = event_ids[:20],
                    explanation  = (
                        f"Delegation '{d_id}' spans {comp} causal connected components "
                        f"(Comp(d) = {comp}). "
                        "This is expected under parallel sub-tasks, retries, and multi-agent "
                        "fanout — CIM makes this structural property checkable. "
                        "High Comp(d) indicates regions where trace-only or session-based "
                        "reconstruction would be structurally mis-specified."
                    ),
                    evidence     = [
                        f"delegation_id={d_id!r}",
                        f"Comp(d)={comp}",
                        f"Event count under delegation (incl. descendants): {len(event_ids)}",
                    ],
                    next_step    = (
                        "Review whether the multiple causal components reflect legitimate "
                        "parallel sub-tasks or indicate an anomalous execution pattern."
                    ),
                    is_confirmed = True,
                ))

    # ------------------------------------------------------------------
    # Execution drift (§5.4)
    # ------------------------------------------------------------------

    def _check_execution_drift(self) -> None:
        """
        §5.4: Execution drift under fixed authorization.

        Drift occurs if the observed envelope of actions/resources expands
        along a monitored dimension (new resource_class, first write/share,
        or increased sensitivity) without an accompanying authority expansion
        event in the lineage.

        This is a metadata-only check — no payload inspection.
        """
        delegations = self.delegation_graph.all_nodes()
        for d_id, node in delegations.items():
            if d_id == "UNBOUND" or not node.authority_envelope:
                continue

            env = node.authority_envelope
            permitted_actions  = set(env.get("permitted_action_tier1", []))
            permitted_resources = set(env.get("permitted_resource_class", []))
            permitted_sensitivity = env.get("max_sensitivity")

            events = self.store.query_events(delegation_id=d_id)
            drift_events: list[str] = []
            evidence: list[str] = []

            for ev in events:
                action   = ev.get("action_semantic", "")
                res_type = ev.get("resource_type", "")
                res_sens = ev.get("resource_sensitivity", "")

                if permitted_actions and action and action not in permitted_actions:
                    drift_events.append(ev["event_id"])
                    evidence.append(
                        f"event={ev['event_id']!r}: action={action!r} not in permitted={permitted_actions}"
                    )
                if permitted_resources and res_type and res_type not in permitted_resources:
                    drift_events.append(ev["event_id"])
                    evidence.append(
                        f"event={ev['event_id']!r}: resource_type={res_type!r} not in permitted={permitted_resources}"
                    )

            if drift_events:
                # Deduplicate
                drift_events = list(dict.fromkeys(drift_events))
                self._findings.append(AuditFinding.make(
                    rule_id      = "DRIFT",
                    rule_name    = "Execution Drift Under Fixed Authorization",
                    severity     = "warning",
                    status       = "potential",
                    delegation_id = d_id,
                    event_ids    = drift_events,
                    explanation  = (
                        f"Delegation '{d_id}' observed actions/resources outside its "
                        "recorded authority envelope. This may indicate scope expansion "
                        "without a recorded authority expansion event, or it may reflect "
                        "legitimate scope evolution not captured in the envelope. "
                        "CIM enables metadata-only drift detection without payload inspection."
                    ),
                    evidence     = evidence[:10],
                    next_step    = (
                        "Verify whether an authority expansion/approval event exists in "
                        "the delegation lineage. If not, investigate the scope expansion."
                    ),
                    is_confirmed = False,  # potential — missing evidence doesn't prove violation
                ))

    # ------------------------------------------------------------------
    # Delegation lifecycle / closure
    # ------------------------------------------------------------------

    def _check_delegation_lifecycle(self) -> None:
        """
        Check that delegations have closure events recorded.
        A delegation without a closed_at timestamp may indicate missing
        closure evidence or an ongoing delegation.
        """
        delegations = self.store.all_delegations()
        for d in delegations:
            if not d.get("closed_at"):
                event_ids = self.delegation_graph.events_under_delegation(
                    d["delegation_id"]
                )
                # Only flag leaf delegations with no children and no closure
                children = self.delegation_graph.all_nodes().get(d["delegation_id"])
                has_children = bool(
                    self.delegation_graph.descendants(d["delegation_id"])
                )
                if not has_children:
                    self._findings.append(AuditFinding.make(
                        rule_id      = "CLOSURE",
                        rule_name    = "Delegation Lifecycle Completeness",
                        severity     = "info",
                        status       = "potential",
                        delegation_id = d["delegation_id"],
                        event_ids    = event_ids[:10],
                        explanation  = (
                            f"Delegation '{d['delegation_id']}' has no recorded closure event. "
                            "Missing closure may indicate the delegation is still active, "
                            "or that the closure event was not captured by the gateway. "
                            "Absence of closure evidence does not confirm a violation."
                        ),
                        evidence     = [
                            f"issued_at={d.get('issued_at')!r}",
                            f"closed_at=None",
                            f"Event count under delegation: {len(event_ids)}",
                        ],
                        next_step    = "Verify whether this delegation was explicitly closed by the delegating principal.",
                        is_confirmed = False,
                    ))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_findings(self) -> list[AuditFinding]:
        return list(self._findings)

    def findings_for_delegation(self, delegation_id: str) -> list[AuditFinding]:
        return [f for f in self._findings if f.delegation_id == delegation_id]

    def findings_for_event(self, event_id: str) -> list[AuditFinding]:
        return [f for f in self._findings if event_id in f.event_ids]

    def summary(self) -> dict[str, int]:
        from collections import Counter
        sev = Counter(f.severity for f in self._findings)
        return {
            "total":    len(self._findings),
            "errors":   sev.get("error", 0),
            "warnings": sev.get("warning", 0),
            "info":     sev.get("info", 0),
            "confirmed": sum(1 for f in self._findings if f.is_confirmed),
            "potential": sum(1 for f in self._findings if not f.is_confirmed),
        }
