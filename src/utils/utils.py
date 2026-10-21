"""
Utility functions shared across modules.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """Return current UTC time as RFC3339 string."""
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path: str | Path) -> Any:
    """Load a JSON file and return its content."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(data: Any, path: str | Path, indent: int = 2) -> None:
    """Save data to a JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str, indent=indent)


def truncate(s: str, max_len: int = 80) -> str:
    """Truncate a string for display."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def format_finding_for_display(finding: Any) -> dict[str, str]:
    """Format an AuditFinding for tabular display."""
    return {
        "finding_id":    finding.finding_id[:8],
        "rule_id":       finding.rule_id,
        "rule_name":     finding.rule_name,
        "severity":      finding.severity,
        "status":        finding.status,
        "delegation_id": finding.delegation_id or "—",
        "events_count":  str(len(finding.event_ids)),
        "explanation":   truncate(finding.explanation, 120),
        "confirmed":     "✓" if finding.is_confirmed else "○",
    }
