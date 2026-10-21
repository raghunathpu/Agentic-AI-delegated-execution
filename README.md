# Delegated Execution Observability — Research Prototype

**Based on:** *Observability for Delegated Execution in Agentic AI Systems*  
**Authors:** Abhinav Mishra & Kumar Sharad (Splunk/Cisco AI Research)  
**arXiv:** [2606.09692v1](https://arxiv.org/abs/2606.09692)

---

## Overview

This prototype implements the core observability framework proposed in the paper. It demonstrates how a **Common Information Model (CIM)** with **delegation-aware event capture** via an instrumentation gateway enables:

- Unambiguous, payload-independent reconstruction of delegated execution
- Delegation-scoped forensic queries with dramatically reduced correlation burden
- Detection of authority-causality divergence and execution drift
- Verification of structural invariants I1–I5

> **Scope note:** This implementation covers only the research paper's core model. It does not include any content from other sources or unrelated productivity applications.

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the Dashboard

```bash
streamlit run app.py
```

Or on Windows PowerShell:

```powershell
.\run.ps1
```

### 3. Run Tests

```bash
cd delegated_execution_observability_baseline
pytest tests/ -v
```

### 4. Load Sample Data

In the dashboard sidebar, click **"Load Sample Dataset"** to ingest:
- The paper's Listing 1 / Appendix B.3 example (4 events across drive, crm, gdocs, slack)
- Synthetic W1 (cross-system parallel reads)
- Synthetic W2 (multi-agent nested delegation)
- Defective workflow (intentional invariant violations for audit testing)

---

## Project Structure

```
delegated_execution_observability_baseline/
├── app.py                        # Streamlit dashboard (7 pages)
├── requirements.txt
├── run.ps1                       # Windows launcher
├── run.sh                        # Unix launcher
├── README.md
├── docs/
│   ├── research_mapping.md       # Mapping of paper → implementation
│   ├── architecture.md           # System architecture description
│   ├── data_model.md             # CIM schema field reference
│   └── evaluation.md             # Metric definitions and baselines
├── sample_data/
│   └── sample_loader.py          # Demo dataset loader
├── src/
│   ├── schemas/
│   │   └── cim_event.py          # CIM Event schema (Table 8, Appendix B.1)
│   ├── ingestion/
│   │   └── ingestion_service.py  # Validation, normalization, batch import
│   ├── storage/
│   │   └── event_store.py        # SQLite append-only event store
│   ├── gateway/
│   │   └── gateway.py            # Instrumentation gateway (§6)
│   ├── normalization/
│   │   └── semantic_normalizer.py # Action taxonomy (Appendix B.2)
│   ├── delegation/
│   │   └── delegation_graph.py   # Authority graph G_auth
│   ├── reconstruction/
│   │   └── reconstructor.py      # CIM + baselines B0, B1, B2
│   ├── audit/
│   │   └── audit_engine.py       # Invariants I1–I5 + ACD + drift
│   └── experiments/
│       ├── synthetic_generator.py # W1, W2, W3 scenarios
│       └── metrics.py             # Ambiguity, Recall, Divergence, Burden
└── tests/
    ├── conftest.py
    └── test_all.py                # 40+ pytest test cases
```

---

## Core Architecture

### CIM Event (Table 8, Appendix B.1)

| Field | Req | Description |
|-------|-----|-------------|
| `time` | ✓ | RFC3339 timestamp at gateway capture |
| `event_id` | ✓ | Unique event identifier (UUID) |
| `delegation_id` | ✓ | Durable authorization context identifier |
| `principal.user_id` | ✓ | Human principal (stable ID) |
| `agent.agent_id` | ✓ | Logical agent entity |
| `tool.name` | ✓ | Tool family name |
| `action.semantic` | ✓ | Tier-1 normalized action |
| `delegation_parent_id` | ○ | Parent delegation (lineage) |
| `resource.*` | ○ | Resource type, ID, sensitivity |
| `trace.*` | ○ | Control-flow tracing (optional) |
| `action.tier2.*` | ○ | Direction, principal scope |

### Dual-Graph Representation

```
G_auth (Authority Graph)          G_exec (Execution Graph)
─────────────────────────         ─────────────────────────
del_root                          e1 ─────────────────── e4
  │                               │ (trace parent_span)  │
  ├── del_child_A                 e2 ────────────────── e5
  │     └── del_grandchild        │
  └── del_child_B                 e3
```

- **G_auth** is determined by delegation lineage (parent–child edges).
- **G_exec** is determined by trace.parent_span_id edges.
- These are **structurally independent** — the key insight of §2.3.

### Semantic Action Normalization (Appendix B.2)

Tier-1 verbs:
- `read` — retrieve content (drive.files.get, gdocs.documents.get, github.contents.get)
- `write` — create/modify (gdocs.batchUpdate, github.git.createCommit)
- `share` — distribute/grant access (slack.chat.postMessage, drive.permissions.create)
- `invoke` — trigger execution (github.pulls.merge, actions.createWorkflowDispatch)
- `search` — query/list (crm.query, search.web_search)

### Invariants (§2.4)

| ID | Name | Description |
|----|------|-------------|
| I1 | Delegation Consistency | Events sharing `delegation_id` must share a stable authority context |
| I2 | Authority-Causality Separation | `delegation_id` ≠ `trace_id` |
| I3 | Lineage Closure | Re-delegation must preserve attribution through lineage |
| I4 | Cross-System Normalization | Stable `action.semantic` across tools |
| I5 | Binding Integrity | `delegation_id` bound at authenticated gateway boundary |

---

## Evaluation Metrics

| Metric | Symbol | Definition |
|--------|--------|------------|
| Ambiguity | Amb(E) | \|𝒫(E)\| — number of consistent delegation groupings |
| Delegation Recall | Rec(d) | \|E*(d) ∩ Ê(d)\| / \|E*(d)\| |
| Divergence | Comp(d) | Causal components spanned by delegation d |
| Query Burden | ops/query | Correlation + normalization + join operations |

CIM achieves **Amb(E) = 1** for gateway-captured events (unambiguous by construction). Baselines B0–B2 exhibit Amb > 1 under multi-delegation interleaving.

---

## Reconstruction Baselines

| Method | Description | Failure Mode |
|--------|-------------|--------------|
| CIM | Delegation-aware (primary) | None for gateway-covered events |
| B0 (trace-only) | Group by trace_id, fallback to singleton | Fails delegation closure under multi-agent fanout |
| B1 (time-window) | Sliding window Δ | Cross-delegation contamination |
| B2 (hybrid) | Trace + session + time-window | Still fails under re-delegation |

---

## Important Notes

- **Synthetic data only** — no real enterprise telemetry is used.
- **Local SQLite storage** — all data stays on-device.
- **No paid APIs** — fully self-contained.
- **Prototype scale** — overhead measurements reflect local prototype, not paper's micro-deployment scale.
- **Paper metrics** — published results from Table 5/6 are from a different deployment scale; local experiments use the same methodology but different data.

---

## Non-Identifiability Theorem (Proposition 2.1)

The paper proves that **R_auth cannot be uniquely recovered from standard observables** (E, R_causal, attr). Formally:

> Two distinct authorization relations R_auth ≠ R'_auth can produce identical observed telemetry F(E, R_causal, attr, D, R_auth) = F(E, R_causal, attr, D, R'_auth).

This is why CIM's gateway-bound `delegation_id` is necessary — it makes R_auth directly observable, resolving the ambiguity.

---

## License & Attribution

This is a research prototype for academic purposes. The underlying paper is:

```bibtex
@article{mishra2026observability,
  title   = {Observability for Delegated Execution in Agentic AI Systems},
  author  = {Mishra, Abhinav and Sharad, Kumar},
  journal = {arXiv preprint arXiv:2606.09692},
  year    = {2026}
}
```
