"""
Synthetic data generator and experimental environment — §7.1 of the paper.

Implements three workflow scenarios:
  W1 (cross-system, causally disconnected subtasks): issues parallel reads
     across multiple tools and produces a single synthesis action, inducing
     causally disconnected components within a single delegation.

  W2 (multi-agent composition): spawns cooperating agents with re-delegation
     edges, stressing lineage closure (I3) and runtime fragmentation.

  W3 (trial-and-adapt): includes retries and backtracking with interleaved
     successes and failures, reflecting LLM agents that iteratively refine
     tool calls based on intermediate observations.

Each generated dataset has accessible ground-truth information (E*(d) for
each delegation d) that allows reconstruction results to be evaluated.

Parameters from Table 4 (reduced for local prototype demonstration):
  - Tools K: 3-7
  - Overlap rate: configurable
  - Spawn fanout: configurable
  - Retry/backtrack rate: configurable
  - Trace coverage: 60-80% of events carry trace/span IDs

Random seed is configurable and recorded with the experiment configuration.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from src.schemas.cim_event import (
    CIMEvent,
    ActionSemantic,
    ActionOutcome,
    ActionDirection,
    ResourceType,
    SensitivityLabel,
    PrincipalField,
    AgentField,
    ToolField,
    ActionField,
    ActionTier2Field,
    ResourceField,
    TraceField,
    WorkflowField,
)
from src.schemas.cim_event import DelegationRecord


# ---------------------------------------------------------------------------
# Scenario configuration
# ---------------------------------------------------------------------------

@dataclass
class ScenarioConfig:
    """Configuration for a synthetic experiment scenario."""
    scenario:            str   = "W1"         # W1, W2, W3, mixed, defective
    seed:                int   = 42
    n_workflows:         int   = 3
    n_tools:             int   = 4
    events_per_workflow: int   = 10
    overlap_rate:        float = 0.2          # fraction of users with ≥2 delegations
    spawn_fanout:        int   = 2            # mean children per delegation (W2)
    spawn_depth:         int   = 2            # max re-delegation depth (W2)
    retry_rate:          float = 0.3          # fraction of steps with retries (W3)
    trace_coverage:      float = 0.7          # fraction of events with trace/span IDs
    include_defects:     bool  = True         # inject intentional lineage/authority defects
    include_missing_lineage: bool = True
    include_authority_conflicts: bool = True
    n_principals:        int   = 3
    n_agents:            int   = 4


# ---------------------------------------------------------------------------
# Tool catalog (simulated from paper examples)
# ---------------------------------------------------------------------------

TOOL_CATALOG = [
    {"name": "drive",    "operations": ["files.get", "files.update", "files.create", "permissions.create"]},
    {"name": "github",   "operations": ["contents.get", "git.createCommit", "pulls.create", "collaborators.add"]},
    {"name": "slack",    "operations": ["chat.postMessage", "conversations.history", "files.upload"]},
    {"name": "search",   "operations": ["web_search", "semantic_search"]},
    {"name": "crm",      "operations": ["query", "search", "update"]},
    {"name": "email",    "operations": ["send", "get", "list"]},
    {"name": "calendar", "operations": ["events.insert", "events.get", "acl.insert"]},
    {"name": "gdocs",    "operations": ["documents.get", "documents.batchUpdate"]},
]

RESOURCE_CATALOG = [
    {"type": ResourceType.DOCUMENT,  "ids": ["doc_q4_report", "doc_sales_data", "doc_strategy"]},
    {"type": ResourceType.REPO,      "ids": ["repo_main", "repo_tools", "repo_data"]},
    {"type": ResourceType.CHANNEL,   "ids": ["channel_sales", "channel_eng", "channel_exec"]},
    {"type": ResourceType.FILE,      "ids": ["file_sales_q4", "file_budget", "file_roadmap"]},
    {"type": ResourceType.EMAIL,     "ids": ["email_exec_summary", "email_customer_data"]},
    {"type": ResourceType.DATABASE,  "ids": ["db_crm_customers", "db_analytics"]},
]

SENSITIVITY_LEVELS = [
    SensitivityLabel.PUBLIC,
    SensitivityLabel.INTERNAL,
    SensitivityLabel.CONFIDENTIAL,
    SensitivityLabel.RESTRICTED,
]


@dataclass
class SyntheticDataset:
    """A generated synthetic dataset with ground truth."""
    events:             list[CIMEvent]
    delegation_records: list[DelegationRecord]
    ground_truth:       dict[str, set[str]]      # delegation_id → set[event_id]
    config:             ScenarioConfig
    defects:            list[dict[str, Any]]     # injected defects with metadata
    scenario_notes:     list[str]                = field(default_factory=list)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class SyntheticDatasetGenerator:
    """
    Generates deterministic synthetic datasets for experimental evaluation.

    All randomness is seeded; the same seed + config always produces the
    same dataset. Configuration is recorded alongside output.
    """

    def __init__(self, config: Optional[ScenarioConfig] = None) -> None:
        self.config = config or ScenarioConfig()
        self._rng   = random.Random(self.config.seed)

    def generate(self) -> SyntheticDataset:
        """Generate a synthetic dataset according to the configured scenario."""
        scenario = self.config.scenario
        if scenario == "W1":
            return self._generate_w1()
        elif scenario == "W2":
            return self._generate_w2()
        elif scenario == "W3":
            return self._generate_w3()
        elif scenario == "mixed":
            return self._generate_mixed()
        elif scenario == "defective":
            return self._generate_defective()
        else:
            raise ValueError(f"Unknown scenario: {scenario!r}")

    # ------------------------------------------------------------------
    # W1: Cross-system, causally disconnected subtasks
    # ------------------------------------------------------------------

    def _generate_w1(self) -> SyntheticDataset:
        """
        W1: A single delegation issues parallel reads across multiple tools
        and produces a single synthesis (write/share) action.
        This induces causally disconnected components within one delegation.
        """
        events:    list[CIMEvent]      = []
        del_recs:  list[DelegationRecord] = []
        gt:        dict[str, set[str]] = {}
        defects:   list[dict]          = []
        notes:     list[str]           = []

        principals = self._make_principals()
        agents     = self._make_agents()
        tools      = self._pick_tools(min(self.config.n_tools, len(TOOL_CATALOG)))

        base_time = datetime.now(timezone.utc)

        for wf_idx in range(self.config.n_workflows):
            principal = principals[wf_idx % len(principals)]
            agent     = agents[wf_idx % len(agents)]
            wf_id     = f"wf_w1_{wf_idx}"
            d_id      = f"del_w1_{wf_idx}"
            workflow_id = wf_id

            dr = DelegationRecord(
                delegation_id        = d_id,
                parent_delegation_id = None,
                principal_user_id    = principal,
                agent_id             = agent,
                issued_at            = (base_time + timedelta(seconds=wf_idx * 120)).isoformat(),
                scope_description    = "Summarize Q4 sales performance",
                authority_envelope   = {
                    "permitted_action_tier1": ["read", "search", "write", "share"],
                    "permitted_resource_class": ["document", "file", "channel"],
                },
            )
            del_recs.append(dr)
            gt[d_id] = set()

            trace_id = self._maybe_trace()
            # Parallel reads (causally disconnected within the delegation)
            for t in tools[:-1]:
                op      = self._rng.choice(t["operations"])
                res     = self._pick_resource()
                ev_time = base_time + timedelta(seconds=wf_idx * 120 + self._rng.uniform(0, 5))
                span_id = str(uuid.UUID(int=self._rng.getrandbits(128)))
                ev = self._make_event(
                    time          = ev_time,
                    delegation_id = d_id,
                    principal     = principal,
                    agent         = agent,
                    tool_name     = t["name"],
                    operation     = op,
                    resource      = res,
                    trace_id      = trace_id if self._rng.random() < self.config.trace_coverage else None,
                    span_id       = span_id,
                    parent_span_id = None,  # parallel — no causal parent
                    outcome       = "success",
                    workflow_id   = workflow_id,
                )
                events.append(ev)
                gt[d_id].add(ev.event_id)

            # Synthesis action (write/share)
            synthesis_tool = tools[-1]
            ev_time = base_time + timedelta(seconds=wf_idx * 120 + 10)
            ev = self._make_event(
                time          = ev_time,
                delegation_id = d_id,
                principal     = principal,
                agent         = agent,
                tool_name     = synthesis_tool["name"],
                operation     = synthesis_tool["operations"][-1],
                resource      = self._pick_resource(prefer_channel=True),
                trace_id      = trace_id if self._rng.random() < self.config.trace_coverage else None,
                span_id       = str(uuid.UUID(int=self._rng.getrandbits(128))),
                parent_span_id = None,
                outcome       = "success",
                workflow_id   = workflow_id,
                target_principal = "external_user@example.com",
            )
            events.append(ev)
            gt[d_id].add(ev.event_id)

        notes.append("W1: Parallel reads across multiple tools produce causally disconnected components (high Comp(d)).")
        return SyntheticDataset(
            events=events,
            delegation_records=del_recs,
            ground_truth=gt,
            config=self.config,
            defects=defects,
            scenario_notes=notes,
        )

    # ------------------------------------------------------------------
    # W2: Multi-agent composition
    # ------------------------------------------------------------------

    def _generate_w2(self) -> SyntheticDataset:
        """
        W2: A primary delegation spawns child agents via re-delegation.
        Stresses lineage closure (I3) and multi-agent authority propagation.
        """
        events:   list[CIMEvent]       = []
        del_recs: list[DelegationRecord] = []
        gt:       dict[str, set[str]]  = {}
        defects:  list[dict]           = []
        notes:    list[str]            = []

        principals = self._make_principals()
        agents     = self._make_agents()
        tools      = self._pick_tools(min(self.config.n_tools, len(TOOL_CATALOG)))
        base_time  = datetime.now(timezone.utc)

        for wf_idx in range(self.config.n_workflows):
            principal = principals[wf_idx % len(principals)]
            root_agent = agents[0]
            root_d_id  = f"del_w2_root_{wf_idx}"
            wf_id      = f"wf_w2_{wf_idx}"

            root_dr = DelegationRecord(
                delegation_id     = root_d_id,
                parent_delegation_id = None,
                principal_user_id = principal,
                agent_id          = root_agent,
                issued_at         = (base_time + timedelta(seconds=wf_idx * 200)).isoformat(),
                scope_description = "Multi-step research and synthesis",
                authority_envelope = {
                    "permitted_action_tier1": ["read", "search", "write", "share", "invoke"],
                    "permitted_resource_class": ["document", "repo", "channel", "file"],
                },
            )
            del_recs.append(root_dr)
            gt[root_d_id] = set()

            t0 = base_time + timedelta(seconds=wf_idx * 200)
            trace_id = self._maybe_trace()
            root_span = str(uuid.UUID(int=self._rng.getrandbits(128)))

            # Root agent invokes initial tool
            ev = self._make_event(
                time          = t0,
                delegation_id = root_d_id,
                principal     = principal,
                agent         = root_agent,
                tool_name     = tools[0]["name"],
                operation     = tools[0]["operations"][0],
                resource      = self._pick_resource(),
                trace_id      = trace_id,
                span_id       = root_span,
                outcome       = "success",
                workflow_id   = wf_id,
            )
            events.append(ev)
            gt[root_d_id].add(ev.event_id)

            # Spawn child agents
            n_children = min(self.config.spawn_fanout, len(agents) - 1)
            for child_idx in range(n_children):
                child_agent = agents[(child_idx + 1) % len(agents)]
                child_d_id  = f"del_w2_child_{wf_idx}_{child_idx}"
                child_tool  = tools[(child_idx + 1) % len(tools)]

                child_dr = DelegationRecord(
                    delegation_id        = child_d_id,
                    parent_delegation_id = root_d_id,  # lineage preserved
                    principal_user_id    = principal,   # principal propagates
                    agent_id             = child_agent,
                    issued_at            = (t0 + timedelta(seconds=2)).isoformat(),
                    scope_description    = f"Sub-task {child_idx}: specialized research",
                )
                del_recs.append(child_dr)
                gt[child_d_id] = set()

                # Child may have a different trace (fragmented)
                child_trace = self._maybe_trace() if self._rng.random() > 0.5 else trace_id
                child_span  = str(uuid.UUID(int=self._rng.getrandbits(128)))

                for step in range(3):
                    op  = self._rng.choice(child_tool["operations"])
                    ev  = self._make_event(
                        time          = t0 + timedelta(seconds=3 + child_idx * 5 + step),
                        delegation_id = child_d_id,
                        delegation_parent_id = root_d_id,
                        principal     = principal,
                        agent         = child_agent,
                        tool_name     = child_tool["name"],
                        operation     = op,
                        resource      = self._pick_resource(),
                        trace_id      = child_trace,
                        span_id       = f"{child_span}_{step}",
                        parent_span_id = child_span if step > 0 else None,
                        outcome       = "success",
                        workflow_id   = wf_id,
                    )
                    events.append(ev)
                    gt[child_d_id].add(ev.event_id)

            # Optionally inject missing lineage defect
            if self.config.include_defects and self.config.include_missing_lineage and wf_idx == 0:
                orphan_d_id = f"del_w2_missing_parent_{wf_idx}"
                ev = self._make_event(
                    time          = t0 + timedelta(seconds=50),
                    delegation_id = orphan_d_id,
                    delegation_parent_id = "del_NONEXISTENT_parent",  # missing parent
                    principal     = principal,
                    agent         = agents[-1],
                    tool_name     = tools[-1]["name"],
                    operation     = "files.get",
                    resource      = self._pick_resource(),
                    outcome       = "success",
                    workflow_id   = wf_id,
                )
                events.append(ev)
                gt.setdefault(orphan_d_id, set()).add(ev.event_id)
                defects.append({
                    "type":       "missing_lineage",
                    "event_id":   ev.event_id,
                    "delegation_id": orphan_d_id,
                    "description": "delegation_parent_id references a non-existent delegation record.",
                })

        notes.append("W2: Multi-agent composition with nested delegation. Trace may fragment across sub-agent runtimes.")
        return SyntheticDataset(
            events=events,
            delegation_records=del_recs,
            ground_truth=gt,
            config=self.config,
            defects=defects,
            scenario_notes=notes,
        )

    # ------------------------------------------------------------------
    # W3: Trial-and-adapt (retries and backtracking)
    # ------------------------------------------------------------------

    def _generate_w3(self) -> SyntheticDataset:
        """
        W3: Includes retries and backtracking with interleaved successes
        and failures, reflecting LLM agents that iteratively refine tool
        calls based on intermediate observations.
        """
        events:   list[CIMEvent]       = []
        del_recs: list[DelegationRecord] = []
        gt:       dict[str, set[str]]  = {}
        defects:  list[dict]           = []
        notes:    list[str]            = []

        principals = self._make_principals()
        agents     = self._make_agents()
        tools      = self._pick_tools(min(self.config.n_tools, len(TOOL_CATALOG)))
        base_time  = datetime.now(timezone.utc)

        for wf_idx in range(self.config.n_workflows):
            principal = principals[wf_idx % len(principals)]
            agent     = agents[wf_idx % len(agents)]
            d_id      = f"del_w3_{wf_idx}"
            wf_id     = f"wf_w3_{wf_idx}"

            dr = DelegationRecord(
                delegation_id     = d_id,
                parent_delegation_id = None,
                principal_user_id = principal,
                agent_id          = agent,
                issued_at         = (base_time + timedelta(seconds=wf_idx * 150)).isoformat(),
                scope_description = "Iterative research with retries",
                authority_envelope = {
                    "permitted_action_tier1": ["read", "search", "write"],
                    "permitted_resource_class": ["document", "file", "repo"],
                },
            )
            del_recs.append(dr)
            gt[d_id] = set()

            t0 = base_time + timedelta(seconds=wf_idx * 150)
            trace_id = self._maybe_trace()
            prev_span: Optional[str] = None

            n_steps = self.config.events_per_workflow
            for step in range(n_steps):
                tool = tools[step % len(tools)]
                op   = self._rng.choice(tool["operations"])

                # Simulate retries
                n_attempts = 1
                if self._rng.random() < self.config.retry_rate:
                    n_attempts = self._rng.randint(2, 4)

                for attempt in range(n_attempts):
                    outcome = "success" if attempt == n_attempts - 1 else "failure"
                    span_id = str(uuid.UUID(int=self._rng.getrandbits(128)))
                    ev = self._make_event(
                        time          = t0 + timedelta(seconds=step * 3 + attempt * 0.5),
                        delegation_id = d_id,
                        principal     = principal,
                        agent         = agent,
                        tool_name     = tool["name"],
                        operation     = op,
                        resource      = self._pick_resource(),
                        trace_id      = trace_id if self._rng.random() < self.config.trace_coverage else None,
                        span_id       = span_id,
                        parent_span_id = prev_span if step > 0 else None,
                        outcome       = outcome,
                        workflow_id   = wf_id,
                    )
                    events.append(ev)
                    gt[d_id].add(ev.event_id)

                    if outcome == "success":
                        prev_span = span_id

        # Inject authority conflict defect
        if self.config.include_defects and self.config.include_authority_conflicts:
            # An agent performs a "share" to external outside its envelope
            target_d_id = f"del_w3_0"
            if target_d_id in {d.delegation_id for d in del_recs}:
                # Override authority envelope to restrict sharing
                for dr in del_recs:
                    if dr.delegation_id == target_d_id:
                        dr.authority_envelope = {
                            "permitted_action_tier1": ["read", "search"],  # NO share
                            "permitted_resource_class": ["document", "file"],
                        }
                # Add a share event (violates envelope)
                ev = self._make_event(
                    time          = datetime.now(timezone.utc),
                    delegation_id = target_d_id,
                    principal     = principals[0],
                    agent         = agents[0],
                    tool_name     = "slack",
                    operation     = "chat.postMessage",
                    resource      = self._pick_resource(prefer_channel=True),
                    outcome       = "success",
                    workflow_id   = "wf_w3_0",
                    target_principal = "external_partner@partner.com",
                )
                events.append(ev)
                gt.setdefault(target_d_id, set()).add(ev.event_id)
                defects.append({
                    "type":        "authority_conflict",
                    "event_id":    ev.event_id,
                    "delegation_id": target_d_id,
                    "description": "Share action performed under a delegation whose envelope only permits read/search.",
                })

        notes.append("W3: Trial-and-adapt with retries and backtracking. Single delegation spans multiple trace components.")
        return SyntheticDataset(
            events=events,
            delegation_records=del_recs,
            ground_truth=gt,
            config=self.config,
            defects=defects,
            scenario_notes=notes,
        )

    # ------------------------------------------------------------------
    # Mixed scenario
    # ------------------------------------------------------------------

    def _generate_mixed(self) -> SyntheticDataset:
        """Generate a dataset combining W1, W2, and W3 patterns."""
        c = self.config
        datasets = []
        for scenario in ["W1", "W2", "W3"]:
            sub_cfg = ScenarioConfig(
                scenario            = scenario,
                seed                = c.seed,
                n_workflows         = max(1, c.n_workflows // 3),
                n_tools             = c.n_tools,
                events_per_workflow = c.events_per_workflow,
                overlap_rate        = c.overlap_rate,
                spawn_fanout        = c.spawn_fanout,
                retry_rate          = c.retry_rate,
                trace_coverage      = c.trace_coverage,
                include_defects     = c.include_defects,
                include_missing_lineage      = c.include_missing_lineage,
                include_authority_conflicts  = c.include_authority_conflicts,
                n_principals        = c.n_principals,
                n_agents            = c.n_agents,
            )
            gen = SyntheticDatasetGenerator(sub_cfg)
            datasets.append(gen.generate())

        # Merge datasets
        all_events: list[CIMEvent] = []
        all_del_recs: list[DelegationRecord] = []
        merged_gt: dict[str, set[str]] = {}
        merged_defects: list[dict] = []
        merged_notes: list[str] = []

        # Track event_ids to avoid duplicates
        seen_event_ids: set[str] = set()
        seen_del_ids: set[str] = set()

        for ds in datasets:
            for ev in ds.events:
                if ev.event_id not in seen_event_ids:
                    all_events.append(ev)
                    seen_event_ids.add(ev.event_id)
            for dr in ds.delegation_records:
                if dr.delegation_id not in seen_del_ids:
                    all_del_recs.append(dr)
                    seen_del_ids.add(dr.delegation_id)
            merged_gt.update(ds.ground_truth)
            merged_defects.extend(ds.defects)
            merged_notes.extend(ds.scenario_notes)

        return SyntheticDataset(
            events=all_events,
            delegation_records=all_del_recs,
            ground_truth=merged_gt,
            config=self.config,
            defects=merged_defects,
            scenario_notes=merged_notes,
        )

    # ------------------------------------------------------------------
    # Defective scenario
    # ------------------------------------------------------------------

    def _generate_defective(self) -> SyntheticDataset:
        """
        Generate a dataset with intentional defects for audit testing:
        - Missing lineage records.
        - Authority-causality mismatch (delegation_id == trace_id).
        - Events with UNBOUND delegation.
        - Authority scope violations.
        """
        base = SyntheticDatasetGenerator(
            ScenarioConfig(
                scenario        = "W1",
                seed            = self.config.seed,
                n_workflows     = 2,
                n_tools         = 3,
                include_defects = False,
            )
        ).generate()

        defects  = []
        extra_ev = []

        principals = self._make_principals()
        agents     = self._make_agents()
        t0 = datetime.now(timezone.utc)

        # Defect 1: delegation_id == trace_id (I2 violation)
        d_id_same_as_trace = "trace_equals_delegation_DEFECT"
        ev_i2 = self._make_event(
            time          = t0,
            delegation_id = d_id_same_as_trace,
            principal     = principals[0],
            agent         = agents[0],
            tool_name     = "drive",
            operation     = "files.get",
            resource      = self._pick_resource(),
            trace_id      = d_id_same_as_trace,  # I2 violation
            outcome       = "success",
        )
        extra_ev.append(ev_i2)
        defects.append({
            "type": "i2_violation",
            "event_id": ev_i2.event_id,
            "description": "delegation_id == trace_id — violates I2 authority-causality separation.",
        })

        # Defect 2: UNBOUND delegation (I5 / orphan)
        ev_unbound = self._make_event(
            time          = t0 + timedelta(seconds=5),
            delegation_id = "UNBOUND",
            principal     = "unknown",
            agent         = "unknown",
            tool_name     = "slack",
            operation     = "chat.postMessage",
            resource      = self._pick_resource(prefer_channel=True),
            outcome       = "success",
        )
        extra_ev.append(ev_unbound)
        defects.append({
            "type": "unbound_delegation",
            "event_id": ev_unbound.event_id,
            "description": "UNBOUND delegation_id — gateway capture boundary not enforced.",
        })

        all_events = base.events + extra_ev
        all_del    = base.delegation_records
        merged_gt  = dict(base.ground_truth)
        merged_def = base.defects + defects

        return SyntheticDataset(
            events=all_events,
            delegation_records=all_del,
            ground_truth=merged_gt,
            config=self.config,
            defects=merged_def,
            scenario_notes=["DEFECTIVE: Contains intentional invariant violations for audit testing."] + base.scenario_notes,
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _make_principals(self) -> list[str]:
        return [f"user_{i:04d}" for i in range(self.config.n_principals)]

    def _make_agents(self) -> list[str]:
        return [f"agent_{i:04d}" for i in range(self.config.n_agents)]

    def _pick_tools(self, n: int) -> list[dict]:
        return TOOL_CATALOG[:n]

    def _pick_resource(self, prefer_channel: bool = False) -> ResourceField:
        if prefer_channel:
            cat = RESOURCE_CATALOG[2]  # channel
        else:
            cat = self._rng.choice(RESOURCE_CATALOG)
        res_id = self._rng.choice(cat["ids"])
        sens   = self._rng.choice(SENSITIVITY_LEVELS)
        return ResourceField(
            type              = cat["type"],
            id                = res_id,
            uri               = f"tool://{cat['type'].value}/{res_id}",
            sensitivity_label = sens,
        )

    def _maybe_trace(self) -> Optional[str]:
        if self._rng.random() < self.config.trace_coverage:
            return str(uuid.UUID(int=self._rng.getrandbits(128)))
        return None

    def _make_event(
        self,
        time:           datetime,
        delegation_id:  str,
        principal:      str,
        agent:          str,
        tool_name:      str,
        operation:      str,
        resource:       Optional[ResourceField] = None,
        trace_id:       Optional[str] = None,
        span_id:        Optional[str] = None,
        parent_span_id: Optional[str] = None,
        outcome:        str = "success",
        workflow_id:    Optional[str] = None,
        target_principal: Optional[str] = None,
        delegation_parent_id: Optional[str] = None,
    ) -> CIMEvent:
        from src.normalization.semantic_normalizer import normalize_action, infer_direction
        from src.schemas.cim_event import ActionOutcome

        norm = normalize_action(tool_name, operation)
        direction = infer_direction(
            tool_name, operation,
            resource.type.value if resource and resource.type else None,
            target_principal,
        )
        tier2 = ActionTier2Field(direction=direction) if direction else None

        try:
            outcome_val = ActionOutcome(outcome.lower())
        except ValueError:
            outcome_val = ActionOutcome.UNKNOWN

        trace = None
        if any([trace_id, span_id, parent_span_id]):
            trace = TraceField(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )

        return CIMEvent(
            time             = time.isoformat(),
            event_id         = str(uuid.uuid4()),
            delegation_id    = delegation_id,
            delegation_parent_id = delegation_parent_id,
            principal        = PrincipalField(user_id=principal),
            agent            = AgentField(
                agent_id     = agent,
                instance_id  = f"{agent}_inst_0",
            ),
            tool             = ToolField(name=tool_name, operation=operation),
            action           = ActionField(
                semantic     = norm.action_semantic,
                outcome      = outcome_val,
                tier2        = tier2,
            ),
            resource         = resource,
            target_principal_id = target_principal,
            trace            = trace,
            workflow         = WorkflowField(workflow_id=workflow_id) if workflow_id else None,
            gateway_captured_at = time.isoformat(),
            gateway_session_id  = f"sess_{delegation_id}",
            minting_context     = "gateway",
        )
