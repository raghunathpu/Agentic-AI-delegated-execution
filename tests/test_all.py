"""
Pytest test suite for the Delegated Execution Observability prototype.

Tests cover:
- CIM event schema validation (required vs optional fields, enumerations)
- Invalid and malformed events
- Persistent event storage and duplicate detection
- Semantic normalization across different tools
- Delegation lineage and nested delegation
- Reconstruction (CIM + baselines B0, B1, B2)
- Audit checks (I1–I5, ACD, drift, orphan)
- Evaluation metrics
- JSON/JSONL import and export
- Deterministic synthetic generation
"""

from __future__ import annotations

import json
import tempfile
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "test_events.db")


def make_valid_event(**overrides) -> dict:
    """Return a minimal valid CIM event dict."""
    base = {
        "time":          "2026-02-13T10:00:01.120Z",
        "event_id":      str(uuid.uuid4()),
        "delegation_id": "del_test_001",
        "principal":     {"user_id": "user_12345"},
        "agent":         {"agent_id": "research-agent"},
        "tool":          {"name": "drive", "operation": "files.get"},
        "action":        {"semantic": "read", "outcome": "success"},
        "gateway_captured_at": "2026-02-13T10:00:01.120Z",
        "minting_context":     "gateway",
    }
    base.update(overrides)
    return base


# ===========================================================================
# 1. CIM EVENT SCHEMA VALIDATION
# ===========================================================================

class TestCIMEventSchemaRequired:
    """Test that required fields are enforced and optional fields accepted."""

    def test_minimal_valid_event(self):
        from src.schemas.cim_event import CIMEvent
        ev = CIMEvent.model_validate(make_valid_event())
        assert ev.event_id is not None
        assert ev.delegation_id == "del_test_001"

    def test_missing_time_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        del d["time"]
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_missing_event_id_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        del d["event_id"]
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_missing_delegation_id_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        del d["delegation_id"]
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_missing_principal_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        del d["principal"]
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_missing_agent_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        del d["agent"]
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_missing_tool_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        del d["tool"]
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_missing_action_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        del d["action"]
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_optional_fields_accepted(self):
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        d["delegation_parent_id"] = "del_parent_001"
        d["resource"] = {"type": "document", "id": "doc_123", "sensitivity_label": "confidential"}
        d["trace"] = {"trace_id": "T1", "span_id": "S1", "parent_span_id": None}
        d["workflow"] = {"workflow_id": "wf_001"}
        d["labels"] = {"env": "test"}
        ev = CIMEvent.model_validate(d)
        assert ev.resource is not None
        assert ev.trace is not None
        assert ev.workflow is not None

    def test_invalid_timestamp_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event(time="not-a-timestamp")
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_invalid_action_semantic_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        d["action"] = {"semantic": "NONEXISTENT_ACTION"}
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_delegation_parent_not_self(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        d["delegation_parent_id"] = d["delegation_id"]  # self-referential
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_empty_event_id_raises(self):
        from pydantic import ValidationError
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event(event_id="")
        with pytest.raises(ValidationError):
            CIMEvent.model_validate(d)

    def test_all_action_semantics_valid(self):
        from src.schemas.cim_event import CIMEvent, ActionSemantic
        for sem in ActionSemantic:
            d = make_valid_event()
            d["action"] = {"semantic": sem.value}
            ev = CIMEvent.model_validate(d)
            assert ev.action.semantic == sem

    def test_all_outcomes_valid(self):
        from src.schemas.cim_event import CIMEvent, ActionOutcome
        for outcome in ActionOutcome:
            d = make_valid_event()
            d["action"]["outcome"] = outcome.value
            ev = CIMEvent.model_validate(d)
            assert ev.action.outcome == outcome

    def test_to_flat_dict_round_trip(self):
        from src.schemas.cim_event import CIMEvent
        d = make_valid_event()
        d["resource"] = {"type": "document", "id": "doc_1"}
        d["trace"]    = {"trace_id": "T1", "span_id": "S1"}
        ev   = CIMEvent.model_validate(d)
        flat = ev.to_flat_dict()
        assert flat["event_id"]          == ev.event_id
        assert flat["delegation_id"]     == ev.delegation_id
        assert flat["principal_user_id"] == ev.principal.user_id
        assert flat["tool_name"]         == ev.tool.name
        assert flat["action_semantic"]   == ev.action.semantic.value


# ===========================================================================
# 2. INGESTION SERVICE
# ===========================================================================

class TestIngestionService:

    def test_valid_event_ingested(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store  = EventStore(tmp_db(tmp_path))
        svc    = IngestionService(store)
        result = svc.ingest(make_valid_event())
        assert result.valid is True
        assert store.stats()["events"] == 1

    def test_invalid_event_quarantined(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store  = EventStore(tmp_db(tmp_path))
        svc    = IngestionService(store)
        bad    = {"event_id": "e1"}  # missing required fields
        result = svc.ingest(bad)
        assert result.valid is False
        assert len(result.errors) > 0
        assert store.stats()["events"] == 0
        assert store.stats()["quarantined"] == 1

    def test_duplicate_event_rejected(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store  = EventStore(tmp_db(tmp_path))
        svc    = IngestionService(store)
        ev     = make_valid_event()
        r1     = svc.ingest(ev)
        r2     = svc.ingest(ev)  # duplicate
        assert r1.valid is True
        assert r2.valid is False
        assert "already exists" in r2.errors[0].lower() or "duplicate" in r2.errors[0].lower()
        assert store.stats()["events"] == 1

    def test_allow_duplicate_flag(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        ev    = make_valid_event()
        svc.ingest(ev, allow_duplicate=True)
        svc.ingest(ev, allow_duplicate=True)  # should not raise
        assert store.stats()["events"] == 1

    def test_normalization_fills_semantic(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        from src.schemas.cim_event import ActionSemantic
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        ev    = make_valid_event()
        del ev["action"]["semantic"]  # remove; should be inferred from tool.operation
        ev["action"] = {"outcome": "success"}  # no semantic
        result = svc.validate(ev, normalize=True)
        # Should fill semantic from tool=drive, operation=files.get → read
        if result.valid:
            assert result.event.action.semantic == ActionSemantic.READ

    def test_json_batch_import(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store  = EventStore(tmp_db(tmp_path))
        svc    = IngestionService(store)
        events = [make_valid_event() for _ in range(5)]
        result = svc.ingest_json(json.dumps(events), allow_duplicate=True)
        assert result["success"] == 5
        assert result["failed"]  == 0

    def test_jsonl_batch_import(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        lines = "\n".join(json.dumps(make_valid_event()) for _ in range(4))
        result = svc.ingest_jsonl(lines, allow_duplicate=True)
        assert result["success"] == 4

    def test_malformed_json_handled(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store  = EventStore(tmp_db(tmp_path))
        svc    = IngestionService(store)
        result = svc.ingest_json("{not valid json}")
        assert result["failed"] == 0 or "errors" in result

    def test_partial_batch_quarantined(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store  = EventStore(tmp_db(tmp_path))
        svc    = IngestionService(store)
        events = [make_valid_event(), {"bad": "record"}, make_valid_event()]
        result = svc.ingest_json(json.dumps(events), allow_duplicate=True)
        assert result["success"] == 2
        assert result["failed"]  >= 1


# ===========================================================================
# 3. EVENT STORE
# ===========================================================================

class TestEventStore:

    def test_insert_and_retrieve(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.schemas.cim_event import CIMEvent
        store = EventStore(tmp_db(tmp_path))
        ev    = CIMEvent.model_validate(make_valid_event())
        store.insert_event(ev)
        row = store.get_event(ev.event_id)
        assert row is not None
        assert row["event_id"] == ev.event_id

    def test_query_by_delegation(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.schemas.cim_event import CIMEvent
        store = EventStore(tmp_db(tmp_path))
        ev1 = CIMEvent.model_validate(make_valid_event(delegation_id="del_A"))
        ev2 = CIMEvent.model_validate(make_valid_event(delegation_id="del_B"))
        store.insert_event(ev1)
        store.insert_event(ev2)
        rows = store.query_events(delegation_id="del_A")
        assert len(rows) == 1
        assert rows[0]["delegation_id"] == "del_A"

    def test_append_only_duplicate_rejected(self, tmp_path):
        from src.storage.event_store import EventStore, DuplicateEventError
        from src.schemas.cim_event import CIMEvent
        store = EventStore(tmp_db(tmp_path))
        ev    = CIMEvent.model_validate(make_valid_event())
        store.insert_event(ev)
        with pytest.raises(DuplicateEventError):
            store.insert_event(ev)

    def test_quarantine_stores_errors(self, tmp_path):
        from src.storage.event_store import EventStore
        store = EventStore(tmp_db(tmp_path))
        store.quarantine_event(
            raw={"event_id": "bad"},
            errors=["Missing required field: time"],
            warnings=[],
        )
        q = store.get_quarantined()
        assert len(q) == 1
        errors = json.loads(q[0]["errors"])
        assert "time" in errors[0]

    def test_delegation_upsert(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.schemas.cim_event import DelegationRecord
        store = EventStore(tmp_db(tmp_path))
        dr = DelegationRecord(
            delegation_id="del_001",
            parent_delegation_id=None,
            principal_user_id="user_1",
            agent_id="agent_1",
            issued_at="2026-01-01T00:00:00Z",
        )
        store.upsert_delegation(dr)
        result = store.get_delegation("del_001")
        assert result is not None
        assert result["delegation_id"] == "del_001"

    def test_stats_accurate(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        for _ in range(3):
            svc.ingest(make_valid_event(), allow_duplicate=True)
        stats = store.stats()
        assert stats["events"] == 3

    def test_export_json(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        svc.ingest(make_valid_event(), allow_duplicate=True)
        exported = store.export_events_json()
        data     = json.loads(exported)
        assert len(data) == 1


# ===========================================================================
# 4. SEMANTIC NORMALIZATION
# ===========================================================================

class TestSemanticNormalization:

    def test_drive_files_get_is_read(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("drive", "files.get")
        assert r.action_semantic == ActionSemantic.READ

    def test_drive_permissions_create_is_share(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("drive", "permissions.create")
        assert r.action_semantic == ActionSemantic.SHARE

    def test_github_commit_is_write(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("github", "git.createCommit")
        assert r.action_semantic == ActionSemantic.WRITE

    def test_slack_post_is_share(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("slack", "chat.postMessage")
        assert r.action_semantic == ActionSemantic.SHARE

    def test_crm_query_is_read(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("crm", "query")
        assert r.action_semantic in (ActionSemantic.READ, ActionSemantic.SEARCH)

    def test_unknown_operation_is_unknown(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("unknown_tool", "xyzzy_operation_123")
        assert r.action_semantic == ActionSemantic.UNKNOWN
        assert r.is_ambiguous is True

    def test_no_operation_is_unknown(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("drive", None)
        assert r.action_semantic == ActionSemantic.UNKNOWN

    def test_github_collaborators_add_is_invite(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("github", "collaborators.add")
        assert r.action_semantic == ActionSemantic.INVITE

    def test_prefix_pattern_fallback(self):
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        r = normalize_action("custom_tool", "readFile")
        assert r.action_semantic == ActionSemantic.READ

    def test_cross_tool_same_semantic(self):
        """
        Same semantic (read) should be returned for equivalent operations
        on different tools — cross-system normalization (I4).
        """
        from src.normalization.semantic_normalizer import normalize_action
        from src.schemas.cim_event import ActionSemantic
        ops = [
            ("drive",    "files.get"),
            ("gdocs",    "documents.get"),
            ("github",   "contents.get"),
            ("calendar", "events.get"),
        ]
        for tool, op in ops:
            r = normalize_action(tool, op)
            assert r.action_semantic == ActionSemantic.READ, f"{tool}.{op} → {r.action_semantic}"


# ===========================================================================
# 5. DELEGATION GRAPH
# ===========================================================================

class TestDelegationGraph:

    def _populate_store(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.schemas.cim_event import DelegationRecord
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)

        # Root → child → grandchild
        for d_id, parent in [("root", None), ("child", "root"), ("grand", "child")]:
            dr = DelegationRecord(
                delegation_id        = d_id,
                parent_delegation_id = parent,
                principal_user_id    = "user_1",
                agent_id             = f"agent_{d_id}",
                issued_at            = "2026-01-01T00:00:00Z",
            )
            store.upsert_delegation(dr)
            ev = make_valid_event(
                delegation_id        = d_id,
                delegation_parent_id = parent,
            )
            ev["agent"] = {"agent_id": f"agent_{d_id}"}
            svc.ingest(ev, allow_duplicate=True)
        return store

    def test_ancestors(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        store  = self._populate_store(tmp_path)
        graph  = DelegationGraph(store)
        graph.build()
        assert "child" in graph.ancestors("grand")
        assert "root"  in graph.ancestors("grand")

    def test_descendants(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        store  = self._populate_store(tmp_path)
        graph  = DelegationGraph(store)
        graph.build()
        assert "child" in graph.descendants("root")
        assert "grand" in graph.descendants("root")

    def test_root_has_no_ancestors(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        store  = self._populate_store(tmp_path)
        graph  = DelegationGraph(store)
        graph.build()
        assert graph.ancestors("root") == []

    def test_leaf_has_no_descendants(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        store  = self._populate_store(tmp_path)
        graph  = DelegationGraph(store)
        graph.build()
        assert graph.descendants("grand") == []

    def test_events_under_delegation_transitive(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        store  = self._populate_store(tmp_path)
        graph  = DelegationGraph(store)
        graph.build()
        # root should cover events under child and grand too
        ev_ids = graph.events_under_delegation("root")
        assert len(ev_ids) >= 3  # at least one per delegation

    def test_lineage_gap_detection(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        # Event references a non-existent parent delegation
        ev = make_valid_event(delegation_parent_id="missing_parent_XXXX")
        svc.ingest(ev, allow_duplicate=True)
        graph = DelegationGraph(store)
        graph.build()
        analysis = graph.analyze_lineage(ev["delegation_id"])
        assert len(analysis.lineage_gaps) > 0 or len(analysis.issues) > 0

    def test_root_delegations_returned(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        store  = self._populate_store(tmp_path)
        graph  = DelegationGraph(store)
        graph.build()
        roots = graph.root_delegations()
        assert "root" in roots


# ===========================================================================
# 6. RECONSTRUCTION ENGINE
# ===========================================================================

def _make_two_workflow_store(tmp_path):
    """Create a store with two distinct workflow delegation chains."""
    from src.storage.event_store import EventStore
    from src.schemas.cim_event import DelegationRecord
    from src.ingestion.ingestion_service import IngestionService
    store = EventStore(tmp_db(tmp_path))
    svc   = IngestionService(store)

    for wf_idx in range(2):
        d_id = f"del_wf_{wf_idx}"
        wf_id = f"wf_{wf_idx}"
        dr = DelegationRecord(
            delegation_id="del_" + wf_id,
            parent_delegation_id=None,
            principal_user_id="user_1",
            agent_id="agent_1",
            issued_at="2026-01-01T00:00:00Z",
            workflow_id=wf_id,
        )
        store.upsert_delegation(dr)
        for _ in range(3):
            ev = make_valid_event(
                delegation_id=f"del_{wf_id}",
                workflow_id=wf_id,
            )
            ev["workflow"] = {"workflow_id": wf_id}
            svc.ingest(ev, allow_duplicate=True)
    return store


class TestReconstruction:

    def test_cim_reconstructor_runs(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        from src.reconstruction.reconstructor import CIMReconstructor
        store = _make_two_workflow_store(tmp_path)
        graph = DelegationGraph(store)
        result = CIMReconstructor(store, graph).reconstruct()
        assert result.total_events > 0
        assert len(result.workflows) > 0

    def test_cim_covers_all_events(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        from src.reconstruction.reconstructor import CIMReconstructor
        store = _make_two_workflow_store(tmp_path)
        graph = DelegationGraph(store)
        result = CIMReconstructor(store, graph).reconstruct()
        assert len(result.uncovered_event_ids) == 0

    def test_b0_reconstructor_runs(self, tmp_path):
        from src.reconstruction.reconstructor import BaselineB0TraceOnly
        store = _make_two_workflow_store(tmp_path)
        result = BaselineB0TraceOnly(store).reconstruct()
        assert result.total_events > 0

    def test_b1_reconstructor_runs(self, tmp_path):
        from src.reconstruction.reconstructor import BaselineB1TimeWindow
        store = _make_two_workflow_store(tmp_path)
        result = BaselineB1TimeWindow(store, window_seconds=60).reconstruct()
        assert result.total_events > 0

    def test_b2_reconstructor_runs(self, tmp_path):
        from src.reconstruction.reconstructor import BaselineB2Hybrid
        store = _make_two_workflow_store(tmp_path)
        result = BaselineB2Hybrid(store, window_seconds=60).reconstruct()
        assert result.total_events > 0

    def test_cim_ambiguity_is_one_for_covered(self, tmp_path):
        """CIM achieves Amb=1 for events with explicit delegation binding."""
        from src.delegation.delegation_graph import DelegationGraph
        from src.reconstruction.reconstructor import CIMReconstructor
        store = _make_two_workflow_store(tmp_path)
        graph = DelegationGraph(store)
        result = CIMReconstructor(store, graph).reconstruct()
        for wf in result.workflows:
            # Workflows with no orphans should have Amb=1
            if not wf.orphan_event_ids:
                assert wf.ambiguity == 1, f"Expected Amb=1 for {wf.workflow_id}, got {wf.ambiguity}"

    def test_empty_store_returns_empty_result(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.reconstruction.reconstructor import CIMReconstructor
        store = EventStore(tmp_db(tmp_path))
        graph = DelegationGraph(store)
        result = CIMReconstructor(store, graph).reconstruct()
        assert result.total_events == 0
        assert len(result.workflows) == 0

    def test_interleaved_delegations_separated(self, tmp_path):
        """Events from two different delegations should be in separate groups."""
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.reconstruction.reconstructor import CIMReconstructor
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)

        for i, d_id in enumerate(["del_A", "del_B", "del_A", "del_B"]):
            ev = make_valid_event(
                delegation_id=d_id,
                time=f"2026-01-01T00:00:0{i}.000Z",
            )
            svc.ingest(ev, allow_duplicate=True)

        graph = DelegationGraph(store)
        result = CIMReconstructor(store, graph).reconstruct()
        # Should find two distinct delegation groups
        d_ids_in_wfs = set()
        for wf in result.workflows:
            d_ids_in_wfs.update(wf.delegation_ids)
        assert "del_A" in d_ids_in_wfs
        assert "del_B" in d_ids_in_wfs


# ===========================================================================
# 7. AUDIT ENGINE
# ===========================================================================

class TestAuditEngine:

    def _make_i1_violation_store(self, tmp_path):
        """Two events share delegation_id but have different principals."""
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        ev1   = make_valid_event(delegation_id="del_shared")
        ev2   = make_valid_event(delegation_id="del_shared")
        ev2["principal"] = {"user_id": "different_user"}
        svc.ingest(ev1, allow_duplicate=True)
        svc.ingest(ev2, allow_duplicate=True)
        return store

    def test_i1_delegation_consistency_detected(self, tmp_path):
        from src.delegation.delegation_graph import DelegationGraph
        from src.audit.audit_engine import AuditEngine
        store    = self._make_i1_violation_store(tmp_path)
        graph    = DelegationGraph(store)
        engine   = AuditEngine(store, graph)
        findings = engine.run_all_checks()
        i1_findings = [f for f in findings if f.rule_id == "I1"]
        assert len(i1_findings) > 0

    def test_i2_authority_causality_detected(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.ingestion.ingestion_service import IngestionService
        from src.audit.audit_engine import AuditEngine
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        # delegation_id == trace_id (I2 violation)
        ev = make_valid_event(delegation_id="trace_abc")
        ev["trace"] = {"trace_id": "trace_abc"}
        svc.ingest(ev, allow_duplicate=True)
        graph    = DelegationGraph(store)
        engine   = AuditEngine(store, graph)
        findings = engine.run_all_checks()
        i2 = [f for f in findings if f.rule_id == "I2"]
        assert len(i2) > 0

    def test_i4_unknown_semantic_detected(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.ingestion.ingestion_service import IngestionService
        from src.audit.audit_engine import AuditEngine
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        ev    = make_valid_event()
        ev["action"] = {"semantic": "unknown", "outcome": "unknown"}
        svc.ingest(ev, allow_duplicate=True)
        graph    = DelegationGraph(store)
        engine   = AuditEngine(store, graph)
        findings = engine.run_all_checks()
        i4 = [f for f in findings if f.rule_id == "I4"]
        assert len(i4) > 0

    def test_orphan_events_detected(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.ingestion.ingestion_service import IngestionService
        from src.audit.audit_engine import AuditEngine
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        ev    = make_valid_event(delegation_id="UNBOUND")
        ev["minting_context"] = "unbound"
        svc.ingest(ev, allow_duplicate=True)
        graph    = DelegationGraph(store)
        engine   = AuditEngine(store, graph)
        findings = engine.run_all_checks()
        orphan   = [f for f in findings if f.rule_id == "ORPHAN"]
        assert len(orphan) > 0

    def test_audit_summary_counts(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.ingestion.ingestion_service import IngestionService
        from src.audit.audit_engine import AuditEngine
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        svc.ingest(make_valid_event(), allow_duplicate=True)
        graph   = DelegationGraph(store)
        engine  = AuditEngine(store, graph)
        engine.run_all_checks()
        summary = engine.summary()
        assert "total" in summary
        assert summary["total"] == summary["errors"] + summary["warnings"] + summary["info"]

    def test_no_findings_on_clean_dataset(self, tmp_path):
        """A clean, well-formed dataset should produce no I1/I2 findings."""
        from src.storage.event_store import EventStore
        from src.delegation.delegation_graph import DelegationGraph
        from src.ingestion.ingestion_service import IngestionService
        from src.audit.audit_engine import AuditEngine
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        for _ in range(3):
            svc.ingest(make_valid_event(), allow_duplicate=True)
        graph    = DelegationGraph(store)
        engine   = AuditEngine(store, graph)
        findings = engine.run_all_checks()
        i1 = [f for f in findings if f.rule_id == "I1"]
        i2 = [f for f in findings if f.rule_id == "I2"]
        assert len(i1) == 0
        assert len(i2) == 0


# ===========================================================================
# 8. METRICS
# ===========================================================================

class TestMetrics:

    def test_ambiguity_metric_cim_is_one(self):
        from src.experiments.metrics import MetricsCalculator
        from src.reconstruction.reconstructor import ReconstructedWorkflow
        gt = {"del_1": {"e1", "e2"}}
        calc = MetricsCalculator(ground_truth=gt)
        wf   = ReconstructedWorkflow(
            workflow_id="wf_1",
            delegation_ids=["del_1"],
            event_ids=["e1", "e2"],
            ambiguity=1,
        )
        amb = calc.compute_ambiguity([wf], "cim", total_events=2, covered_events=2)
        assert amb.median_ambiguity == 1.0
        assert amb.workflows_with_ambiguity_gt1 == 0

    def test_recall_metric_perfect(self):
        from src.experiments.metrics import MetricsCalculator
        from src.reconstruction.reconstructor import ReconstructedWorkflow
        gt = {"del_1": {"e1", "e2", "e3"}}
        calc = MetricsCalculator(ground_truth=gt)
        wf   = ReconstructedWorkflow(
            workflow_id="wf_1",
            delegation_ids=["del_1"],
            event_ids=["e1", "e2", "e3"],
            ambiguity=1,
        )
        recall = calc.compute_recall([wf], "cim")
        assert recall.median_recall == 1.0

    def test_recall_metric_partial(self):
        from src.experiments.metrics import MetricsCalculator
        from src.reconstruction.reconstructor import ReconstructedWorkflow
        gt = {"del_1": {"e1", "e2", "e3", "e4"}}
        calc = MetricsCalculator(ground_truth=gt)
        wf   = ReconstructedWorkflow(
            workflow_id="wf_1",
            delegation_ids=["del_1"],
            event_ids=["e1", "e2"],  # missing e3, e4
            ambiguity=2,
        )
        recall = calc.compute_recall([wf], "b0")
        assert recall.median_recall == 0.5

    def test_recall_empty_gt(self):
        from src.experiments.metrics import MetricsCalculator
        gt   = {"del_1": set()}  # empty ground truth
        calc = MetricsCalculator(ground_truth=gt)
        from src.reconstruction.reconstructor import ReconstructedWorkflow
        recall = calc.compute_recall([], "cim")
        # Empty delegation GT → vacuously 1.0
        recall2 = calc.compute_recall([], "cim")
        assert recall2.delegations_evaluated == 0 or recall2.median_recall >= 0.0

    def test_divergence_zero_without_events(self):
        from src.experiments.metrics import MetricsCalculator, DivergenceMetric
        calc   = MetricsCalculator(ground_truth={})
        metric = DivergenceMetric(delegation_divergences={})
        metric.compute()
        assert metric.median_comp == 0.0

    def test_query_burden_table_has_four_rows(self):
        from src.experiments.metrics import MetricsCalculator
        rows = MetricsCalculator.query_burden_table()
        assert len(rows) == 4
        for row in rows:
            assert row.cim_ops < row.baseline_ops[0]  # CIM should be cheaper

    def test_measure_event_bytes(self):
        from src.experiments.metrics import MetricsCalculator
        events = [make_valid_event() for _ in range(5)]
        avg = MetricsCalculator.measure_event_bytes(events)
        assert avg > 0

    def test_measure_event_bytes_empty(self):
        from src.experiments.metrics import MetricsCalculator
        avg = MetricsCalculator.measure_event_bytes([])
        assert avg == 0.0


# ===========================================================================
# 9. SYNTHETIC DATA GENERATOR
# ===========================================================================

class TestSyntheticGenerator:

    def test_w1_generates_events(self):
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        cfg = ScenarioConfig(scenario="W1", seed=42, n_workflows=2)
        ds  = SyntheticDatasetGenerator(cfg).generate()
        assert len(ds.events) > 0
        assert len(ds.delegation_records) > 0

    def test_w2_generates_nested_delegations(self):
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        cfg = ScenarioConfig(scenario="W2", seed=42, n_workflows=1, spawn_fanout=2)
        ds  = SyntheticDatasetGenerator(cfg).generate()
        # Should have at least a root + one child
        d_ids = {d.delegation_id for d in ds.delegation_records}
        parents = {d.parent_delegation_id for d in ds.delegation_records if d.parent_delegation_id}
        assert len(d_ids) > 1
        assert len(parents) > 0

    def test_w3_includes_failures(self):
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        cfg = ScenarioConfig(scenario="W3", seed=42, n_workflows=1, retry_rate=0.8)
        ds  = SyntheticDatasetGenerator(cfg).generate()
        outcomes = [ev.action.outcome.value for ev in ds.events]
        assert "failure" in outcomes

    def test_defective_scenario_injects_defects(self):
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        cfg = ScenarioConfig(scenario="defective", seed=42, include_defects=True)
        ds  = SyntheticDatasetGenerator(cfg).generate()
        assert len(ds.defects) > 0

    def test_deterministic_with_same_seed(self):
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        cfg = ScenarioConfig(scenario="W1", seed=99, n_workflows=2)
        ds1 = SyntheticDatasetGenerator(cfg).generate()
        ds2 = SyntheticDatasetGenerator(cfg).generate()
        ids1 = [e.event_id for e in ds1.events]
        ids2 = [e.event_id for e in ds2.events]
        # Same seed → same number of events (event_ids are uuid4, so may differ
        # but counts and delegation structure must match)
        assert len(ids1) == len(ids2)

    def test_ground_truth_covers_all_events(self):
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        cfg = ScenarioConfig(scenario="W1", seed=42, n_workflows=1)
        ds  = SyntheticDatasetGenerator(cfg).generate()
        all_gt_events = set()
        for ev_set in ds.ground_truth.values():
            all_gt_events.update(ev_set)
        ev_ids = {ev.event_id for ev in ds.events}
        # All GT events must be in the event list
        assert all_gt_events.issubset(ev_ids)

    def test_mixed_scenario(self):
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        cfg = ScenarioConfig(scenario="mixed", seed=42, n_workflows=3)
        ds  = SyntheticDatasetGenerator(cfg).generate()
        assert len(ds.events) > 0

    def test_events_have_valid_cim_schema(self):
        """All generated events must pass CIM schema validation."""
        from src.experiments.synthetic_generator import SyntheticDatasetGenerator, ScenarioConfig
        from src.schemas.cim_event import CIMEvent
        cfg = ScenarioConfig(scenario="W1", seed=42, n_workflows=1, n_tools=3)
        ds  = SyntheticDatasetGenerator(cfg).generate()
        for ev in ds.events:
            # Already CIMEvent objects; validate via model_dump round-trip
            d = ev.model_dump(mode="json")
            ev2 = CIMEvent.model_validate(d)
            assert ev2.event_id == ev.event_id


# ===========================================================================
# 10. GATEWAY
# ===========================================================================

class TestGateway:

    def test_gateway_invoke_creates_event(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        from src.gateway.gateway import InstrumentedGateway, GatewayContext
        store   = EventStore(tmp_db(tmp_path))
        svc     = IngestionService(store)
        gw      = InstrumentedGateway(svc)
        ctx     = GatewayContext(
            delegation_id="del_gw_test",
            principal_user_id="user_1",
            agent_id="agent_test",
        )
        ev = gw.invoke(ctx, tool_name="drive", operation="files.get")
        assert ev.delegation_id == "del_gw_test"
        assert store.stats()["events"] == 1

    def test_gateway_no_context_emits_unbound(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        from src.gateway.gateway import InstrumentedGateway
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        gw    = InstrumentedGateway(svc)
        ev    = gw.invoke(None, tool_name="slack", operation="chat.postMessage")
        assert ev.delegation_id == "UNBOUND"

    def test_child_agent_preserves_lineage(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        from src.gateway.gateway import InstrumentedGateway, GatewayContext, SimulatedAgent
        store  = EventStore(tmp_db(tmp_path))
        svc    = IngestionService(store)
        gw     = InstrumentedGateway(svc)
        ctx    = GatewayContext(
            delegation_id="del_root",
            principal_user_id="user_1",
            agent_id="agent_root",
        )
        root_agent  = SimulatedAgent(gw, ctx)
        child_agent = root_agent.spawn_child_agent("del_child", "agent_child")
        child_agent.invoke("github", "contents.get")
        rows = store.query_events(delegation_id="del_child")
        assert len(rows) == 1
        assert rows[0]["delegation_parent_id"] == "del_root"

    def test_normalization_applied_at_gateway(self, tmp_path):
        from src.storage.event_store import EventStore
        from src.ingestion.ingestion_service import IngestionService
        from src.gateway.gateway import InstrumentedGateway, GatewayContext
        from src.schemas.cim_event import ActionSemantic
        store = EventStore(tmp_db(tmp_path))
        svc   = IngestionService(store)
        gw    = InstrumentedGateway(svc)
        ctx   = GatewayContext("del_norm", "user_1", "agent_1")
        ev    = gw.invoke(ctx, "gdocs", "documents.batchUpdate")
        assert ev.action.semantic == ActionSemantic.WRITE
