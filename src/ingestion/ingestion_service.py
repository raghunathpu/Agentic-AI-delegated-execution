"""
Event validation and ingestion service.

Responsibilities:
- Validate raw event dicts against the CIM schema.
- Provide readable error messages for malformed records.
- Normalize tool operations to CIM semantic actions.
- Insert validated events into the persistent store.
- Quarantine invalid records with diagnostic information.
- Detect and reject duplicate event IDs.
- Support JSON and JSONL import.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import ValidationError

from src.schemas.cim_event import (
    CIMEvent,
    ValidationResult,
    ActionSemantic,
    ActionOutcome,
)
from src.storage.event_store import EventStore, DuplicateEventError
from src.normalization.semantic_normalizer import normalize_action, infer_direction


class IngestionService:
    """
    Validates, normalizes, and ingests CIM events.

    The service distinguishes:
    - Failed execution: the tool invocation itself failed (action.outcome=failure).
    - Malformed telemetry: the event record is structurally invalid.
    - Missing evidence: required fields are absent (event is quarantined).
    - Unknown outcome: the result of the action is not captured.
    """

    def __init__(self, store: EventStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Single event ingestion
    # ------------------------------------------------------------------

    def ingest(
        self,
        raw: dict[str, Any],
        allow_duplicate: bool = False,
        normalize: bool = True,
    ) -> ValidationResult:
        """
        Validate and ingest a single raw event dict.

        Returns a ValidationResult. If invalid, the event is quarantined.
        If valid but duplicate, a DuplicateEventError is raised (unless
        allow_duplicate=True).
        """
        result = self.validate(raw, normalize=normalize)
        if not result.valid:
            self.store.quarantine_event(
                raw=raw,
                errors=result.errors,
                warnings=result.warnings,
            )
            return result

        assert result.event is not None
        raw_json = json.dumps(raw, default=str)

        try:
            self.store.insert_event(
                event=result.event,
                raw_json=raw_json,
                allow_duplicate=allow_duplicate,
            )
        except DuplicateEventError as e:
            result.valid = False
            result.errors = [str(e)]
            self.store.quarantine_event(
                raw=raw,
                errors=result.errors,
                warnings=result.warnings,
            )
        return result

    def validate(
        self,
        raw: dict[str, Any],
        normalize: bool = True,
    ) -> ValidationResult:
        """
        Validate a raw dict against the CIM schema WITHOUT writing to the store.

        If normalize=True, attempt to fill action.semantic from tool.operation
        when action.semantic is missing.
        """
        errors: list[str]   = []
        warnings: list[str] = []

        # Pre-validation: type check
        if not isinstance(raw, dict):
            return ValidationResult(
                valid=False,
                errors=["Input must be a JSON object (dict), got: " + type(raw).__name__],
                raw=raw,
            )

        # Attempt normalization before schema validation
        processed = dict(raw)
        if normalize:
            processed, norm_warnings = self._apply_normalization(processed)
            warnings.extend(norm_warnings)

        # Schema validation
        try:
            event = CIMEvent.model_validate(processed)
        except ValidationError as exc:
            for err in exc.errors():
                loc  = " → ".join(str(l) for l in err["loc"])
                msg  = err["msg"]
                errors.append(f"[{loc}] {msg}")
            return ValidationResult(
                valid=False,
                errors=errors,
                warnings=warnings,
                raw=raw,
            )
        except Exception as exc:
            return ValidationResult(
                valid=False,
                errors=[f"Unexpected validation error: {exc}"],
                warnings=warnings,
                raw=raw,
            )

        # Post-schema semantic checks
        post_warnings = self._semantic_checks(event)
        warnings.extend(post_warnings)

        return ValidationResult(
            valid=True,
            event=event,
            errors=[],
            warnings=warnings,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Batch ingestion
    # ------------------------------------------------------------------

    def ingest_json(self, json_str: str, allow_duplicate: bool = True) -> dict[str, Any]:
        """
        Ingest events from a JSON string (list of event dicts).
        Returns a summary dict with counts.
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return {"success": 0, "failed": 0, "errors": [f"JSON parse error: {e}"]}

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return {"success": 0, "failed": 0, "errors": ["Expected a JSON array or object"]}

        return self._ingest_batch(data, allow_duplicate=allow_duplicate)

    def ingest_jsonl(self, jsonl_str: str, allow_duplicate: bool = True) -> dict[str, Any]:
        """Ingest events from a JSONL string (one event per line)."""
        records: list[dict] = []
        parse_errors: list[str] = []
        for i, line in enumerate(jsonl_str.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except json.JSONDecodeError as e:
                parse_errors.append(f"Line {i}: {e}")
        result = self._ingest_batch(records, allow_duplicate=allow_duplicate)
        result["parse_errors"] = parse_errors
        return result

    def _ingest_batch(
        self,
        records: list[dict],
        allow_duplicate: bool,
    ) -> dict[str, Any]:
        success = 0
        failed  = 0
        errors: list[str] = []
        for rec in records:
            result = self.ingest(rec, allow_duplicate=allow_duplicate)
            if result.valid:
                success += 1
            else:
                failed  += 1
                ev_id = rec.get("event_id", "<unknown>")
                errors.append(f"event_id={ev_id!r}: {'; '.join(result.errors)}")
        return {"success": success, "failed": failed, "errors": errors}

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    def _apply_normalization(
        self,
        raw: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Attempt to fill action.semantic from tool.operation when missing.
        Returns (processed_dict, warnings).
        """
        warnings: list[str] = []
        raw = dict(raw)  # shallow copy

        action = raw.get("action", {})
        tool   = raw.get("tool", {})

        if isinstance(action, dict) and isinstance(tool, dict):
            tool_name  = tool.get("name", "")
            operation  = tool.get("operation")
            semantic   = action.get("semantic")

            if not semantic and tool_name and operation:
                norm = normalize_action(tool_name, operation)
                action = dict(action)
                action["semantic"] = norm.action_semantic.value
                if norm.is_ambiguous:
                    action.setdefault("outcome", ActionOutcome.UNKNOWN.value)
                    warnings.append(
                        f"Ambiguous normalization for {tool_name}.{operation}: "
                        f"{norm.action_semantic.value} (matched by {norm.matched_by}). "
                        "Review action.semantic and action.outcome."
                    )
                raw["action"] = action

            # Infer direction if missing
            tier2 = action.get("tier2", {}) or {}
            if isinstance(tier2, dict) and not tier2.get("direction"):
                direction = infer_direction(
                    tool_name,
                    operation,
                    raw.get("resource", {}).get("type") if isinstance(raw.get("resource"), dict) else None,
                    raw.get("target_principal_id"),
                )
                if direction:
                    tier2 = dict(tier2)
                    tier2["direction"] = direction.value
                    action = dict(action)
                    action["tier2"] = tier2
                    raw["action"] = action

        return raw, warnings

    def _semantic_checks(self, event: CIMEvent) -> list[str]:
        """Additional semantic consistency checks after schema validation."""
        warnings: list[str] = []

        # Check: share/invoke should ideally have direction
        if event.action.semantic in (ActionSemantic.SHARE, ActionSemantic.INVOKE):
            if not event.action.tier2 or not event.action.tier2.direction:
                warnings.append(
                    f"action.semantic={event.action.semantic.value!r} but "
                    "action.tier2.direction is missing; externalization queries may be incomplete."
                )

        # Check: resource is absent but semantic implies a resource target
        resource_verbs = {
            ActionSemantic.READ, ActionSemantic.WRITE, ActionSemantic.DELETE,
            ActionSemantic.SHARE, ActionSemantic.MOVE,
        }
        if event.action.semantic in resource_verbs and not event.resource:
            warnings.append(
                "action.semantic implies a resource target but resource field is absent; "
                "blast-radius queries will be incomplete for this event."
            )

        # Check: share with no target principal
        if event.action.semantic == ActionSemantic.SHARE and not event.target_principal_id:
            warnings.append(
                "action.semantic=share but target_principal_id is absent; "
                "externalization provenance (Q3) cannot be answered for this event."
            )

        return warnings
