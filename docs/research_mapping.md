# Research Mapping: Paper → Implementation

This document maps every major concept from  
*"Observability for Delegated Execution in Agentic AI Systems"* (arXiv:2606.09692v1)  
to its corresponding implementation file and module.

---

## §1 — Introduction

| Paper concept | Implementation |
|---------------|----------------|
| Delegated execution problem | `README.md` — Overview |
| Non-identifiability (informal) | `src/audit/audit_engine.py` — `AuditEngine` |
| Instrumentation gateway (informal) | `src/gateway/gateway.py` — `InstrumentedGateway` |

---

## §2 — Formal Framework

### §2.1 — Execution Model

| Paper concept | Implementation |
|---------------|----------------|
| Event set E | `src/schemas/cim_event.py` — `CIMEvent` |
| Delegation set D | `src/schemas/cim_event.py` — `DelegationRecord` |
| R_causal (causal structure) | `src/reconstruction/reconstructor.py` — causal_edges |
| R_auth (authorization relation) | `src/delegation/delegation_graph.py` — `DelegationGraph` |
| attr : E → A (event attributes) | `CIMEvent.to_flat_dict()` |

### §2.2 — Non-identifiability (Proposition 2.1)

| Paper concept | Implementation |
|---------------|----------------|
| Standard telemetry assumption | `src/reconstruction/reconstructor.py` — `BaselineB0TraceOnly` |
| Latent R_auth not in observables | `src/audit/audit_engine.py` — I5 binding integrity check |
| Non-injectivity proof sketch | `README.md` — Non-identifiability section |

### §2.3 — CIM

| Paper concept | Implementation |
|---------------|----------------|
| dual-structured observability | `src/delegation/delegation_graph.py` + `src/reconstruction/reconstructor.py` |
| G_auth (authority graph) | `src/delegation/delegation_graph.py` — `DelegationGraph` |
| delegation_id binding | `src/gateway/gateway.py` — `GatewayContext.delegation_id` |

### §2.4 — Structural Invariants I1–I5

| Invariant | Implementation |
|-----------|----------------|
| I1 Delegation Consistency | `src/audit/audit_engine.py` — `_check_i1_delegation_consistency()` |
| I2 Authority-Causality Separation | `src/audit/audit_engine.py` — `_check_i2_authority_causality_separation()` |
| I3 Lineage Closure | `src/audit/audit_engine.py` — `_check_i3_lineage_closure()` |
| I4 Cross-System Normalization | `src/audit/audit_engine.py` — `_check_i4_semantic_normalization()` |
| I5 Binding Integrity | `src/audit/audit_engine.py` — `_check_i5_binding_integrity()` |

---

## §3 — Information Model Specification

| Paper concept | Implementation |
|---------------|----------------|
| Core event envelope | `src/schemas/cim_event.py` — `CIMEvent` |
| Gateway instrumentation | `src/gateway/gateway.py` |
| Delegation records | `src/schemas/cim_event.py` — `DelegationRecord` |

---

## §4 — Delegation-Scoped Queries

| Query | Implementation |
|-------|----------------|
| Q1: All events for delegation d | `EventStore.query_events(delegation_id=d)` |
| Q2: Authority propagation / lineage | `DelegationGraph.ancestors()`, `DelegationGraph.descendants()` |
| Q3: Exfiltration-style sequences | `AuditEngine._check_execution_drift()` |
| Q4: Blast-radius (resource exposure) | `EventStore.query_events(action_semantic="share")` |

---

## §5 — Authority-Causality Structure

### §5.2 — Divergence

| Paper concept | Implementation |
|---------------|----------------|
| Comp(d) = causal components | `DelegationGraph.compute_authority_causality_divergence()` |
| Divergence checking | `AuditEngine._check_authority_causality_divergence()` |

### §5.4 — Execution Drift

| Paper concept | Implementation |
|---------------|----------------|
| Authority envelope | `DelegationRecord.authority_envelope` |
| Drift detection (§5.4) | `AuditEngine._check_execution_drift()` |

---

## §6 — Gateway Instrumentation

| Paper concept | Implementation |
|---------------|----------------|
| Gateway as mediation layer | `src/gateway/gateway.py` — `InstrumentedGateway` |
| Delegation context binding | `GatewayContext` |
| Multi-agent re-delegation | `GatewayContext.child_context()`, `SimulatedAgent.spawn_child_agent()` |
| Explicit absence emission | `InstrumentedGateway.invoke(context=None)` → UNBOUND |
| Provenance / trust boundary | `gateway_captured_at`, `gateway_session_id`, `minting_context` |

---

## §7 — Experimental Evaluation

### §7.1 — Dataset

| Paper concept | Implementation |
|---------------|----------------|
| W1 (cross-system, disconnected) | `SyntheticDatasetGenerator` — `scenario="W1"` |
| W2 (multi-agent composition) | `SyntheticDatasetGenerator` — `scenario="W2"` |
| W3 (trial-and-adapt, retries) | `SyntheticDatasetGenerator` — `scenario="W3"` |
| Configurable parameters (Table 4) | `ScenarioConfig` |
| Ground truth E*(d) | `SyntheticDataset.ground_truth` |
| Injected defects | `SyntheticDataset.defects` |

### §7.2 — Baselines

| Baseline | Implementation |
|----------|----------------|
| B0 (trace-only) | `src/reconstruction/reconstructor.py` — `BaselineB0TraceOnly` |
| B1 (time-window Δ) | `src/reconstruction/reconstructor.py` — `BaselineB1TimeWindow` |
| B2 (hybrid) | `src/reconstruction/reconstructor.py` — `BaselineB2Hybrid` |
| CIM (primary) | `src/reconstruction/reconstructor.py` — `CIMReconstructor` |

### §7.2.4 — Metrics

| Metric | Implementation |
|--------|----------------|
| Ambiguity Amb(E) | `MetricsCalculator.compute_ambiguity()` |
| Delegation Recall Rec(d) | `MetricsCalculator.compute_recall()` |
| Divergence Comp(d) | `MetricsCalculator.compute_divergence()` |
| Query burden (Table 7) | `MetricsCalculator.query_burden_table()` |
| Operational overhead (Table 6) | `MetricsCalculator.operational_overhead()` |

---

## Appendix B — CIM Specification

### Appendix B.1 — Core Event Envelope

| Field | Implementation |
|-------|----------------|
| All Table 8 fields | `CIMEvent` model fields |
| Required fields | `CIMEvent` — Pydantic required fields |
| Optional fields | `CIMEvent` — `Optional[...]` fields |

### Appendix B.2 — Semantic Action Normalization Taxonomy

| Verb group | Implementation |
|------------|----------------|
| Read and discovery (read, search, enumerate) | `_TOOL_OPERATION_MAP` + `ActionSemantic` |
| Write and modification (write, delete, move) | `_TOOL_OPERATION_MAP` + `ActionSemantic` |
| Sharing and authorization (share, revoke, invite) | `_TOOL_OPERATION_MAP` + `ActionSemantic` |
| Execution and delegation (invoke, spawn) | `_TOOL_OPERATION_MAP` + `ActionSemantic` |
| Prefix pattern fallback | `_PREFIX_PATTERNS` |

### Appendix B.3 — Worked Example (Listing 1)

| Example event | Implementation |
|---------------|----------------|
| e1 (drive.files.get) | `sample_data/sample_loader.py` — e1_drive_read_D1 |
| e2 (crm.search) | `sample_data/sample_loader.py` — e2_crm_search |
| e3 (gdocs.documents.get) | `sample_data/sample_loader.py` — e3_gdocs_read_D2 |
| e4 (slack.chat.postMessage) | `sample_data/sample_loader.py` — e4_slack_post |
| e5 (gdocs.batchUpdate) | `sample_data/sample_loader.py` — e5_gdocs_write |
