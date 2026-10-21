"""
SQLite-backed persistent event store.

Design goals:
- Append-only event log (no silent overwrites on conflict).
- Queryable by event_id, delegation_id, agent_id, principal, tool, time, etc.
- Stores both required and optional CIM fields.
- Preserves gateway provenance metadata separate from agent-provided hints.
- Delegation records stored in a separate table.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from src.schemas.cim_event import (
    CIMEvent,
    DelegationRecord,
)


_SCHEMA_VERSION = 1

_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS cim_events (
    event_id              TEXT PRIMARY KEY,
    time                  TEXT NOT NULL,
    delegation_id         TEXT NOT NULL,
    delegation_parent_id  TEXT,
    principal_user_id     TEXT NOT NULL,
    agent_id              TEXT NOT NULL,
    agent_instance_id     TEXT,
    tool_name             TEXT NOT NULL,
    tool_operation        TEXT,
    action_semantic       TEXT NOT NULL,
    action_outcome        TEXT,
    action_direction      TEXT,
    action_principal_scope TEXT,
    resource_type         TEXT,
    resource_id           TEXT,
    resource_uri          TEXT,
    resource_sensitivity  TEXT,
    target_principal_id   TEXT,
    trace_id              TEXT,
    span_id               TEXT,
    parent_span_id        TEXT,
    workflow_id           TEXT,
    labels                TEXT,
    gateway_captured_at   TEXT,
    gateway_session_id    TEXT,
    minting_context       TEXT,
    ingested_at           TEXT NOT NULL,
    raw_json              TEXT
);
"""

_CREATE_DELEGATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS delegations (
    delegation_id         TEXT PRIMARY KEY,
    parent_delegation_id  TEXT,
    principal_user_id     TEXT NOT NULL,
    agent_id              TEXT NOT NULL,
    issued_at             TEXT NOT NULL,
    closed_at             TEXT,
    scope_description     TEXT,
    authority_envelope    TEXT,
    workflow_id           TEXT,
    labels                TEXT,
    created_at            TEXT NOT NULL
);
"""

_CREATE_QUARANTINE_TABLE = """
CREATE TABLE IF NOT EXISTS quarantine (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ingested_at TEXT NOT NULL,
    raw_json    TEXT NOT NULL,
    errors      TEXT NOT NULL,
    warnings    TEXT
);
"""

_CREATE_META_TABLE = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_delegation ON cim_events(delegation_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_principal  ON cim_events(principal_user_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_agent      ON cim_events(agent_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_tool       ON cim_events(tool_name);",
    "CREATE INDEX IF NOT EXISTS idx_events_time       ON cim_events(time);",
    "CREATE INDEX IF NOT EXISTS idx_events_workflow   ON cim_events(workflow_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_trace      ON cim_events(trace_id);",
    "CREATE INDEX IF NOT EXISTS idx_delegations_parent ON delegations(parent_delegation_id);",
    "CREATE INDEX IF NOT EXISTS idx_delegations_principal ON delegations(principal_user_id);",
]


class DuplicateEventError(Exception):
    """Raised when an event_id already exists in the store."""


class EventStore:
    """
    Persistent, append-only SQLite-backed store for CIM events and delegation records.
    """

    def __init__(self, db_path: str | Path = "cim_events.db") -> None:
        self.db_path = Path(db_path)
        self._init_db()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(_CREATE_EVENTS_TABLE)
            conn.execute(_CREATE_DELEGATIONS_TABLE)
            conn.execute(_CREATE_QUARANTINE_TABLE)
            conn.execute(_CREATE_META_TABLE)
            for idx in _INDEXES:
                conn.execute(idx)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)),
            )
            conn.commit()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Event ingestion
    # ------------------------------------------------------------------

    def insert_event(
        self,
        event: CIMEvent,
        raw_json: Optional[str] = None,
        allow_duplicate: bool = False,
    ) -> None:
        """
        Insert a validated CIMEvent into the store.

        Raises DuplicateEventError if the event_id already exists
        (unless allow_duplicate=True, which is for import idempotency).
        """
        flat = event.to_flat_dict()
        flat["ingested_at"] = datetime.now(timezone.utc).isoformat()
        flat["raw_json"]    = raw_json

        with self._conn() as conn:
            existing = conn.execute(
                "SELECT event_id FROM cim_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing:
                if allow_duplicate:
                    return
                raise DuplicateEventError(
                    f"Event '{event.event_id}' already exists in the store. "
                    "The audit log is append-only; duplicate event IDs are rejected."
                )
            conn.execute(
                """
                INSERT INTO cim_events (
                    event_id, time, delegation_id, delegation_parent_id,
                    principal_user_id, agent_id, agent_instance_id,
                    tool_name, tool_operation, action_semantic, action_outcome,
                    action_direction, action_principal_scope,
                    resource_type, resource_id, resource_uri, resource_sensitivity,
                    target_principal_id, trace_id, span_id, parent_span_id,
                    workflow_id, labels, gateway_captured_at, gateway_session_id,
                    minting_context, ingested_at, raw_json
                ) VALUES (
                    :event_id, :time, :delegation_id, :delegation_parent_id,
                    :principal_user_id, :agent_id, :agent_instance_id,
                    :tool_name, :tool_operation, :action_semantic, :action_outcome,
                    :action_direction, :action_principal_scope,
                    :resource_type, :resource_id, :resource_uri, :resource_sensitivity,
                    :target_principal_id, :trace_id, :span_id, :parent_span_id,
                    :workflow_id, :labels, :gateway_captured_at, :gateway_session_id,
                    :minting_context, :ingested_at, :raw_json
                )
                """,
                flat,
            )
            conn.commit()

    def quarantine_event(
        self,
        raw: dict[str, Any],
        errors: list[str],
        warnings: list[str] | None = None,
    ) -> None:
        """Store a rejected/malformed event in the quarantine table with diagnostics."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO quarantine(ingested_at, raw_json, errors, warnings)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now,
                    json.dumps(raw, default=str),
                    json.dumps(errors),
                    json.dumps(warnings or []),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Delegation records
    # ------------------------------------------------------------------

    def upsert_delegation(self, delegation: DelegationRecord) -> None:
        """Insert or update a delegation record."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO delegations (
                    delegation_id, parent_delegation_id, principal_user_id,
                    agent_id, issued_at, closed_at, scope_description,
                    authority_envelope, workflow_id, labels, created_at
                ) VALUES (
                    :delegation_id, :parent_delegation_id, :principal_user_id,
                    :agent_id, :issued_at, :closed_at, :scope_description,
                    :authority_envelope, :workflow_id, :labels, :created_at
                )
                ON CONFLICT(delegation_id) DO UPDATE SET
                    closed_at          = excluded.closed_at,
                    scope_description  = excluded.scope_description,
                    authority_envelope = excluded.authority_envelope,
                    workflow_id        = excluded.workflow_id,
                    labels             = excluded.labels
                """,
                {
                    "delegation_id":        delegation.delegation_id,
                    "parent_delegation_id": delegation.parent_delegation_id,
                    "principal_user_id":    delegation.principal_user_id,
                    "agent_id":             delegation.agent_id,
                    "issued_at":            delegation.issued_at,
                    "closed_at":            delegation.closed_at,
                    "scope_description":    delegation.scope_description,
                    "authority_envelope":   json.dumps(delegation.authority_envelope) if delegation.authority_envelope else None,
                    "workflow_id":          delegation.workflow_id,
                    "labels":               json.dumps(delegation.labels) if delegation.labels else None,
                    "created_at":           now,
                },
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cim_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return dict(row) if row else None

    def query_events(
        self,
        delegation_id:   Optional[str] = None,
        principal_user_id: Optional[str] = None,
        agent_id:        Optional[str] = None,
        tool_name:       Optional[str] = None,
        action_semantic: Optional[str] = None,
        workflow_id:     Optional[str] = None,
        trace_id:        Optional[str] = None,
        time_from:       Optional[str] = None,
        time_to:         Optional[str] = None,
        limit:           int = 10_000,
    ) -> list[dict[str, Any]]:
        """Flexible query with optional filters. Returns list of row dicts."""
        clauses: list[str] = []
        params:  list[Any] = []

        if delegation_id:
            clauses.append("delegation_id = ?"); params.append(delegation_id)
        if principal_user_id:
            clauses.append("principal_user_id = ?"); params.append(principal_user_id)
        if agent_id:
            clauses.append("agent_id = ?"); params.append(agent_id)
        if tool_name:
            clauses.append("tool_name = ?"); params.append(tool_name)
        if action_semantic:
            clauses.append("action_semantic = ?"); params.append(action_semantic)
        if workflow_id:
            clauses.append("workflow_id = ?"); params.append(workflow_id)
        if trace_id:
            clauses.append("trace_id = ?"); params.append(trace_id)
        if time_from:
            clauses.append("time >= ?"); params.append(time_from)
        if time_to:
            clauses.append("time <= ?"); params.append(time_to)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM cim_events {where} ORDER BY time ASC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def all_events(self, limit: int = 50_000) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cim_events ORDER BY time ASC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def all_delegations(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM delegations ORDER BY issued_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_delegation(self, delegation_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM delegations WHERE delegation_id = ?", (delegation_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_quarantined(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM quarantine ORDER BY ingested_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            ev_count  = conn.execute("SELECT COUNT(*) FROM cim_events").fetchone()[0]
            del_count = conn.execute("SELECT COUNT(*) FROM delegations").fetchone()[0]
            q_count   = conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
            principal_count = conn.execute(
                "SELECT COUNT(DISTINCT principal_user_id) FROM cim_events"
            ).fetchone()[0]
            agent_count = conn.execute(
                "SELECT COUNT(DISTINCT agent_id) FROM cim_events"
            ).fetchone()[0]
            tool_count = conn.execute(
                "SELECT COUNT(DISTINCT tool_name) FROM cim_events"
            ).fetchone()[0]
            workflow_count = conn.execute(
                "SELECT COUNT(DISTINCT workflow_id) FROM cim_events WHERE workflow_id IS NOT NULL"
            ).fetchone()[0]
        return {
            "events":         ev_count,
            "delegations":    del_count,
            "quarantined":    q_count,
            "principals":     principal_count,
            "agents":         agent_count,
            "tools":          tool_count,
            "workflows":      workflow_count,
        }

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def export_events_json(self, delegation_id: Optional[str] = None) -> str:
        rows = self.query_events(delegation_id=delegation_id) if delegation_id else self.all_events()
        return json.dumps(rows, default=str, indent=2)

    def clear_all(self) -> None:
        """DESTRUCTIVE: remove all data. Used only in test fixtures."""
        with self._conn() as conn:
            conn.execute("DELETE FROM cim_events")
            conn.execute("DELETE FROM delegations")
            conn.execute("DELETE FROM quarantine")
            conn.commit()
