"""
Sample data loader — creates the demonstration dataset.

Based on Listing 1 / Appendix B.3 example from the paper:
"Summarize Q4 sales performance and create a memo."

The agent performs four steps spanning heterogeneous systems:
  (i)  reads an internal spreadsheet (drive),
  (ii) queries a CRM (crm),
  (iii) writes a memo document (gdocs),
  (iv) shares the memo to a restricted Slack channel (slack).

All steps occur under a single delegated authorization context.

Also includes:
  - A multi-agent workflow (W2) with nested delegation.
  - A defective workflow with intentional lineage/authority issues.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

from src.experiments.synthetic_generator import (
    SyntheticDatasetGenerator,
    ScenarioConfig,
    SyntheticDataset,
)
from src.storage.event_store import EventStore
from src.schemas.cim_event import DelegationRecord
from src.ingestion.ingestion_service import IngestionService


def load_sample_dataset(store: EventStore) -> dict[str, int]:
    """
    Load the demonstration dataset into the event store.
    Returns a summary of what was loaded.
    """
    svc = IngestionService(store)
    total_events  = 0
    total_del     = 0

    # 1. Listing 1 example from paper (B.3)
    paper_events, paper_dels = _make_paper_listing1_example()
    for dr in paper_dels:
        store.upsert_delegation(dr)
        total_del += 1
    for ev_dict in paper_events:
        result = svc.ingest(ev_dict, allow_duplicate=True)
        if result.valid:
            total_events += 1

    # 2. W1 scenario (cross-system, causally disconnected subtasks)
    w1_cfg = ScenarioConfig(
        scenario            = "W1",
        seed                = 42,
        n_workflows         = 2,
        n_tools             = 4,
        events_per_workflow = 6,
        include_defects     = False,
    )
    w1_ds = SyntheticDatasetGenerator(w1_cfg).generate()
    total_events += _ingest_dataset(svc, store, w1_ds)

    # 3. W2 scenario (multi-agent composition)
    w2_cfg = ScenarioConfig(
        scenario        = "W2",
        seed            = 42,
        n_workflows     = 1,
        n_tools         = 3,
        spawn_fanout    = 2,
        spawn_depth     = 2,
        include_defects = True,
        include_missing_lineage = True,
    )
    w2_ds = SyntheticDatasetGenerator(w2_cfg).generate()
    total_events += _ingest_dataset(svc, store, w2_ds)

    # 4. Defective workflow
    def_cfg = ScenarioConfig(
        scenario        = "defective",
        seed            = 42,
        n_workflows     = 1,
        n_tools         = 3,
        include_defects = True,
        include_authority_conflicts = True,
    )
    def_ds = SyntheticDatasetGenerator(def_cfg).generate()
    total_events += _ingest_dataset(svc, store, def_ds)

    return {
        "events_loaded":     total_events,
        "delegations_loaded": total_del + sum(
            len([d for d in ds.delegation_records])
            for ds in [w1_ds, w2_ds, def_ds]
        ),
    }


def _ingest_dataset(
    svc:   IngestionService,
    store: EventStore,
    ds:    SyntheticDataset,
) -> int:
    """Ingest a SyntheticDataset into the store. Returns count of ingested events."""
    for dr in ds.delegation_records:
        store.upsert_delegation(dr)
    count = 0
    for ev in ds.events:
        result = svc.ingest(ev.model_dump(mode="json"), allow_duplicate=True)
        if result.valid:
            count += 1
    return count


def _make_paper_listing1_example() -> tuple[list[dict], list[DelegationRecord]]:
    """
    Construct the paper's Listing 1 / B.3 example:
    User delegates "Summarize Q4 sales performance and create a memo."
    Four CIM events across drive, crm, gdocs, slack.
    """
    base = datetime(2026, 2, 13, 10, 0, 1, tzinfo=timezone.utc)
    d_id = "del_7b3a9c1e"
    principal = "user_12345"
    agent_id  = "analysis-agent"
    agent_inst = "agentinst_a9"

    delegation = DelegationRecord(
        delegation_id        = d_id,
        parent_delegation_id = None,
        principal_user_id    = principal,
        agent_id             = agent_id,
        issued_at            = (base - timedelta(seconds=10)).isoformat(),
        scope_description    = "Summarize Q4 sales performance and create a memo",
        authority_envelope   = {
            "permitted_action_tier1": ["read", "write", "share"],
            "permitted_resource_class": ["document", "file", "channel"],
        },
        workflow_id          = "wf_paper_listing1",
    )

    # Event e1 — drive.files.get (read spreadsheet)
    e1 = {
        "time":          (base + timedelta(seconds=0)).isoformat(),
        "event_id":      "e1_drive_read_D1",
        "delegation_id": d_id,
        "principal":     {"user_id": principal},
        "agent":         {"agent_id": agent_id, "instance_id": agent_inst},
        "tool":          {"name": "drive", "operation": "files.get"},
        "action":        {"semantic": "read", "outcome": "success"},
        "resource":      {
            "type": "file",
            "id":   "file_sales_q4",
            "uri":  "drive://file_sales_q4",
            "sensitivity_label": "confidential",
        },
        "trace": {"trace_id": "T1", "span_id": "S1", "parent_span_id": None},
        "workflow": {"workflow_id": "wf_paper_listing1"},
        "gateway_captured_at": (base + timedelta(seconds=0)).isoformat(),
        "gateway_session_id":  "sess_listing1",
        "minting_context":     "gateway",
    }

    # Event e2 — crm.search (query CRM for customer segments) — paper example: Slack.search(#sales)
    e2 = {
        "time":          (base + timedelta(seconds=2)).isoformat(),
        "event_id":      "e2_crm_search",
        "delegation_id": d_id,
        "principal":     {"user_id": principal},
        "agent":         {"agent_id": agent_id, "instance_id": agent_inst},
        "tool":          {"name": "crm", "operation": "search"},
        "action":        {"semantic": "search", "outcome": "success"},
        "resource":      {
            "type": "database",
            "id":   "db_crm_customers",
            "sensitivity_label": "confidential",
        },
        "trace": {"trace_id": "T1", "span_id": "S2", "parent_span_id": "S1"},
        "workflow": {"workflow_id": "wf_paper_listing1"},
        "gateway_captured_at": (base + timedelta(seconds=2)).isoformat(),
        "gateway_session_id":  "sess_listing1",
        "minting_context":     "gateway",
    }

    # Event e3 — docs.read (read D2) — mapped to gdocs.documents.get
    e3 = {
        "time":          (base + timedelta(seconds=4)).isoformat(),
        "event_id":      "e3_gdocs_read_D2",
        "delegation_id": d_id,
        "principal":     {"user_id": principal},
        "agent":         {"agent_id": agent_id, "instance_id": agent_inst},
        "tool":          {"name": "gdocs", "operation": "documents.get"},
        "action":        {"semantic": "read", "outcome": "success"},
        "resource":      {
            "type": "document",
            "id":   "doc_q4_report",
            "uri":  "gdocs://doc_q4_report",
            "sensitivity_label": "confidential",
        },
        "trace": {"trace_id": "T1", "span_id": "S3", "parent_span_id": "S2"},
        "workflow": {"workflow_id": "wf_paper_listing1"},
        "gateway_captured_at": (base + timedelta(seconds=4)).isoformat(),
        "gateway_session_id":  "sess_listing1",
        "minting_context":     "gateway",
    }

    # Event e4 — Slack.post(#sales, "draft summary") — share event
    e4 = {
        "time":          (base + timedelta(seconds=6)).isoformat(),
        "event_id":      "e4_slack_post",
        "delegation_id": d_id,
        "principal":     {"user_id": principal},
        "agent":         {"agent_id": agent_id, "instance_id": agent_inst},
        "tool":          {"name": "slack", "operation": "chat.postMessage"},
        "action":        {
            "semantic": "share",
            "outcome":  "success",
            "tier2":    {"direction": "internal", "principal_scope": "org"},
        },
        "resource":      {
            "type": "channel",
            "id":   "channel_sales",
            "sensitivity_label": "internal",
        },
        "target_principal_id": "channel_sales",
        "trace": {"trace_id": "T1", "span_id": "S4", "parent_span_id": "S3"},
        "workflow": {"workflow_id": "wf_paper_listing1"},
        "gateway_captured_at": (base + timedelta(seconds=6)).isoformat(),
        "gateway_session_id":  "sess_listing1",
        "minting_context":     "gateway",
    }

    # Event e5 — Docs.write(Report) — write the final memo
    e5 = {
        "time":          (base + timedelta(seconds=8)).isoformat(),
        "event_id":      "e5_gdocs_write",
        "delegation_id": d_id,
        "principal":     {"user_id": principal},
        "agent":         {"agent_id": agent_id, "instance_id": agent_inst},
        "tool":          {"name": "gdocs", "operation": "documents.batchUpdate"},
        "action":        {"semantic": "write", "outcome": "success"},
        "resource":      {
            "type": "document",
            "id":   "doc_strategy",
            "uri":  "gdocs://doc_strategy",
            "sensitivity_label": "confidential",
        },
        "trace": {"trace_id": "T1", "span_id": "S5", "parent_span_id": "S4"},
        "workflow": {"workflow_id": "wf_paper_listing1"},
        "gateway_captured_at": (base + timedelta(seconds=8)).isoformat(),
        "gateway_session_id":  "sess_listing1",
        "minting_context":     "gateway",
    }

    return [e1, e2, e3, e4, e5], [delegation]
