"""
Observability for Delegated Execution in Agentic AI Systems
Research Prototype Dashboard

Based on: Mishra & Sharad (2026), arXiv:2606.09692v1

Launch: streamlit run app.py
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd

from src.storage.event_store import EventStore
from src.ingestion.ingestion_service import IngestionService
from src.delegation.delegation_graph import DelegationGraph
from src.reconstruction.reconstructor import (
    CIMReconstructor,
    BaselineB0TraceOnly,
    BaselineB1TimeWindow,
    BaselineB2Hybrid,
)
from src.audit.audit_engine import AuditEngine
from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
from src.experiments.metrics import MetricsCalculator, PAPER_QUERY_BURDEN_TABLE

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Delegated Execution Observability",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .main-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        padding: 2rem 2.5rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        border: 1px solid rgba(255,255,255,0.08);
    }

    .main-header h1 {
        color: #e2e8f0;
        font-size: 1.8rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.02em;
    }

    .main-header p {
        color: #94a3b8;
        font-size: 0.9rem;
        margin: 0.5rem 0 0 0;
    }

    .metric-card {
        background: linear-gradient(135deg, #1e293b, #0f172a);
        border: 1px solid rgba(148,163,184,0.15);
        border-radius: 10px;
        padding: 1.2rem;
        text-align: center;
        transition: transform 0.2s, border-color 0.2s;
    }

    .metric-card:hover {
        transform: translateY(-2px);
        border-color: rgba(99,179,237,0.4);
    }

    .metric-value {
        font-size: 2rem;
        font-weight: 700;
        color: #63b3ed;
        display: block;
    }

    .metric-label {
        font-size: 0.75rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-top: 0.3rem;
    }

    .finding-card {
        border-radius: 8px;
        padding: 1rem;
        margin: 0.5rem 0;
        border-left: 4px solid;
    }

    .finding-error   { border-left-color: #fc8181; background: rgba(252,129,129,0.08); }
    .finding-warning { border-left-color: #f6ad55; background: rgba(246,173,85,0.08); }
    .finding-info    { border-left-color: #63b3ed; background: rgba(99,179,237,0.08); }

    .badge {
        display: inline-block;
        padding: 0.15rem 0.5rem;
        border-radius: 4px;
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    .badge-error   { background: rgba(252,129,129,0.2); color: #fc8181; }
    .badge-warning { background: rgba(246,173,85,0.2); color: #f6ad55; }
    .badge-info    { background: rgba(99,179,237,0.2); color: #63b3ed; }
    .badge-success { background: rgba(72,187,120,0.2); color: #48bb78; }

    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #e2e8f0;
        margin: 1.2rem 0 0.8rem 0;
        padding-bottom: 0.4rem;
        border-bottom: 1px solid rgba(148,163,184,0.15);
    }

    .info-box {
        background: rgba(99,179,237,0.06);
        border: 1px solid rgba(99,179,237,0.2);
        border-radius: 8px;
        padding: 0.8rem 1rem;
        font-size: 0.85rem;
        color: #90cdf4;
        margin: 0.5rem 0;
    }

    .warning-box {
        background: rgba(246,173,85,0.06);
        border: 1px solid rgba(246,173,85,0.25);
        border-radius: 8px;
        padding: 0.8rem 1rem;
        font-size: 0.85rem;
        color: #f6ad55;
        margin: 0.5rem 0;
    }

    .stDataFrame { border-radius: 8px; }

    div[data-testid="metric-container"] {
        background: rgba(30,41,59,0.7);
        border: 1px solid rgba(148,163,184,0.12);
        border-radius: 10px;
        padding: 0.8rem;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "cim_events.db"


@st.cache_resource
def get_store() -> EventStore:
    return EventStore(db_path=DB_PATH)


def get_components():
    store   = get_store()
    del_graph = DelegationGraph(store)
    ingestion = IngestionService(store)
    return store, del_graph, ingestion


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

PAGES = [
    "🏠  Overview",
    "📋  Event Explorer",
    "🌳  Delegation Explorer",
    "🔄  Execution Trace",
    "🛡️  Audit & Integrity",
    "🧪  Experiment Lab",
    "📤  Import / Export",
]

with st.sidebar:
    st.markdown("""
    <div style="text-align:center; padding: 1rem 0 1.5rem 0;">
        <div style="font-size:1.8rem;">🔍</div>
        <div style="font-weight:700; color:#e2e8f0; font-size:0.95rem; margin-top:0.4rem;">
            Delegated Execution<br>Observability
        </div>
        <div style="font-size:0.72rem; color:#64748b; margin-top:0.3rem;">
            arXiv:2606.09692v1
        </div>
    </div>
    """, unsafe_allow_html=True)

    page = st.radio("Navigate", PAGES, label_visibility="collapsed")

    st.markdown("---")

    store, del_graph, ingestion = get_components()
    stats = store.stats()

    st.markdown(f"""
    <div style="font-size:0.8rem; color:#64748b; line-height:1.8;">
        📊 <b style="color:#94a3b8;">{stats['events']:,}</b> events<br>
        🔗 <b style="color:#94a3b8;">{stats['delegations']:,}</b> delegations<br>
        👤 <b style="color:#94a3b8;">{stats['principals']:,}</b> principals<br>
        🤖 <b style="color:#94a3b8;">{stats['agents']:,}</b> agents<br>
        ⚠️ <b style="color:#94a3b8;">{stats['quarantined']:,}</b> quarantined
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    if st.button("🗄️ Load Sample Dataset", use_container_width=True):
        from sample_data.sample_loader import load_sample_dataset
        with st.spinner("Loading sample data…"):
            result = load_sample_dataset(store)
        st.success(f"Loaded {result['events_loaded']} events, {result['delegations_loaded']} delegations.")
        st.rerun()

    if st.button("🗑️ Clear All Data", use_container_width=True, type="secondary"):
        store.clear_all()
        st.warning("All data cleared.")
        st.rerun()


# ---------------------------------------------------------------------------
# Page router
# ---------------------------------------------------------------------------

page_key = page.split("  ", 1)[-1].strip()


# ===========================================================================
# PAGE 1: OVERVIEW
# ===========================================================================

if "Overview" in page:
    st.markdown("""
    <div class="main-header">
        <h1>🔍 Observability for Delegated Execution in Agentic AI Systems</h1>
        <p>Research prototype based on Mishra & Sharad (2026) · arXiv:2606.09692v1 · Splunk/Cisco</p>
    </div>
    """, unsafe_allow_html=True)

    stats = store.stats()

    # Metric cards
    cols = st.columns(7)
    metric_items = [
        ("events",      "Events",       "📋"),
        ("delegations", "Delegations",  "🔗"),
        ("principals",  "Principals",   "👤"),
        ("agents",      "Agents",       "🤖"),
        ("tools",       "Tools",        "🔧"),
        ("workflows",   "Workflows",    "🔄"),
        ("quarantined", "Quarantined",  "⚠️"),
    ]
    for col, (key, label, icon) in zip(cols, metric_items):
        with col:
            st.metric(f"{icon} {label}", stats[key])

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="section-header">Research Paper Framework</div>', unsafe_allow_html=True)
        st.markdown("""
        **Core Problem:** Delegation-scoped execution is **not identifiable** from standard
        observables — audit logs and execution traces can be identical under multiple
        incompatible delegation assignments (Non-identifiability, Proposition 2.1).

        **Solution:** Agent-aware Common Information Model (CIM) that binds delegation
        context at execution time through a lightweight gateway, enabling:

        - ✅ Cross-tool delegation-scoped reconstruction
        - ✅ Direct forensic queries without heuristic time-window correlation
        - ✅ Authority-causality divergence detection
        - ✅ Payload-independence (no prompt/chain-of-thought inspection)
        """)

        st.markdown('<div class="section-header">CIM Requirements (§2.4)</div>', unsafe_allow_html=True)
        requirements = [
            ("I1", "Delegation Consistency", "Events sharing delegation_id must correspond to stable authority context"),
            ("I2", "Authority-Causality Separation", "delegation_id must not coincide with trace_id"),
            ("I3", "Lineage Closure", "Re-delegation must preserve attribution through lineage"),
            ("I4", "Cross-System Normalization", "Stable action/resource vocabulary across tools"),
            ("I5", "Binding Integrity", "delegation_id bound to authenticated principals via gateway"),
        ]
        for rule_id, name, desc in requirements:
            st.markdown(f"**{rule_id}. {name}** — {desc}")

    with col2:
        st.markdown('<div class="section-header">System Architecture</div>', unsafe_allow_html=True)
        st.code("""
Agent/Tool Invocation
        ↓
Instrumentation Gateway  (§6)
  • Captures tool invocations
  • Binds delegation_id at boundary
  • Enforces provenance/trust
        ↓
CIM Event Construction & Validation  (Appendix B.1)
  • Required: time, event_id, delegation_id,
    principal.user_id, agent.agent_id,
    tool.name, action.semantic
  • Optional: resource, trace, workflow, labels
        ↓
Semantic Normalization  (Appendix B.2)
  • Tier-1: read, write, share, invoke, search…
  • Tier-2: direction, principal_scope
        ↓
Persistent Event Store  (SQLite, append-only)
        ↓
Delegation-Aware Reconstruction Engine
  • CIM (unambiguous, Amb=1 for covered events)
  • Baseline B0 (trace-only)
  • Baseline B1 (time-window)
  • Baseline B2 (hybrid)
        ↓
Audit Engine  (I1–I5, ACD, Drift)
        ↓
Dashboard & Experimental Evaluation
        """, language="text")

        st.markdown('<div class="section-header">Dataset Status</div>', unsafe_allow_html=True)
        if stats["events"] == 0:
            st.markdown("""
            <div class="warning-box">
                No events loaded. Use the <b>Load Sample Dataset</b> button in the sidebar,
                or use the <b>Import/Export</b> page to load your own events.
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="info-box">
                Dataset loaded: <b>{stats['events']:,}</b> events across
                <b>{stats['delegations']:,}</b> delegations and
                <b>{stats['workflows']:,}</b> workflows.
                {stats['quarantined']:,} event(s) quarantined (rejected during validation).
            </div>
            """, unsafe_allow_html=True)

    # Reconstruction summary if data exists
    if stats["events"] > 0:
        st.markdown('<div class="section-header">Quick Reconstruction Summary</div>', unsafe_allow_html=True)
        del_graph.build()
        cim_result = CIMReconstructor(store, del_graph).reconstruct()
        b0_result  = BaselineB0TraceOnly(store).reconstruct()

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("CIM Workflows", len(cim_result.workflows))
        with c2:
            st.metric("CIM Covered Events", len(cim_result.covered_event_ids))
        with c3:
            avg_amb_cim = sum(w.ambiguity for w in cim_result.workflows) / max(1, len(cim_result.workflows))
            st.metric("CIM Avg. Ambiguity", f"{avg_amb_cim:.1f}")
        with c4:
            avg_amb_b0 = sum(w.ambiguity for w in b0_result.workflows) / max(1, len(b0_result.workflows))
            st.metric("B0 Avg. Ambiguity", f"{avg_amb_b0:.1f}")


# ===========================================================================
# PAGE 2: EVENT EXPLORER
# ===========================================================================

elif "Event Explorer" in page:
    st.title("📋 Event Explorer")
    st.caption("Browse and filter stored CIM events. Select a record for full details.")

    stats = store.stats()
    if stats["events"] == 0:
        st.info("No events in the store. Load the sample dataset first.")
    else:
        with st.expander("🔍 Filters", expanded=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                filter_delegation = st.text_input("delegation_id")
                filter_principal  = st.text_input("principal_user_id")
            with col2:
                filter_agent = st.text_input("agent_id")
                filter_tool  = st.text_input("tool_name")
            with col3:
                filter_action  = st.text_input("action_semantic")
                filter_workflow = st.text_input("workflow_id")

        rows = store.query_events(
            delegation_id    = filter_delegation   or None,
            principal_user_id = filter_principal   or None,
            agent_id         = filter_agent        or None,
            tool_name        = filter_tool         or None,
            action_semantic  = filter_action       or None,
            workflow_id      = filter_workflow      or None,
            limit            = 1000,
        )

        if not rows:
            st.warning("No events match the current filters.")
        else:
            display_cols = [
                "event_id", "time", "delegation_id", "principal_user_id",
                "agent_id", "tool_name", "action_semantic", "action_outcome",
                "resource_type", "workflow_id",
            ]
            df = pd.DataFrame(rows)[display_cols]
            df["event_id"] = df["event_id"].str[:12] + "…"
            df["time"]     = pd.to_datetime(df["time"], utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
            df["delegation_id"] = df["delegation_id"].str[:20]

            st.markdown(f"**{len(rows):,} event(s) found**")
            selection = st.dataframe(df, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row")

            # Detail panel
            if selection and selection.selection.rows:
                idx = selection.selection.rows[0]
                ev  = rows[idx]
                st.markdown("---")
                st.markdown("### Event Detail")
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(f"**event_id:** `{ev['event_id']}`")
                    st.markdown(f"**time:** `{ev['time']}`")
                    st.markdown(f"**delegation_id:** `{ev['delegation_id']}`")
                    st.markdown(f"**delegation_parent_id:** `{ev.get('delegation_parent_id', '—')}`")
                    st.markdown(f"**principal_user_id:** `{ev['principal_user_id']}`")
                    st.markdown(f"**agent_id:** `{ev['agent_id']}`")
                    st.markdown(f"**agent_instance_id:** `{ev.get('agent_instance_id', '—')}`")
                with c2:
                    st.markdown(f"**tool_name:** `{ev['tool_name']}`")
                    st.markdown(f"**tool_operation:** `{ev.get('tool_operation', '—')}`")
                    st.markdown(f"**action_semantic:** `{ev['action_semantic']}`")
                    st.markdown(f"**action_outcome:** `{ev.get('action_outcome', '—')}`")
                    st.markdown(f"**action_direction:** `{ev.get('action_direction', '—')}`")
                    st.markdown(f"**resource_type:** `{ev.get('resource_type', '—')}`")
                    st.markdown(f"**resource_id:** `{ev.get('resource_id', '—')}`")
                    st.markdown(f"**sensitivity:** `{ev.get('resource_sensitivity', '—')}`")

                with st.expander("Tracing & Workflow"):
                    st.markdown(f"**trace_id:** `{ev.get('trace_id', '—')}`")
                    st.markdown(f"**span_id:** `{ev.get('span_id', '—')}`")
                    st.markdown(f"**parent_span_id:** `{ev.get('parent_span_id', '—')}`")
                    st.markdown(f"**workflow_id:** `{ev.get('workflow_id', '—')}`")

                with st.expander("Gateway Provenance"):
                    st.markdown(f"**gateway_captured_at:** `{ev.get('gateway_captured_at', '—')}`")
                    st.markdown(f"**gateway_session_id:** `{ev.get('gateway_session_id', '—')}`")
                    st.markdown(f"**minting_context:** `{ev.get('minting_context', '—')}`")

                # Associated audit findings
                audit_engine = AuditEngine(store, del_graph)
                audit_engine.run_all_checks()
                findings = audit_engine.findings_for_event(ev["event_id"])
                if findings:
                    st.markdown("**Associated Audit Findings:**")
                    for f in findings:
                        color_cls = f"finding-{f.severity}"
                        st.markdown(f"""
                        <div class="finding-card {color_cls}">
                            <span class="badge badge-{f.severity}">{f.severity}</span>
                            <b>{f.rule_id}: {f.rule_name}</b><br>
                            <small>{f.explanation}</small>
                        </div>
                        """, unsafe_allow_html=True)


# ===========================================================================
# PAGE 3: DELEGATION EXPLORER
# ===========================================================================

elif "Delegation Explorer" in page:
    st.title("🌳 Delegation Explorer")
    st.caption("Visualize the authority graph G_auth and inspect delegation lineage.")

    del_graph.build()
    nodes = del_graph.all_nodes()
    stats = store.stats()

    if stats["delegations"] == 0 and not nodes:
        st.info("No delegations in the store. Load the sample dataset first.")
    else:
        # Delegation list
        del_rows = store.all_delegations()
        if not del_rows:
            # Build from events
            del_rows = [
                {
                    "delegation_id": d_id,
                    "parent_delegation_id": node.parent_delegation_id,
                    "principal_user_id": node.principal_user_id,
                    "agent_id": node.agent_id,
                    "issued_at": node.issued_at,
                    "closed_at": node.closed_at,
                    "event_count": node.event_count,
                }
                for d_id, node in nodes.items()
            ]

        df_del = pd.DataFrame(del_rows)
        # Add event counts
        event_counts = {d_id: node.event_count for d_id, node in nodes.items()}
        df_del["event_count"] = df_del["delegation_id"].map(event_counts).fillna(0).astype(int)

        display_del_cols = [
            c for c in ["delegation_id", "parent_delegation_id", "principal_user_id",
                         "agent_id", "issued_at", "closed_at", "event_count"]
            if c in df_del.columns
        ]
        df_del_display = df_del[display_del_cols].copy()
        if "delegation_id" in df_del_display.columns:
            df_del_display["delegation_id"] = df_del_display["delegation_id"].str[:30]

        st.markdown(f"**{len(del_rows):,} delegation(s) in authority graph G_auth**")
        selection = st.dataframe(
            df_del_display, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row"
        )

        if selection and selection.selection.rows:
            idx   = selection.selection.rows[0]
            d_id  = del_rows[idx]["delegation_id"]
            node  = del_graph.get_node(d_id)
            st.markdown("---")
            st.markdown(f"### Delegation Detail: `{d_id}`")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**delegation_id:** `{d_id}`")
                st.markdown(f"**parent_delegation_id:** `{node.parent_delegation_id or '— (root)'}`")
                st.markdown(f"**principal_user_id:** `{node.principal_user_id}`")
                st.markdown(f"**agent_id:** `{node.agent_id}`")
                st.markdown(f"**issued_at:** `{node.issued_at}`")
                st.markdown(f"**closed_at:** `{node.closed_at or '— (open)'}`")
            with c2:
                lineage = del_graph.analyze_lineage(d_id)
                st.markdown(f"**Is root delegation:** `{lineage.is_root}`")
                st.markdown(f"**Lineage depth:** `{lineage.lineage_depth}`")
                st.markdown(f"**Ancestors:** `{lineage.ancestors or ['—']}`")
                st.markdown(f"**Descendants:** `{lineage.descendants or ['—']}`")
                st.markdown(f"**Has closure evidence:** `{lineage.has_closure}`")

            if node.scope_description:
                st.markdown(f"**Scope:** {node.scope_description}")

            if node.authority_envelope:
                st.markdown("**Authority Envelope:**")
                st.json(node.authority_envelope)

            if lineage.issues:
                st.markdown("**Lineage Issues:**")
                for issue in lineage.issues:
                    st.warning(issue)

            # Events under this delegation
            ev_ids = del_graph.events_under_delegation(d_id)
            st.markdown(f"**Events under this delegation (incl. descendants):** {len(ev_ids)}")
            if ev_ids:
                ev_rows = [store.get_event(eid) for eid in ev_ids[:50] if store.get_event(eid)]
                ev_df = pd.DataFrame(ev_rows)[
                    ["event_id", "time", "delegation_id", "tool_name",
                     "action_semantic", "resource_type"]
                ]
                st.dataframe(ev_df, use_container_width=True, hide_index=True)

            # Divergence
            comp = del_graph.compute_authority_causality_divergence(d_id)
            st.metric("Authority-Causality Divergence Comp(d)", comp,
                      help="Number of causal components spanned by this delegation. "
                           "Comp(d)>1 means trace/session-based reconstruction is structurally mis-specified.")


# ===========================================================================
# PAGE 4: EXECUTION TRACE
# ===========================================================================

elif "Execution Trace" in page:
    st.title("🔄 Execution Trace Explorer")
    st.caption("Reconstructed execution graph G_exec and event timeline.")

    del_graph.build()
    stats = store.stats()

    if stats["events"] == 0:
        st.info("No events in the store. Load the sample dataset first.")
    else:
        cim_result = CIMReconstructor(store, del_graph).reconstruct()

        st.markdown(f"**{len(cim_result.workflows)} reconstructed workflow(s)** · "
                    f"method: `{cim_result.method}` · "
                    f"{len(cim_result.covered_event_ids)} covered events · "
                    f"{len(cim_result.uncovered_event_ids)} uncovered")

        if cim_result.uncovered_event_ids:
            st.warning(
                f"{len(cim_result.uncovered_event_ids)} event(s) not covered by any workflow. "
                "These may be orphan or UNBOUND events."
            )

        wf_options = [f"{wf.workflow_id} ({len(wf.event_ids)} events)" for wf in cim_result.workflows]
        selected_wf_label = st.selectbox("Select Workflow", wf_options)
        selected_idx = wf_options.index(selected_wf_label)
        wf = cim_result.workflows[selected_idx]

        c1, c2, c3, c4 = st.columns(4)
        with c1: st.metric("Events", len(wf.event_ids))
        with c2: st.metric("Delegations", len(wf.delegation_ids))
        with c3: st.metric("Causal Edges", len(wf.causal_edges))
        with c4: st.metric("Ambiguity Amb(E)", wf.ambiguity)

        if wf.notes:
            for note in wf.notes[:3]:
                st.info(note)

        # Timeline
        st.markdown("**Event Timeline (ordered by time):**")
        ev_rows = [store.get_event(eid) for eid in wf.event_ids if store.get_event(eid)]
        if ev_rows:
            ev_rows_sorted = sorted(ev_rows, key=lambda r: r.get("time", ""))
            timeline_df = pd.DataFrame(ev_rows_sorted)[[
                "event_id", "time", "delegation_id", "agent_id",
                "tool_name", "action_semantic", "resource_id",
            ]]
            timeline_df["event_id"] = timeline_df["event_id"].str[:16]
            timeline_df["delegation_id"] = timeline_df["delegation_id"].str[:20]
            st.dataframe(timeline_df, use_container_width=True, hide_index=True)

        # Causal graph
        if wf.causal_edges:
            st.markdown("**Observed Causal Edges (from trace.parent_span_id):**")
            edge_df = pd.DataFrame(
                [(src[:16], dst[:16]) for src, dst in wf.causal_edges],
                columns=["Parent Event", "Child Event"]
            )
            st.dataframe(edge_df, use_container_width=True, hide_index=True)
            st.caption(
                "These edges are **observed** causal links from trace structure. "
                "Absence of an edge does NOT prove events are causally unrelated — "
                "trace coverage is partial."
            )

        if wf.orphan_event_ids:
            st.warning(f"Orphan events in this workflow: {len(wf.orphan_event_ids)} (delegation_id=UNBOUND)")

        if wf.unresolved_event_ids:
            st.warning(
                f"{len(wf.unresolved_event_ids)} event(s) reference a parent_span_id "
                "not found in the dataset. Causal ancestry is unresolved."
            )


# ===========================================================================
# PAGE 5: AUDIT & INTEGRITY
# ===========================================================================

elif "Audit" in page:
    st.title("🛡️ Audit & Integrity")
    st.caption("Invariants I1–I5, authority-causality divergence, execution drift.")

    stats = store.stats()
    if stats["events"] == 0:
        st.info("No events in the store. Load the sample dataset first.")
    else:
        del_graph.build()
        audit_engine = AuditEngine(store, del_graph)
        findings = audit_engine.run_all_checks()
        summary  = audit_engine.summary()

        # Summary metrics
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1: st.metric("Total Findings", summary["total"])
        with c2: st.metric("🔴 Errors",   summary["errors"])
        with c3: st.metric("🟡 Warnings", summary["warnings"])
        with c4: st.metric("🔵 Info",     summary["info"])
        with c5: st.metric("Confirmed",   summary["confirmed"])

        if not findings:
            st.success("No audit findings. The dataset appears consistent.")
        else:
            # Filter controls
            col1, col2, col3 = st.columns(3)
            with col1:
                sev_filter = st.multiselect(
                    "Severity", ["error", "warning", "info"],
                    default=["error", "warning", "info"]
                )
            with col2:
                rule_filter = st.multiselect(
                    "Rule", list({f.rule_id for f in findings})
                )
            with col3:
                confirmed_only = st.checkbox("Confirmed only")

            filtered = [
                f for f in findings
                if (not sev_filter or f.severity in sev_filter)
                and (not rule_filter or f.rule_id in rule_filter)
                and (not confirmed_only or f.is_confirmed)
            ]

            for finding in filtered:
                with st.expander(
                    f"[{finding.severity.upper()}] {finding.rule_id}: {finding.rule_name} "
                    f"({'✓ confirmed' if finding.is_confirmed else '○ potential'})",
                    expanded=finding.severity == "error",
                ):
                    st.markdown(f"**Finding ID:** `{finding.finding_id}`")
                    st.markdown(f"**Status:** {finding.status}")
                    if finding.delegation_id:
                        st.markdown(f"**Delegation:** `{finding.delegation_id}`")
                    st.markdown(f"**Affected events:** {len(finding.event_ids)}")
                    st.markdown(f"**Explanation:**\n\n{finding.explanation}")
                    if finding.evidence:
                        st.markdown("**Evidence:**")
                        for e in finding.evidence:
                            st.markdown(f"- {e}")
                    if finding.next_step:
                        st.markdown(f"**Recommended next step:** {finding.next_step}")
                    st.markdown("""
                    <div class="info-box">
                        <b>Interpretation note:</b> This prototype distinguishes confirmed
                        inconsistencies (structural violations) from potential inconsistencies
                        caused by missing evidence. Missing evidence does not prove a violation.
                    </div>
                    """, unsafe_allow_html=True)


# ===========================================================================
# PAGE 6: EXPERIMENT LAB
# ===========================================================================

elif "Experiment Lab" in page:
    st.title("🧪 Experiment Lab")
    st.caption(
        "Generate synthetic datasets, run reconstruction methods, and compare metrics. "
        "Results are from local synthetic experiments — NOT reproductions of published paper results."
    )

    with st.expander("⚙️ Experiment Configuration", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            scenario = st.selectbox("Scenario", ["W1", "W2", "W3", "mixed", "defective"])
            seed     = st.number_input("Random Seed", value=42, min_value=0, max_value=9999)
        with col2:
            n_workflows  = st.slider("Workflows", 1, 10, 3)
            n_tools      = st.slider("Tools (K)", 2, 7, 4)
        with col3:
            trace_cov    = st.slider("Trace Coverage", 0.0, 1.0, 0.7, 0.1)
            include_def  = st.checkbox("Include Defects", value=True)

    methods = st.multiselect(
        "Reconstruction Methods",
        ["CIM (delegation-aware)", "B0 (trace-only)", "B1 (time-window Δ=30s)", "B2 (hybrid)"],
        default=["CIM (delegation-aware)", "B0 (trace-only)", "B1 (time-window Δ=30s)", "B2 (hybrid)"],
    )

    if st.button("▶️ Run Experiment", type="primary", use_container_width=True):
        with st.spinner("Generating synthetic dataset and running reconstruction…"):
            cfg = ScenarioConfig(
                scenario        = scenario,
                seed            = int(seed),
                n_workflows     = n_workflows,
                n_tools         = n_tools,
                trace_coverage  = trace_cov,
                include_defects = include_def,
            )
            gen = SyntheticDatasetGenerator(cfg)
            dataset = gen.generate()

            # Create isolated in-memory store for experiment
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                exp_db = f.name
            try:
                exp_store   = EventStore(db_path=exp_db)
                exp_svc     = IngestionService(exp_store)
                for dr in dataset.delegation_records:
                    exp_store.upsert_delegation(dr)
                n_ingested = 0
                for ev in dataset.events:
                    r = exp_svc.ingest(ev.model_dump(mode="json"), allow_duplicate=True)
                    if r.valid:
                        n_ingested += 1

                exp_del_graph = DelegationGraph(exp_store)
                exp_del_graph.build()

                # Run selected methods
                results = {}
                if "CIM (delegation-aware)" in methods:
                    results["CIM"] = CIMReconstructor(exp_store, exp_del_graph).reconstruct()
                if "B0 (trace-only)" in methods:
                    results["B0"] = BaselineB0TraceOnly(exp_store).reconstruct()
                if "B1 (time-window Δ=30s)" in methods:
                    results["B1"] = BaselineB1TimeWindow(exp_store, window_seconds=30).reconstruct()
                if "B2 (hybrid)" in methods:
                    results["B2"] = BaselineB2Hybrid(exp_store, window_seconds=30).reconstruct()

                # Compute metrics
                calc = MetricsCalculator(ground_truth=dataset.ground_truth)
                amb_metrics   = {}
                recall_metrics = {}

                for name, result in results.items():
                    amb_metrics[name] = calc.compute_ambiguity(
                        result.workflows, name,
                        result.total_events, len(result.covered_event_ids)
                    )
                    recall_metrics[name] = calc.compute_recall(result.workflows, name)

                div_metric = calc.compute_divergence(exp_del_graph)
                div_metric.compute()

            finally:
                try:
                    os.unlink(exp_db)
                except Exception:
                    pass

        # Display results
        st.success(f"Experiment complete! {n_ingested} events processed.")
        st.markdown("""
        <div class="warning-box">
            ⚠️ <b>Local synthetic experiment</b> — results reflect this prototype's behavior
            on a small synthetic dataset. These are NOT reproductions of the published paper results.
            Paper-reported results are in Table 5 (Ambiguity) and Table 6 (Overhead) of arXiv:2606.09692v1.
        </div>
        """, unsafe_allow_html=True)

        # Dataset info
        st.markdown(f"**Scenario:** {scenario} | **Seed:** {seed} | **Events:** {n_ingested} | "
                    f"**Delegations:** {len(dataset.delegation_records)} | "
                    f"**Defects:** {len(dataset.defects)}")

        if dataset.scenario_notes:
            for note in dataset.scenario_notes:
                st.info(note)

        # Ambiguity table
        st.markdown("### Ambiguity Amb(E)")
        st.caption(
            "Amb(E) = number of distinct delegation groupings consistent with observed telemetry. "
            "CIM achieves Amb=1 for covered events (unambiguous by construction). "
            "Lower is better."
        )
        amb_rows = []
        for name, m in amb_metrics.items():
            amb_rows.append({
                "Method": name,
                "Median Amb": f"{m.median_ambiguity:.1f}",
                "p95 Amb": f"{m.p95_ambiguity:.1f}",
                "Workflows w/ Amb>1": m.workflows_with_ambiguity_gt1,
                "Covered Events": m.covered_events,
            })
        st.dataframe(pd.DataFrame(amb_rows), use_container_width=True, hide_index=True)

        # Recall table
        st.markdown("### Delegation Recall Rec(d)")
        st.caption(
            "Rec(d) = |E*(d) ∩ Ê(d)| / |E*(d)| — fraction of ground-truth events correctly "
            "attributed to delegation d. Higher is better."
        )
        recall_rows = []
        for name, m in recall_metrics.items():
            recall_rows.append({
                "Method": name,
                "Median Recall": f"{m.median_recall:.3f}",
                "p05 Recall": f"{m.p05_recall:.3f}",
                "Mean Recall": f"{m.mean_recall:.3f}",
                "Delegations Evaluated": m.delegations_evaluated,
            })
        st.dataframe(pd.DataFrame(recall_rows), use_container_width=True, hide_index=True)

        # Divergence
        st.markdown("### Authority-Causality Divergence Comp(d)")
        st.caption(
            "Comp(d) = causal connected components spanned by delegation d. "
            "Method-independent. High Comp(d) indicates structural mis-specification "
            "risk for trace/session-based approaches."
        )
        st.markdown(f"- Median Comp(d): **{div_metric.median_comp:.1f}**")
        st.markdown(f"- p95 Comp(d): **{div_metric.p95_comp:.1f}**")
        st.markdown(f"- Fraction with Comp(d)>1: **{div_metric.fraction_gt1*100:.1f}%**")

        # Query burden
        st.markdown("### Query Construction Burden (Paper Table 7)")
        st.caption("Number of correlation, normalization, and join operations per query class.")
        burden_rows = []
        for item in PAPER_QUERY_BURDEN_TABLE:
            burden_rows.append({
                "Query Class": item["query_class"],
                "Baseline Ops": f"{item['baseline_ops'][0]}–{item['baseline_ops'][1]}",
                "CIM Ops": item["cim_ops"],
                "Source": "Paper Table 7 (micro-deployment)",
            })
        st.dataframe(pd.DataFrame(burden_rows), use_container_width=True, hide_index=True)

        # Defects
        if dataset.defects:
            st.markdown("### Injected Defects (Ground Truth for Audit Testing)")
            defect_df = pd.DataFrame(dataset.defects)
            st.dataframe(defect_df, use_container_width=True, hide_index=True)

        # Export
        export_data = {
            "experiment_config": {
                "scenario": scenario,
                "seed": int(seed),
                "n_workflows": n_workflows,
                "n_tools": n_tools,
                "trace_coverage": trace_cov,
                "include_defects": include_def,
            },
            "note": "Local synthetic experiment — NOT a reproduction of published paper results.",
            "ambiguity": {k: {
                "median": v.median_ambiguity,
                "p95": v.p95_ambiguity,
            } for k, v in amb_metrics.items()},
            "recall": {k: {
                "median": v.median_recall,
                "p05": v.p05_recall,
            } for k, v in recall_metrics.items()},
            "divergence": {
                "median_comp": div_metric.median_comp,
                "p95_comp": div_metric.p95_comp,
                "fraction_gt1": div_metric.fraction_gt1,
            },
        }
        st.download_button(
            "⬇️ Export Results (JSON)",
            data=json.dumps(export_data, default=str, indent=2),
            file_name=f"experiment_{scenario}_seed{seed}.json",
            mime="application/json",
        )


# ===========================================================================
# PAGE 7: IMPORT / EXPORT
# ===========================================================================

elif "Import" in page:
    st.title("📤 Import / Export")

    tab_import, tab_export, tab_quarantine = st.tabs(["Import", "Export", "Quarantine"])

    with tab_import:
        st.markdown("### Import CIM Events")
        st.markdown(
            "Upload a JSON array or JSONL file of CIM event records. "
            "Malformed records are quarantined — they do not affect valid stored events."
        )

        fmt = st.radio("Format", ["JSON (array)", "JSONL (one per line)"])
        uploaded = st.file_uploader("Upload event file", type=["json", "jsonl"])

        if uploaded:
            content = uploaded.read().decode("utf-8")
            if st.button("▶️ Ingest"):
                with st.spinner("Ingesting…"):
                    if fmt == "JSON (array)":
                        result = ingestion.ingest_json(content, allow_duplicate=True)
                    else:
                        result = ingestion.ingest_jsonl(content, allow_duplicate=True)
                st.success(f"✅ {result['success']} events ingested, {result['failed']} failed.")
                if result.get("errors"):
                    with st.expander("Validation Errors"):
                        for err in result["errors"][:20]:
                            st.error(err)
                st.rerun()

        st.markdown("---")
        st.markdown("**Example CIM event (minimal):**")
        st.code(json.dumps({
            "time":          "2026-02-13T10:00:01.120Z",
            "event_id":      "e_example_001",
            "delegation_id": "del_abc123",
            "principal":     {"user_id": "user_42"},
            "agent":         {"agent_id": "research-agent"},
            "tool":          {"name": "drive", "operation": "files.get"},
            "action":        {"semantic": "read", "outcome": "success"},
            "resource":      {"type": "document", "id": "doc_q4_report"},
            "gateway_captured_at": "2026-02-13T10:00:01.120Z",
            "minting_context": "gateway",
        }, indent=2), language="json")

    with tab_export:
        st.markdown("### Export Events")
        exp_del = st.text_input("Filter by delegation_id (leave blank for all)")
        if st.button("⬇️ Export to JSON"):
            data = store.export_events_json(delegation_id=exp_del or None)
            st.download_button(
                "Download JSON",
                data=data,
                file_name="cim_events_export.json",
                mime="application/json",
            )

        st.markdown("### Export Delegations")
        if st.button("⬇️ Export Delegations"):
            dels = store.all_delegations()
            st.download_button(
                "Download Delegations JSON",
                data=json.dumps(dels, default=str, indent=2),
                file_name="delegations_export.json",
                mime="application/json",
            )

    with tab_quarantine:
        st.markdown("### Quarantined Events")
        st.caption("Events rejected during validation. Original records are preserved with diagnostic information.")
        q_rows = store.get_quarantined()
        if not q_rows:
            st.info("No quarantined events.")
        else:
            st.markdown(f"**{len(q_rows)} quarantined record(s)**")
            for q in q_rows[:20]:
                with st.expander(f"Quarantined at {q['ingested_at']}", expanded=False):
                    st.markdown("**Errors:**")
                    try:
                        errors = json.loads(q["errors"])
                        for e in errors:
                            st.error(e)
                    except Exception:
                        st.error(q["errors"])
                    st.markdown("**Raw record:**")
                    try:
                        st.json(json.loads(q["raw_json"]))
                    except Exception:
                        st.code(q["raw_json"])
