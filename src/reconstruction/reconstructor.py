"""
Delegation-aware execution reconstruction engine — §4 and §7.2 of the paper.

Reconstructs:
  - The authority graph G_auth (delegation lineage DAG).
  - The execution graph G_exec (causal control-flow over events).
  - The authorization relation R_auth (mapping events to delegations).

Key properties:
  - Does NOT assume temporal proximity implies causality (§1).
  - Does NOT force a total order on concurrent events.
  - Distinguishes observed causal links (trace.parent_span_id) from
    inferred or unresolved relationships.
  - Identifies orphan events (no delegation record).
  - Identifies missing delegation context.
  - Exposes unresolved correlations.
  - Makes reconstruction deterministic for a given dataset.

Baselines implemented (§7.2):
  B0 (trace-only): group by trace_id; fall back to session/task or singletons.
  B1 (time-window): sliding time window Δ; group by time proximity.
  B2 (best-effort hybrid): trace/session grouping merged by time window +
      {user, agent instance, tool}; approximates current SIEM practice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

from src.storage.event_store import EventStore
from src.delegation.delegation_graph import DelegationGraph


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ReconstructedWorkflow:
    """
    A reconstructed workflow: a set of events grouped under a delegation
    with their causal and lineage relationships.
    """
    workflow_id:       str
    delegation_ids:    list[str]           = field(default_factory=list)
    event_ids:         list[str]           = field(default_factory=list)
    causal_edges:      list[tuple[str,str]] = field(default_factory=list)  # (parent_event_id, child_event_id)
    orphan_event_ids:  list[str]           = field(default_factory=list)
    unresolved_event_ids: list[str]        = field(default_factory=list)
    method:            str = "cim"         # "cim", "b0_trace", "b1_time_window", "b2_hybrid"
    ambiguity:         int = 1             # Amb(E): number of distinct delegation groupings consistent with telemetry
    notes:             list[str]           = field(default_factory=list)


@dataclass
class ReconstructionResult:
    """Full reconstruction result for a dataset."""
    workflows:              list[ReconstructedWorkflow]  = field(default_factory=list)
    total_events:           int = 0
    covered_event_ids:      set[str]  = field(default_factory=set)
    uncovered_event_ids:    set[str]  = field(default_factory=set)
    method:                 str = "cim"
    notes:                  list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CIM-aware reconstruction (primary method)
# ---------------------------------------------------------------------------

class CIMReconstructor:
    """
    Delegation-aware reconstruction using CIM events and delegation records.

    Reconstruction is based on explicit delegation_id bindings captured at
    the gateway boundary — NOT on heuristic time-window correlation.

    From §2.3: R_auth is directly encoded in CIM events via delegation_id.
    The delegation partition over events is therefore well-defined.
    """

    def __init__(self, store: EventStore, delegation_graph: DelegationGraph) -> None:
        self.store            = store
        self.delegation_graph = delegation_graph

    def reconstruct(self) -> ReconstructionResult:
        """Reconstruct all workflows from stored CIM events."""
        self.delegation_graph.build()
        events = self.store.all_events()

        if not events:
            return ReconstructionResult(method="cim", notes=["No events in store."])

        result = ReconstructionResult(method="cim", total_events=len(events))

        # Group events by workflow_id (if available), else by root delegation
        workflow_groups = self._group_by_workflow(events)

        for wf_id, wf_events in workflow_groups.items():
            wf = self._reconstruct_workflow(wf_id, wf_events)
            result.workflows.append(wf)
            result.covered_event_ids.update(
                e["event_id"] for e in wf_events
                if e["event_id"] not in wf.orphan_event_ids
            )

        all_ids = {e["event_id"] for e in events}
        result.uncovered_event_ids = all_ids - result.covered_event_ids

        return result

    def _group_by_workflow(
        self,
        events: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Group events by workflow.

        Priority:
        1. workflow.workflow_id if present.
        2. Root delegation_id (via delegation lineage).
        3. delegation_id directly (for events without delegation records).
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        for ev in events:
            wf_id = ev.get("workflow_id")
            if not wf_id:
                # Resolve to root delegation via lineage
                d_id = ev.get("delegation_id", "")
                if d_id and d_id != "UNBOUND":
                    ancs = self.delegation_graph.ancestors(d_id)
                    wf_id = ancs[-1] if ancs else d_id
                else:
                    wf_id = d_id or "unbound_workflow"
            groups.setdefault(wf_id, []).append(ev)
        return groups

    def _reconstruct_workflow(
        self,
        wf_id: str,
        events: list[dict[str, Any]],
    ) -> ReconstructedWorkflow:
        """Reconstruct a single workflow from its events."""
        wf = ReconstructedWorkflow(
            workflow_id=wf_id,
            method="cim",
        )

        # Identify delegation IDs in this workflow
        delegation_ids = list({e["delegation_id"] for e in events})
        wf.delegation_ids = delegation_ids
        wf.event_ids      = [e["event_id"] for e in events]

        # Build causal edges from trace structure (observed causal links)
        span_to_event: dict[str, str] = {}
        for ev in events:
            if ev.get("span_id"):
                span_to_event[ev["span_id"]] = ev["event_id"]

        causal_edges: list[tuple[str, str]] = []
        for ev in events:
            if ev.get("parent_span_id") and ev.get("event_id"):
                parent_event = span_to_event.get(ev["parent_span_id"])
                if parent_event:
                    causal_edges.append((parent_event, ev["event_id"]))
                else:
                    # Parent span not in this dataset — unresolved
                    wf.unresolved_event_ids.append(ev["event_id"])
                    wf.notes.append(
                        f"Event {ev['event_id']!r}: parent_span_id={ev['parent_span_id']!r} "
                        "not found in dataset. Causal ancestor is unresolved."
                    )
        wf.causal_edges = causal_edges

        # Detect orphan events (UNBOUND delegation or no delegation record)
        for ev in events:
            d_id = ev.get("delegation_id", "")
            if d_id == "UNBOUND" or d_id == "":
                wf.orphan_event_ids.append(ev["event_id"])

        # CIM ambiguity: 1 for all covered events (delegation bound at gateway)
        unbound_count = len(wf.orphan_event_ids)
        wf.ambiguity = 1 if unbound_count == 0 else 1 + unbound_count

        return wf


# ---------------------------------------------------------------------------
# Baseline B0: Trace-only reconstruction
# ---------------------------------------------------------------------------

class BaselineB0TraceOnly:
    """
    Baseline B0 (trace-only) — §7.2.2.

    Group by trace_id; fall back to session/task id or singletons.
    Without delegation-aware identifiers, operating on the same observed
    telemetry.

    Failure mode (§4): B0 fails delegation closure and cross-graph span
    under multi-agent fanout and retries, yielding fragmented sessions for
    a single delegation and no lineage semantics.
    """

    def __init__(self, store: EventStore) -> None:
        self.store = store

    def reconstruct(self) -> ReconstructionResult:
        events = self.store.all_events()
        if not events:
            return ReconstructionResult(method="b0_trace", notes=["No events in store."])

        result = ReconstructionResult(method="b0_trace", total_events=len(events))

        # Group by trace_id; fall back to event_id (singleton)
        groups: dict[str, list[dict]] = {}
        for ev in events:
            key = ev.get("trace_id") or ev.get("event_id", str(uuid.uuid4()))
            groups.setdefault(key, []).append(ev)

        for group_id, gevents in groups.items():
            # Build causal edges from span structure
            span_to_event: dict[str, str] = {}
            for ev in gevents:
                if ev.get("span_id"):
                    span_to_event[ev["span_id"]] = ev["event_id"]
            causal_edges = []
            unresolved = []
            for ev in gevents:
                if ev.get("parent_span_id"):
                    parent = span_to_event.get(ev["parent_span_id"])
                    if parent:
                        causal_edges.append((parent, ev["event_id"]))
                    else:
                        unresolved.append(ev["event_id"])

            # B0 ambiguity: multiple delegation groupings consistent with trace
            unique_delegations = {e.get("delegation_id", "") for e in gevents}
            # If trace aligns with delegation: low ambiguity; else high
            ambiguity = len(unique_delegations) if len(unique_delegations) > 1 else 1

            wf = ReconstructedWorkflow(
                workflow_id    = group_id,
                delegation_ids = list(unique_delegations),
                event_ids      = [e["event_id"] for e in gevents],
                causal_edges   = causal_edges,
                unresolved_event_ids = unresolved,
                method         = "b0_trace",
                ambiguity      = ambiguity,
                notes          = [
                    "B0: grouped by trace_id. Cannot distinguish delegation boundaries "
                    "from causal boundaries. Delegation closure is not computable."
                ],
            )
            result.workflows.append(wf)
            result.covered_event_ids.update(e["event_id"] for e in gevents)

        all_ids = {e["event_id"] for e in events}
        result.uncovered_event_ids = all_ids - result.covered_event_ids
        return result


# ---------------------------------------------------------------------------
# Baseline B1: Time-window reconstruction
# ---------------------------------------------------------------------------

class BaselineB1TimeWindow:
    """
    Baseline B1 (time-window) — §7.2.2.

    Sliding time window Δ; best-performing Δ reported per workflow.
    Failure mode: multiple delegations may share a time window; concurrent
    activity is misattributed.
    """

    def __init__(self, store: EventStore, window_seconds: float = 30.0) -> None:
        self.store          = store
        self.window_seconds = window_seconds

    def reconstruct(self) -> ReconstructionResult:
        events = self.store.all_events()
        if not events:
            return ReconstructionResult(method="b1_time_window", notes=["No events in store."])

        result = ReconstructionResult(method="b1_time_window", total_events=len(events))

        # Sort by time
        def parse_ts(ev: dict) -> datetime:
            try:
                ts = ev.get("time", "")
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

        sorted_events = sorted(events, key=parse_ts)
        window = timedelta(seconds=self.window_seconds)

        # Greedy windowing
        groups: list[list[dict]] = []
        current_group: list[dict] = []
        window_start: Optional[datetime] = None

        for ev in sorted_events:
            ts = parse_ts(ev)
            if window_start is None or (ts - window_start) > window:
                if current_group:
                    groups.append(current_group)
                current_group = [ev]
                window_start  = ts
            else:
                current_group.append(ev)
        if current_group:
            groups.append(current_group)

        for i, gevents in enumerate(groups):
            unique_delegations = {e.get("delegation_id", "") for e in gevents}
            ambiguity = len(unique_delegations) if len(unique_delegations) > 1 else 1

            wf = ReconstructedWorkflow(
                workflow_id    = f"time_window_{i}",
                delegation_ids = list(unique_delegations),
                event_ids      = [e["event_id"] for e in gevents],
                method         = "b1_time_window",
                ambiguity      = ambiguity,
                notes          = [
                    f"B1: time-window Δ={self.window_seconds}s. "
                    "Groups may contain events from multiple delegations. "
                    "No delegation lineage semantics."
                ],
            )
            result.workflows.append(wf)
            result.covered_event_ids.update(e["event_id"] for e in gevents)

        all_ids = {e["event_id"] for e in events}
        result.uncovered_event_ids = all_ids - result.covered_event_ids
        return result


# ---------------------------------------------------------------------------
# Baseline B2: Best-effort hybrid
# ---------------------------------------------------------------------------

class BaselineB2Hybrid:
    """
    Baseline B2 (best-effort hybrid) — §7.2.2.

    Trace/session grouping merged by time window + {user, agent instance, tool}.
    Approximates current SIEM practice — the strongest baseline.

    Failure mode: even B2 fails when trace boundaries diverge from delegation
    boundaries under re-delegation or multi-agent fanout.
    """

    def __init__(
        self,
        store:          EventStore,
        window_seconds: float = 30.0,
    ) -> None:
        self.store          = store
        self.window_seconds = window_seconds

    def reconstruct(self) -> ReconstructionResult:
        events = self.store.all_events()
        if not events:
            return ReconstructionResult(method="b2_hybrid", notes=["No events in store."])

        result = ReconstructionResult(method="b2_hybrid", total_events=len(events))

        def parse_ts(ev: dict) -> datetime:
            try:
                ts = ev.get("time", "")
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

        # Group by (trace_id or session_key) + {user, agent}
        def session_key(ev: dict) -> str:
            trace = ev.get("trace_id")
            if trace:
                return f"trace:{trace}"
            return f"user:{ev.get('principal_user_id','?')}|agent:{ev.get('agent_id','?')}"

        # First pass: group by session key
        session_groups: dict[str, list[dict]] = {}
        for ev in events:
            key = session_key(ev)
            session_groups.setdefault(key, []).append(ev)

        # Second pass: merge adjacent sessions within time window
        sorted_sessions = sorted(
            session_groups.items(),
            key=lambda kv: parse_ts(kv[1][0]) if kv[1] else datetime.min.replace(tzinfo=timezone.utc),
        )

        merged_groups: list[list[dict]] = []
        current: list[dict] = []
        current_end: Optional[datetime] = None
        window = timedelta(seconds=self.window_seconds)

        for _, sess_events in sorted_sessions:
            if not sess_events:
                continue
            sorted_sess = sorted(sess_events, key=parse_ts)
            sess_start = parse_ts(sorted_sess[0])
            if current_end is None or (sess_start - current_end) > window:
                if current:
                    merged_groups.append(current)
                current     = list(sorted_sess)
                current_end = parse_ts(sorted_sess[-1])
            else:
                current.extend(sorted_sess)
                current_end = max(current_end, parse_ts(sorted_sess[-1]))
        if current:
            merged_groups.append(current)

        for i, gevents in enumerate(merged_groups):
            unique_delegations = {e.get("delegation_id", "") for e in gevents}
            ambiguity = len(unique_delegations) if len(unique_delegations) > 1 else 1

            wf = ReconstructedWorkflow(
                workflow_id    = f"b2_group_{i}",
                delegation_ids = list(unique_delegations),
                event_ids      = [e["event_id"] for e in gevents],
                method         = "b2_hybrid",
                ambiguity      = ambiguity,
                notes          = [
                    f"B2: hybrid trace+session+time-window Δ={self.window_seconds}s. "
                    "Strongest baseline; still fails under re-delegation fanout."
                ],
            )
            result.workflows.append(wf)
            result.covered_event_ids.update(e["event_id"] for e in gevents)

        all_ids = {e["event_id"] for e in events}
        result.uncovered_event_ids = all_ids - result.covered_event_ids
        return result
