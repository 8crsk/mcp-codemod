"""Rename tables and detection codes for the MCP Python SDK v1 to v2 migration.

Every entry here is transcribed from the SDK's own migration guide
(``docs/migration.md`` in modelcontextprotocol/python-sdk, the 2026-07-28 spec
revision). Nothing in this file is inferred. Where the guide and this file
disagree, the guide wins.

The tables are split by *how* a name is used, because the migration is not a
uniform find-and-replace:

``FIELD_RENAMES`` applies to **attribute access only**. The guide is explicit
that v2 models still accept camelCase at construction time::

    Tool(inputSchema={...})   # still valid in v2 — do not rewrite
    tool.inputSchema          # broken in v2 — rewrite to .input_schema

A regex cannot tell those apart. Rewriting the constructor kwarg is not merely
unnecessary, it is a regression: it changes working code for no reason and
produces diff noise the user has to review and revert.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Mechanical rewrites
# ---------------------------------------------------------------------------

#: camelCase attribute access becomes snake_case in v2.
#: Source: "Field names changed from camelCase to snake_case".
FIELD_RENAMES: dict[str, str] = {
    "inputSchema": "input_schema",
    "outputSchema": "output_schema",
    "isError": "is_error",
    "nextCursor": "next_cursor",
    "mimeType": "mime_type",
    "structuredContent": "structured_content",
    "serverInfo": "server_info",
    "protocolVersion": "protocol_version",
    "uriTemplate": "uri_template",
    "listChanged": "list_changed",
    "progressToken": "progress_token",
}

#: Bare identifiers that changed name in v2.
#: Sources: "`FastMCP` renamed to `MCPServer`", "`McpError` renamed to
#: `MCPError`", "Removed type aliases and classes", "`streamablehttp_client`
#: removed".
SYMBOL_RENAMES: dict[str, str] = {
    # Server class
    "FastMCP": "MCPServer",
    # Error class
    "McpError": "MCPError",
    # Removed aliases with direct replacements
    "Content": "ContentBlock",
    "ResourceReference": "ResourceTemplateReference",
    # The union types lost their `*Type` suffix; the union is now the bare name
    "ClientRequestType": "ClientRequest",
    "ClientNotificationType": "ClientNotification",
    "ClientResultType": "ClientResult",
    "ServerRequestType": "ServerRequest",
    "ServerNotificationType": "ServerNotification",
    "ServerResultType": "ServerResult",
    # Deprecated transport alias removed in favour of the underscored spelling
    "streamablehttp_client": "streamable_http_client",
}

#: Module path moves. All submodules under ``mcp.server.fastmcp.*`` moved to
#: ``mcp.server.mcpserver.*`` with the same structure, so this is a prefix
#: rewrite. ``mcp.shared.version`` was removed outright in favour of
#: ``mcp.types.version``.
MODULE_PREFIX_MOVES: dict[str, str] = {
    "mcp.server.fastmcp": "mcp.server.mcpserver",
    "mcp.shared.version": "mcp.types.version",
}

#: Renames scoped to the Context object injected into tools/resources/prompts.
#: Source: "The ``ctx.fastmcp`` property is now ``ctx.mcp_server``". Kept
#: separate from FIELD_RENAMES because ``fastmcp`` is a plausible attribute
#: name on unrelated objects, so the rename is only applied where we can see
#: the receiver is annotated ``Context``.
CONTEXT_ATTR_RENAMES: dict[str, str] = {
    "fastmcp": "mcp_server",
}

#: Modules that must NOT be rewritten, and why.
#:
#: ``mcp.types`` moved to a standalone ``mcp-types`` distribution, but the
#: guide is emphatic that this is a no-op for anyone depending on ``mcp``:
#: "``mcp.types`` is a permanent alias that mirrors ``mcp_types`` exactly" and
#: "Keep importing through ``mcp`` — the package you actually depend on —
#: rather than writing ``import mcp_types``, which would reach past your
#: declared dependency into a transitive one."
#:
#: A codemod that helpfully "modernised" these imports would introduce an
#: undeclared dependency. This constant exists to make that a tested guarantee
#: rather than an accident of implementation.
DO_NOT_REWRITE_MODULES: frozenset[str] = frozenset({"mcp.types"})

#: Timeout keyword arguments that changed from ``timedelta`` to ``float``
#: seconds. Restricted to MCP-specific parameter names on purpose: a bare
#: ``timeout=`` kwarg is far too common across unrelated libraries to rewrite
#: safely, so that case is reported as F007 instead.
#: Source: "Timeouts take `float` seconds instead of `timedelta`".
TIMEOUT_KWARGS: frozenset[str] = frozenset(
    {
        "read_timeout_seconds",
        "request_read_timeout_seconds",
        "sse_read_timeout",
    }
)

#: Multipliers for converting ``timedelta`` keyword arguments to seconds.
TIMEDELTA_UNITS: dict[str, float] = {
    "weeks": 604800.0,
    "days": 86400.0,
    "hours": 3600.0,
    "minutes": 60.0,
    "seconds": 1.0,
    "milliseconds": 0.001,
    "microseconds": 0.000001,
}

# ---------------------------------------------------------------------------
# Detections (reported, never rewritten)
# ---------------------------------------------------------------------------

#: Methods whose output shape silently changed. See F001.
MODEL_DUMP_METHODS: frozenset[str] = frozenset({"model_dump", "model_dump_json"})

#: Union types that stopped being ``RootModel`` subclasses, so ``.root`` access
#: and direct ``model_validate()`` no longer work.
#: Source: "Replace `RootModel` by union types with `TypeAdapter` validation".
ROOTMODEL_UNIONS: frozenset[str] = frozenset(
    {
        "ClientRequest",
        "ServerRequest",
        "ClientNotification",
        "ServerNotification",
        "ClientResult",
        "ServerResult",
        "JSONRPCMessage",
    }
)

#: Names removed with no drop-in replacement — each needs a human decision.
#: Source: "Removed type aliases and classes".
REMOVED_NO_REPLACEMENT: dict[str, str] = {
    "Cursor": "use `str` directly for pagination cursors",
    "MethodT": "internal TypeVar, not intended for public use",
    "RequestParamsT": "internal TypeVar, not intended for public use",
    "NotificationParamsT": "internal TypeVar, not intended for public use",
    "AnyFunction": "use `Callable[..., Any]` directly",
    "TaskExecutionMode": "use string literals; `TaskStatus` remains",
    "TASK_FORBIDDEN": "use string literals",
    "TASK_OPTIONAL": "use string literals",
    "TASK_REQUIRED": "use string literals",
}

#: Deprecated but still functional — worth surfacing, never worth rewriting.
#: Source: "`SUPPORTED_PROTOCOL_VERSIONS` deprecated".
DEPRECATED_NAMES: dict[str, str] = {
    "SUPPORTED_PROTOCOL_VERSIONS": (
        "now the union of HANDSHAKE_PROTOCOL_VERSIONS and "
        "MODERN_PROTOCOL_VERSIONS. If you meant 'versions the initialize "
        "handshake accepts', use HANDSHAKE_PROTOCOL_VERSIONS."
    ),
}


class Finding:
    """A change the codemod deliberately does not make.

    Some v2 changes need a human decision, and applying them blind would be
    worse than leaving them alone. Those are reported here and left in the
    source untouched.
    """

    __slots__ = ("code", "line", "message", "guide_section")

    def __init__(
        self, code: str, line: int, message: str, guide_section: str = ""
    ) -> None:
        self.code = code
        self.line = line
        self.message = message
        self.guide_section = guide_section

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Finding {self.code} line={self.line}>"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return (self.code, self.line, self.message) == (
            other.code,
            other.line,
            other.message,
        )

    def __hash__(self) -> int:
        return hash((self.code, self.line, self.message))


#: Human-readable catalogue of the detection codes, used by ``--list-checks``
#: and by the README. Keeping it here means the docs cannot drift from the
#: implementation without a test noticing.
CHECKS: dict[str, str] = {
    "F001": (
        "model_dump()/model_dump_json() without by_alias=True. The most "
        "consequential change in the migration: v1 emitted camelCase wire "
        "format, v2 emits snake_case, and peers will not recognise it. The "
        "guide's words: 'No error is raised; the output is silently in the "
        "wrong shape.'"
    ),
    "F002": (
        ".root access on a union type that is no longer a RootModel. Use the "
        "provided TypeAdapter instances for validation instead."
    ),
    "F003": (
        "httpx / httpx-sse imported. The SDK now uses httpx2. Only the objects "
        "handed to the SDK need to change type, so unrelated httpx usage in "
        "your project can stay — which is why this is not rewritten for you."
    ),
    "F004": "Imported name was removed in v2 with no drop-in replacement.",
    "F005": (
        "RequestParams.Meta is now the RequestParamsMeta TypedDict. Attribute "
        "access (meta.progressToken) becomes dict access "
        "(meta.get('progress_token'))."
    ),
    "F006": "Name is deprecated in v2.",
    "F007": (
        "timedelta passed to a timeout parameter that now takes float seconds, "
        "in a form too dynamic to convert safely."
    ),
    "F008": (
        "Lowlevel Server decorator-based handler. v2 replaces these with "
        "constructor on_* parameters; the rewrite depends on your server's "
        "construction site, so it is left to you."
    ),
}
