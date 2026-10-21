"""
Semantic action normalization — Appendix B.2.

CIM defines a cross-tool action vocabulary to enable consistent querying
across heterogeneous systems.

Normalization is performed using tool-native metadata (operation/method name,
endpoint class, and resource type), NOT payload content.

When a mapping is ambiguous, implementations may emit both tool.operation
and action.semantic and record action.outcome=unknown until disambiguated.
"""

from __future__ import annotations

from typing import Optional
from src.schemas.cim_event import ActionSemantic, ActionDirection, ResourceType


# ---------------------------------------------------------------------------
# Normalization taxonomy (Appendix B.2 cross-tool action vocabulary)
# ---------------------------------------------------------------------------

# Maps (tool_name, operation_pattern) → ActionSemantic
# Pattern matching: prefix match, substring match, or exact match.
#
# Per paper: operations named get, download, readFile map to read;
# operations named update, commit, edit map to write;
# operations named addMember, setPermission, createLink map to share.

_TOOL_OPERATION_MAP: dict[str, dict[str, ActionSemantic]] = {
    # --- Google Drive / Docs / Sheets ----
    "drive": {
        "files.get":      ActionSemantic.READ,
        "files.list":     ActionSemantic.ENUMERATE,
        "files.download": ActionSemantic.READ,
        "files.copy":     ActionSemantic.READ,
        "files.create":   ActionSemantic.WRITE,
        "files.update":   ActionSemantic.WRITE,
        "files.delete":   ActionSemantic.DELETE,
        "files.move":     ActionSemantic.MOVE,
        "permissions.create": ActionSemantic.SHARE,
        "permissions.delete": ActionSemantic.REVOKE,
        "permissions.list":   ActionSemantic.ENUMERATE,
    },
    "gdocs": {
        "documents.get":    ActionSemantic.READ,
        "documents.create": ActionSemantic.WRITE,
        "documents.batchUpdate": ActionSemantic.WRITE,
    },
    "gsheets": {
        "spreadsheets.get":    ActionSemantic.READ,
        "spreadsheets.create": ActionSemantic.WRITE,
        "spreadsheets.values.get":    ActionSemantic.READ,
        "spreadsheets.values.update": ActionSemantic.WRITE,
        "spreadsheets.values.append": ActionSemantic.WRITE,
    },
    # --- GitHub ----
    "github": {
        "repos.get":           ActionSemantic.READ,
        "repos.list":          ActionSemantic.ENUMERATE,
        "contents.get":        ActionSemantic.READ,
        "contents.createOrUpdate": ActionSemantic.WRITE,
        "contents.delete":     ActionSemantic.DELETE,
        "issues.get":          ActionSemantic.READ,
        "issues.create":       ActionSemantic.WRITE,
        "issues.update":       ActionSemantic.WRITE,
        "pulls.create":        ActionSemantic.INVOKE,
        "pulls.merge":         ActionSemantic.INVOKE,
        "actions.createWorkflowDispatch": ActionSemantic.INVOKE,
        "collaborators.add":   ActionSemantic.INVITE,
        "collaborators.remove": ActionSemantic.REVOKE,
        "collaborators.list":  ActionSemantic.ENUMERATE,
        "commits.get":         ActionSemantic.READ,
        "git.createCommit":    ActionSemantic.WRITE,
    },
    # --- Slack ----
    "slack": {
        "conversations.list":    ActionSemantic.ENUMERATE,
        "conversations.history": ActionSemantic.READ,
        "conversations.members": ActionSemantic.ENUMERATE,
        "chat.postMessage":      ActionSemantic.SHARE,
        "files.upload":          ActionSemantic.SHARE,
        "users.list":            ActionSemantic.ENUMERATE,
        "conversations.create":  ActionSemantic.WRITE,
        "conversations.invite":  ActionSemantic.INVITE,
        "conversations.kick":    ActionSemantic.REVOKE,
    },
    # --- CRM / Salesforce ----
    "crm": {
        "query":              ActionSemantic.READ,
        "search":             ActionSemantic.SEARCH,
        "create":             ActionSemantic.WRITE,
        "update":             ActionSemantic.WRITE,
        "delete":             ActionSemantic.DELETE,
        "describe":           ActionSemantic.READ,
        "upsert":             ActionSemantic.WRITE,
        "getRecentItems":     ActionSemantic.READ,
        "listViewResults":    ActionSemantic.ENUMERATE,
    },
    # --- Search engines (web search, internal search) ----
    "search": {
        "web_search":     ActionSemantic.SEARCH,
        "search":         ActionSemantic.SEARCH,
        "query":          ActionSemantic.SEARCH,
        "semantic_search": ActionSemantic.SEARCH,
    },
    # --- Email ----
    "email": {
        "send":       ActionSemantic.SHARE,
        "get":        ActionSemantic.READ,
        "list":       ActionSemantic.ENUMERATE,
        "delete":     ActionSemantic.DELETE,
        "forward":    ActionSemantic.SHARE,
        "reply":      ActionSemantic.SHARE,
    },
    # --- Calendar ----
    "calendar": {
        "events.get":    ActionSemantic.READ,
        "events.list":   ActionSemantic.ENUMERATE,
        "events.insert": ActionSemantic.WRITE,
        "events.update": ActionSemantic.WRITE,
        "events.delete": ActionSemantic.DELETE,
        "acl.insert":    ActionSemantic.SHARE,
        "acl.delete":    ActionSemantic.REVOKE,
    },
    # --- Generic file system ----
    "filesystem": {
        "read":    ActionSemantic.READ,
        "write":   ActionSemantic.WRITE,
        "delete":  ActionSemantic.DELETE,
        "list":    ActionSemantic.ENUMERATE,
        "move":    ActionSemantic.MOVE,
        "copy":    ActionSemantic.READ,
        "chmod":   ActionSemantic.PERMISSION_CHANGE,
    },
    # --- Database ----
    "database": {
        "SELECT":   ActionSemantic.READ,
        "INSERT":   ActionSemantic.WRITE,
        "UPDATE":   ActionSemantic.WRITE,
        "DELETE":   ActionSemantic.DELETE,
        "CREATE":   ActionSemantic.WRITE,
        "DROP":     ActionSemantic.DELETE,
        "GRANT":    ActionSemantic.SHARE,
        "REVOKE":   ActionSemantic.REVOKE,
    },
}

# Prefix patterns: when exact match fails, try these prefixes
_PREFIX_PATTERNS: list[tuple[str, ActionSemantic]] = [
    ("get",        ActionSemantic.READ),
    ("read",       ActionSemantic.READ),
    ("list",       ActionSemantic.ENUMERATE),
    ("search",     ActionSemantic.SEARCH),
    ("query",      ActionSemantic.SEARCH),
    ("fetch",      ActionSemantic.READ),
    ("download",   ActionSemantic.READ),
    ("create",     ActionSemantic.WRITE),
    ("insert",     ActionSemantic.WRITE),
    ("update",     ActionSemantic.WRITE),
    ("edit",       ActionSemantic.WRITE),
    ("commit",     ActionSemantic.WRITE),
    ("write",      ActionSemantic.WRITE),
    ("upload",     ActionSemantic.WRITE),
    ("upsert",     ActionSemantic.WRITE),
    ("delete",     ActionSemantic.DELETE),
    ("remove",     ActionSemantic.DELETE),
    ("drop",       ActionSemantic.DELETE),
    ("move",       ActionSemantic.MOVE),
    ("rename",     ActionSemantic.MOVE),
    ("share",      ActionSemantic.SHARE),
    ("post",       ActionSemantic.SHARE),
    ("send",       ActionSemantic.SHARE),
    ("forward",    ActionSemantic.SHARE),
    ("publish",    ActionSemantic.SHARE),
    ("export",     ActionSemantic.SHARE),
    ("grant",      ActionSemantic.SHARE),
    ("revoke",     ActionSemantic.REVOKE),
    ("invite",     ActionSemantic.INVITE),
    ("add_member", ActionSemantic.INVITE),
    ("invoke",     ActionSemantic.INVOKE),
    ("run",        ActionSemantic.INVOKE),
    ("execute",    ActionSemantic.EXECUTE),
    ("trigger",    ActionSemantic.INVOKE),
    ("dispatch",   ActionSemantic.INVOKE),
    ("spawn",      ActionSemantic.SPAWN),
    ("permission", ActionSemantic.PERMISSION_CHANGE),
    ("chmod",      ActionSemantic.PERMISSION_CHANGE),
    ("admin",      ActionSemantic.ADMIN_CONFIG),
    ("config",     ActionSemantic.ADMIN_CONFIG),
]


class NormalizationResult:
    """Result of normalizing a tool operation."""
    __slots__ = ("action_semantic", "is_ambiguous", "matched_by", "notes")

    def __init__(
        self,
        action_semantic: ActionSemantic,
        is_ambiguous: bool = False,
        matched_by: str = "exact",
        notes: str = "",
    ) -> None:
        self.action_semantic = action_semantic
        self.is_ambiguous    = is_ambiguous
        self.matched_by      = matched_by
        self.notes           = notes


def normalize_action(
    tool_name:   str,
    operation:   Optional[str],
) -> NormalizationResult:
    """
    Normalize a tool-native operation to a CIM Tier-1 semantic action.

    Strategy (paper §B.2):
    1. Exact match in tool-specific table.
    2. Prefix/substring match via _PREFIX_PATTERNS.
    3. If ambiguous or unknown, return ActionSemantic.UNKNOWN.

    Normalization is metadata-only; payload content is never inspected.
    """
    if not operation:
        return NormalizationResult(
            action_semantic=ActionSemantic.UNKNOWN,
            is_ambiguous=True,
            matched_by="none",
            notes="No operation name provided; cannot normalize.",
        )

    # 1. Exact match in tool table
    tool_key = tool_name.lower().strip()
    op_key   = operation.strip()
    tool_map = _TOOL_OPERATION_MAP.get(tool_key, {})
    if op_key in tool_map:
        return NormalizationResult(
            action_semantic=tool_map[op_key],
            matched_by="exact_tool_table",
        )

    # 2. Case-insensitive exact match
    op_lower = op_key.lower()
    for mapped_op, semantic in tool_map.items():
        if mapped_op.lower() == op_lower:
            return NormalizationResult(
                action_semantic=semantic,
                matched_by="case_insensitive_tool_table",
            )

    # 3. Prefix/substring scan
    for prefix, semantic in _PREFIX_PATTERNS:
        if op_lower.startswith(prefix) or op_lower == prefix:
            return NormalizationResult(
                action_semantic=semantic,
                is_ambiguous=True,
                matched_by=f"prefix_pattern:{prefix}",
                notes=(
                    f"Operation '{operation}' matched prefix pattern '{prefix}'; "
                    "may emit both tool.operation and action.semantic."
                ),
            )

    # 4. Unknown
    return NormalizationResult(
        action_semantic=ActionSemantic.UNKNOWN,
        is_ambiguous=True,
        matched_by="fallback_unknown",
        notes=(
            f"Operation '{operation}' on tool '{tool_name}' could not be normalized. "
            "Recording action.outcome=unknown as per paper §B.2."
        ),
    )


def infer_direction(
    tool_name: str,
    operation: Optional[str],
    resource_type: Optional[str] = None,
    target_principal_id: Optional[str] = None,
) -> Optional[ActionDirection]:
    """
    Heuristically infer Tier-2 direction qualifier (internal vs. external).
    Used only as a best-effort hint; paper treats this as optional enrichment.
    """
    if target_principal_id and "@" in target_principal_id:
        # Email-style target suggests external recipient
        return ActionDirection.EXTERNAL
    sharing_ops = {"share", "send", "post", "forward", "publish", "export"}
    if operation and any(s in operation.lower() for s in sharing_ops):
        return ActionDirection.EXTERNAL
    internal_tools = {"gdocs", "drive", "gsheets", "github", "database", "filesystem"}
    if tool_name.lower() in internal_tools:
        return ActionDirection.INTERNAL
    return None
