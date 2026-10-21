"""
Delegation lineage and authority graph — §4 and §5.3 of the paper.

The authority graph G_auth is a directed acyclic graph (DAG) where:
- Nodes represent delegations (D).
- Edges represent lineage relations (R_lineage).
- An edge (d, d') ∈ R_lineage denotes that d' was spawned/re-delegated from d.

The transitive closure yields Ancestors(d) and Descendants(d), enabling
lineage-scoped queries that are independent of trace connectivity.

Key invariants (from §2.4):
  I1: Delegation consistency — events sharing a delegation_id must correspond
      to a stable authority context.
  I3: Lineage closure under authority propagation — re-delegation and agent
      spawning must preserve attribution through lineage (via inheritance or
      explicit linkage).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from src.storage.event_store import EventStore


@dataclass
class DelegationNode:
    """A node in the authority graph G_auth."""
    delegation_id:        str
    parent_delegation_id: Optional[str]
    principal_user_id:    str
    agent_id:             str
    issued_at:            str
    closed_at:            Optional[str]
    scope_description:    Optional[str]
    authority_envelope:   Optional[dict]
    workflow_id:          Optional[str]
    event_count:          int = 0


@dataclass
class LineageAnalysis:
    """Result of analyzing a delegation's lineage."""
    delegation_id: str
    ancestors:     list[str]    = field(default_factory=list)
    descendants:   list[str]    = field(default_factory=list)
    lineage_depth: int          = 0
    lineage_gaps:  list[str]    = field(default_factory=list)
    is_root:       bool         = True
    has_closure:   bool         = False
    issues:        list[str]    = field(default_factory=list)


class DelegationGraph:
    """
    Constructs and queries the authority graph G_auth from stored records.

    Provides:
    - Ancestors(d) and Descendants(d) via transitive closure.
    - Delegation closure detection.
    - Lineage gap detection (I3: missing parent records).
    - Authority-causality divergence computation (§5.2).
    """

    def __init__(self, store: EventStore) -> None:
        self.store = store
        self._nodes: dict[str, DelegationNode] = {}
        self._parent_map: dict[str, str] = {}   # child → parent
        self._children_map: dict[str, list[str]] = {}  # parent → [children]
        self._delegation_events: dict[str, list[str]] = {}  # delegation_id → [event_ids]

    def build(self) -> None:
        """Build the authority graph from persistent storage."""
        self._nodes.clear()
        self._parent_map.clear()
        self._children_map.clear()
        self._delegation_events.clear()

        # Load delegation records
        delegations = self.store.all_delegations()
        for d in delegations:
            import json
            env = None
            if d.get("authority_envelope"):
                try:
                    env = json.loads(d["authority_envelope"])
                except Exception:
                    env = None
            node = DelegationNode(
                delegation_id        = d["delegation_id"],
                parent_delegation_id = d.get("parent_delegation_id"),
                principal_user_id    = d["principal_user_id"],
                agent_id             = d["agent_id"],
                issued_at            = d["issued_at"],
                closed_at            = d.get("closed_at"),
                scope_description    = d.get("scope_description"),
                authority_envelope   = env,
                workflow_id          = d.get("workflow_id"),
            )
            self._nodes[d["delegation_id"]] = node
            if d.get("parent_delegation_id"):
                self._parent_map[d["delegation_id"]] = d["parent_delegation_id"]
                self._children_map.setdefault(d["parent_delegation_id"], []).append(d["delegation_id"])

        # Synthesize delegation nodes from events for delegations without explicit records
        events = self.store.all_events()
        for ev in events:
            d_id = ev["delegation_id"]
            self._delegation_events.setdefault(d_id, []).append(ev["event_id"])

            if d_id not in self._nodes:
                # Synthesize a minimal node from event evidence
                node = DelegationNode(
                    delegation_id        = d_id,
                    parent_delegation_id = ev.get("delegation_parent_id"),
                    principal_user_id    = ev.get("principal_user_id", "unknown"),
                    agent_id             = ev.get("agent_id", "unknown"),
                    issued_at            = ev.get("time", ""),
                    closed_at            = None,
                    scope_description    = None,
                    authority_envelope   = None,
                    workflow_id          = ev.get("workflow_id"),
                )
                self._nodes[d_id] = node

            if ev.get("delegation_parent_id"):
                parent = ev["delegation_parent_id"]
                if d_id not in self._parent_map:
                    self._parent_map[d_id] = parent
                    self._children_map.setdefault(parent, []).append(d_id)

        # Count events per delegation
        for d_id, node in self._nodes.items():
            node.event_count = len(self._delegation_events.get(d_id, []))

    def ancestors(self, delegation_id: str) -> list[str]:
        """Return all ancestors of delegation_id via transitive closure."""
        result: list[str] = []
        current = delegation_id
        visited: set[str] = set()
        while True:
            parent = self._parent_map.get(current)
            if not parent or parent in visited:
                break
            visited.add(parent)
            result.append(parent)
            current = parent
        return result

    def descendants(self, delegation_id: str) -> list[str]:
        """Return all descendants of delegation_id via transitive closure."""
        result: list[str] = []
        queue  = list(self._children_map.get(delegation_id, []))
        visited: set[str] = set()
        while queue:
            child = queue.pop(0)
            if child in visited:
                continue
            visited.add(child)
            result.append(child)
            queue.extend(self._children_map.get(child, []))
        return result

    def all_under_delegation(self, delegation_id: str) -> list[str]:
        """
        Return delegation_id + all descendants.
        Used for delegation closure queries.
        """
        return [delegation_id] + self.descendants(delegation_id)

    def events_under_delegation(self, delegation_id: str) -> list[str]:
        """
        Return all event_ids attributed to delegation_id and its descendants.
        This is the delegation-scoped partition over events.
        """
        all_dels = self.all_under_delegation(delegation_id)
        event_ids: list[str] = []
        for d in all_dels:
            event_ids.extend(self._delegation_events.get(d, []))
        return event_ids

    def analyze_lineage(self, delegation_id: str) -> LineageAnalysis:
        """
        Analyze the lineage of a delegation.
        Detects gaps (missing parent records), closure, and depth.
        """
        node = self._nodes.get(delegation_id)
        if not node:
            return LineageAnalysis(
                delegation_id=delegation_id,
                issues=[f"Delegation '{delegation_id}' not found in authority graph."],
            )

        ancestors    = self.ancestors(delegation_id)
        descendants  = self.descendants(delegation_id)
        gaps: list[str] = []

        # Check for lineage gaps (I3)
        current = delegation_id
        for anc in ancestors:
            if anc not in self._nodes:
                gaps.append(
                    f"Missing delegation record for '{anc}' referenced as parent of '{current}'. "
                    "Lineage closure (I3) cannot be verified for this path."
                )
            current = anc

        is_root    = not bool(ancestors)
        has_closure = node.closed_at is not None

        issues: list[str] = []
        if gaps:
            issues.extend(gaps)
        if not has_closure and not is_root and not descendants:
            issues.append(
                f"Delegation '{delegation_id}' is a leaf delegation with no closure event recorded. "
                "Lifecycle completeness cannot be confirmed."
            )

        return LineageAnalysis(
            delegation_id = delegation_id,
            ancestors     = ancestors,
            descendants   = descendants,
            lineage_depth = len(ancestors),
            lineage_gaps  = gaps,
            is_root       = is_root,
            has_closure   = has_closure,
            issues        = issues,
        )

    def root_delegations(self) -> list[str]:
        """Return delegation IDs that have no parent (root nodes of G_auth)."""
        return [d for d in self._nodes if d not in self._parent_map]

    def compute_authority_causality_divergence(
        self,
        delegation_id: str,
        causal_components: Optional[list[list[str]]] = None,
    ) -> int:
        """
        Compute Comp(d) — the number of causal connected components spanned
        by the events under delegation d.

        From §5.2: We say d exhibits authority-causality divergence if it
        contains events with multiple delegation identifiers.
        Formally: |Comp(d)| > 1 or multiple delegation IDs per causal component.

        If causal_components is provided, it is a list of sets of event_ids
        that form causal connected components (from trace structure).
        Otherwise we treat each event as its own component (pessimistic bound).
        """
        events_under = self.events_under_delegation(delegation_id)
        if not events_under:
            return 0
        if causal_components is None:
            # Without trace structure, count trace groups from events
            rows = self.store.query_events(delegation_id=delegation_id)
            trace_groups: set[str] = set()
            no_trace_events = 0
            for row in rows:
                if row.get("trace_id"):
                    trace_groups.add(row["trace_id"])
                else:
                    no_trace_events += 1
            return len(trace_groups) + (1 if no_trace_events else 0)
        return len(causal_components)

    def get_node(self, delegation_id: str) -> Optional[DelegationNode]:
        return self._nodes.get(delegation_id)

    def all_nodes(self) -> dict[str, DelegationNode]:
        return dict(self._nodes)
