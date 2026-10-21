"""
Evaluation metrics — §7.2.4 of the paper.

Implements the paper's actual evaluation dimensions:

1. Ambiguity  Amb(E) = |𝒫(E)| — number of distinct delegation groupings
   consistent with observed telemetry.
   CIM achieves Amb(E) = 1 for covered events (unambiguous by construction).

2. Delegation Recall  Rec(d) = |E*(d) ∩ Ê(d)| / |E*(d)|
   Ground-truth recall: fraction of ground-truth events correctly attributed
   to delegation d.
   Reported as median and p05 across delegations.

3. Authority-Causality Divergence  Comp(d) — counts the causal connected
   components spanned by delegation d in G_exec.
   Method-independent (depends only on R_causal and R_auth*).
   High Comp(d) indicates regimes where trace/session-based reconstruction
   is structurally mis-specified.

4. Query Construction Burden — number of correlation, normalization, and join
   operations per query class (Table 7 from paper).
   Baseline: 6-14 ops. CIM: 1-3 ops.

5. Operational Overhead — bytes/event and gateway latency (ms).
   From Table 6: CIM adds 247 bytes/event and sub-0.3 ms gateway latency at p95.
   (These are paper-reported figures for the micro-deployment scale; our local
    measurements reflect prototype scale.)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from statistics import mean, median, stdev
from typing import Optional


@dataclass
class AmbiguityMetric:
    """
    Ambiguity Amb(E) = |𝒫(E)| — number of distinct delegation groupings
    consistent with the observed telemetry for event set E.

    CIM achieves Amb = 1 for covered events (§7.2.4).
    Baselines: Amb increases with overlap and interleaving.
    """
    method:          str
    total_events:    int
    covered_events:  int
    median_ambiguity: float
    p95_ambiguity:   float
    workflows_with_ambiguity_gt1: int
    interpretation:  str = ""

    def __post_init__(self) -> None:
        if not self.interpretation:
            self.interpretation = (
                f"Amb={self.median_ambiguity:.1f} (median). "
                f"{'Unambiguous reconstruction.' if self.median_ambiguity <= 1.0 else 'Ambiguous groupings exist.'}"
            )


@dataclass
class DelegationRecallMetric:
    """
    Delegation Recall Rec(d) = |E*(d) ∩ Ê(d)| / |E*(d)|

    E*(d) = ground-truth event set for delegation d.
    Ê(d)  = reconstructed event set for delegation d.

    Reported as median and p05 across delegations.
    """
    method:                str
    delegation_recalls:    dict[str, float]   = field(default_factory=dict)
    median_recall:         float = 0.0
    p05_recall:            float = 0.0
    mean_recall:           float = 0.0
    delegations_evaluated: int   = 0
    interpretation:        str   = ""

    def compute(self) -> None:
        vals = list(self.delegation_recalls.values())
        if not vals:
            self.median_recall  = 0.0
            self.p05_recall     = 0.0
            self.mean_recall    = 0.0
            self.delegations_evaluated = 0
            return
        sorted_vals = sorted(vals)
        n = len(sorted_vals)
        self.median_recall = sorted_vals[n // 2]
        p05_idx = max(0, int(0.05 * n) - 1)
        self.p05_recall    = sorted_vals[p05_idx]
        self.mean_recall   = mean(sorted_vals)
        self.delegations_evaluated = n
        self.interpretation = (
            f"Rec(d): median={self.median_recall:.2f}, p05={self.p05_recall:.2f}. "
            f"{'Complete recall.' if self.median_recall >= 1.0 else 'Recall degradation detected.'}"
        )


@dataclass
class DivergenceMetric:
    """
    Authority-Causality Divergence Comp(d).

    Counts causal connected components spanned by delegation d.
    Method-independent — depends only on R_causal and R_auth.
    """
    delegation_divergences: dict[str, int]  = field(default_factory=dict)
    median_comp:  float = 0.0
    p95_comp:     float = 0.0
    fraction_gt1: float = 0.0
    interpretation: str = ""

    def compute(self) -> None:
        vals = list(self.delegation_divergences.values())
        if not vals:
            return
        sorted_vals = sorted(vals)
        n = len(sorted_vals)
        self.median_comp  = sorted_vals[n // 2]
        p95_idx = min(n - 1, int(0.95 * n))
        self.p95_comp     = sorted_vals[p95_idx]
        self.fraction_gt1 = sum(1 for v in vals if v > 1) / n
        self.interpretation = (
            f"Comp(d): median={self.median_comp:.0f}, p95={self.p95_comp:.0f}. "
            f"{self.fraction_gt1*100:.1f}% of delegations span >1 causal component."
        )


@dataclass
class QueryBurdenMetric:
    """
    Query Construction Burden — Table 7 from the paper.

    Number of correlation, normalization, and join operations per query class.
    Baseline: 6-14 ops. CIM: 1-3 ops.
    """
    query_class:    str
    baseline_ops:   tuple[int, int]   # (min, max) from paper Table 7
    cim_ops:        int
    method:         str = "cim"
    note:           str = ""

    @property
    def reduction_factor(self) -> float:
        baseline_avg = (self.baseline_ops[0] + self.baseline_ops[1]) / 2
        if self.cim_ops == 0:
            return float("inf")
        return baseline_avg / self.cim_ops


# Paper Table 7 reference data
PAPER_QUERY_BURDEN_TABLE: list[dict] = [
    {
        "query_class":    "Cross-system reconstruction",
        "baseline_ops":   (6, 9),
        "cim_ops":        1,   # estimated
    },
    {
        "query_class":    "Authority propagation / lineage",
        "baseline_ops":   (5, 8),
        "cim_ops":        1,   # estimated
    },
    {
        "query_class":    "Drift / envelope expansion",
        "baseline_ops":   (7, 11),
        "cim_ops":        2,
    },
    {
        "query_class":    "Exfiltration-style sequences",
        "baseline_ops":   (8, 14),
        "cim_ops":        2,
    },
]


@dataclass
class OperationalOverheadMetric:
    """
    Operational overhead — Table 6 from the paper.

    CIM adds 247 bytes/event and sub-0.3 ms gateway latency at p95.
    These are paper-reported figures for micro-deployment scale.
    """
    method:                str
    baseline_bytes_event:  int   = 341    # paper Table 6 baseline
    cim_bytes_event:       int   = 588    # paper Table 6 CIM
    measured_bytes_event:  Optional[float] = None
    measured_latency_p50_ms: Optional[float] = None
    measured_latency_p95_ms: Optional[float] = None
    note: str = (
        "IMPORTANT: Paper-reported figures are from micro-deployment scale. "
        "Prototype measurements reflect local prototype scale only. "
        "These are NOT reproduction of the published results."
    )


# ---------------------------------------------------------------------------
# Metric calculator
# ---------------------------------------------------------------------------

class MetricsCalculator:
    """
    Calculates all evaluation metrics from reconstruction results and ground truth.

    Ground truth is provided as:
        gt: dict[delegation_id, set[event_id]]  — E*(d) for each delegation.
    """

    def __init__(self, ground_truth: dict[str, set[str]]) -> None:
        self.ground_truth = ground_truth  # E*(d)

    def compute_ambiguity(
        self,
        workflows: list,   # list of ReconstructedWorkflow
        method: str,
        total_events: int,
        covered_events: int,
    ) -> AmbiguityMetric:
        """
        Compute Amb(E) from the list of reconstructed workflows.
        Amb(E) = number of distinct delegation groupings consistent with telemetry.
        For CIM: always 1 per workflow (unambiguous for covered events).
        """
        ambiguities = [wf.ambiguity for wf in workflows]
        if not ambiguities:
            return AmbiguityMetric(
                method=method,
                total_events=total_events,
                covered_events=covered_events,
                median_ambiguity=0.0,
                p95_ambiguity=0.0,
                workflows_with_ambiguity_gt1=0,
            )
        sorted_amb = sorted(ambiguities)
        n = len(sorted_amb)
        med = sorted_amb[n // 2]
        p95_idx = min(n - 1, int(0.95 * n))
        p95 = sorted_amb[p95_idx]
        gt1 = sum(1 for a in ambiguities if a > 1)
        return AmbiguityMetric(
            method=method,
            total_events=total_events,
            covered_events=covered_events,
            median_ambiguity=float(med),
            p95_ambiguity=float(p95),
            workflows_with_ambiguity_gt1=gt1,
        )

    def compute_recall(
        self,
        reconstructed_workflows: list,  # list of ReconstructedWorkflow
        method: str,
    ) -> DelegationRecallMetric:
        """
        Compute Rec(d) = |E*(d) ∩ Ê(d)| / |E*(d)| for each delegation.

        Ê(d) is derived from the reconstructed workflows: events in a workflow
        whose delegation_id matches d (primary delegation grouping).
        """
        # Build Ê(d): reconstructed event sets per delegation
        reconstructed: dict[str, set[str]] = {}
        for wf in reconstructed_workflows:
            for d_id in wf.delegation_ids:
                reconstructed.setdefault(d_id, set()).update(wf.event_ids)

        recalls: dict[str, float] = {}
        for d_id, gt_events in self.ground_truth.items():
            if not gt_events:
                recalls[d_id] = 1.0  # vacuously true
                continue
            rec_events = reconstructed.get(d_id, set())
            recall = len(gt_events & rec_events) / len(gt_events)
            recalls[d_id] = recall

        metric = DelegationRecallMetric(
            method=method,
            delegation_recalls=recalls,
        )
        metric.compute()
        return metric

    def compute_divergence(
        self,
        delegation_graph,  # DelegationGraph
    ) -> DivergenceMetric:
        """
        Compute Comp(d) for all delegations.
        Method-independent.
        """
        divergences: dict[str, int] = {}
        for d_id in delegation_graph.all_nodes():
            if d_id == "UNBOUND":
                continue
            comp = delegation_graph.compute_authority_causality_divergence(d_id)
            divergences[d_id] = comp
        metric = DivergenceMetric(delegation_divergences=divergences)
        metric.compute()
        return metric

    @staticmethod
    def query_burden_table() -> list[QueryBurdenMetric]:
        """Return the query burden comparison from paper Table 7."""
        return [
            QueryBurdenMetric(
                query_class  = item["query_class"],
                baseline_ops = item["baseline_ops"],
                cim_ops      = item["cim_ops"],
                note         = "From paper Table 7 (micro-deployment benchmark).",
            )
            for item in PAPER_QUERY_BURDEN_TABLE
        ]

    @staticmethod
    def operational_overhead(
        measured_bytes: Optional[float] = None,
        measured_latency_p50_ms: Optional[float] = None,
        measured_latency_p95_ms: Optional[float] = None,
    ) -> OperationalOverheadMetric:
        return OperationalOverheadMetric(
            method                 = "cim",
            measured_bytes_event   = measured_bytes,
            measured_latency_p50_ms = measured_latency_p50_ms,
            measured_latency_p95_ms = measured_latency_p95_ms,
        )

    @staticmethod
    def measure_event_bytes(event_dicts: list[dict]) -> float:
        """Measure average bytes per event (JSON serialized)."""
        import json
        if not event_dicts:
            return 0.0
        total = sum(len(json.dumps(ev, default=str).encode()) for ev in event_dicts)
        return total / len(event_dicts)

    @staticmethod
    def measure_gateway_latency(gateway_fn, n_samples: int = 20) -> tuple[float, float]:
        """
        Measure gateway emit latency p50 and p95 in milliseconds.
        gateway_fn: callable that performs a gateway.invoke() call.
        """
        latencies: list[float] = []
        for _ in range(n_samples):
            t0 = time.perf_counter()
            gateway_fn()
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        p50 = sorted_lat[n // 2]
        p95 = sorted_lat[min(n - 1, int(0.95 * n))]
        return p50, p95
