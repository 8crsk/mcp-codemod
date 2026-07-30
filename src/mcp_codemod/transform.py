"""The v1 to v2 codemod itself.

Built on LibCST rather than ``ast`` because ``ast`` discards formatting: a
round-trip through it reflows the file, strips comments, and normalises quotes.
That produces a diff nobody can review. LibCST preserves every byte it does not
deliberately change, so the output diff contains the migration and nothing else.

The transformer maintains a strict split:

* **Rewrites** are applied only where the guide states the change is a pure
  rename or a mechanically derivable value.
* **Findings** are recorded for everything whose correct form depends on
  runtime types, program behaviour, or intent the source does not express.

Nothing moves between those two categories on a hunch. When in doubt the change
is a finding, because a wrong report costs the user thirty seconds and a wrong
rewrite costs them a debugging session.
"""

from __future__ import annotations

import libcst as cst
from libcst.metadata import PositionProvider

from .rules import (
    CONTEXT_ATTR_RENAMES,
    DEPRECATED_NAMES,
    DO_NOT_REWRITE_MODULES,
    FIELD_RENAMES,
    MODEL_DUMP_METHODS,
    MODULE_PREFIX_MOVES,
    REMOVED_NO_REPLACEMENT,
    ROOTMODEL_UNIONS,
    SYMBOL_RENAMES,
    TIMEDELTA_UNITS,
    TIMEOUT_KWARGS,
    Finding,
)

#: Lowlevel ``Server`` decorator handlers replaced by constructor ``on_*``
#: parameters in v2. Source: "Lowlevel Server decorator-based handlers replaced
#: with constructor on_* params".
_LOWLEVEL_DECORATORS = frozenset(
    {
        "list_tools",
        "call_tool",
        "list_resources",
        "read_resource",
        "list_resource_templates",
        "list_prompts",
        "get_prompt",
        "set_logging_level",
        "subscribe_resource",
        "unsubscribe_resource",
        "complete",
    }
)

#: Receiver names that indicate a request-params meta mapping rather than a
#: protocol model, so ``.progressToken`` needs dict access, not a rename.
_META_RECEIVERS = frozenset({"meta", "_meta"})


def _dotted(node: cst.BaseExpression) -> str | None:
    """Flatten a dotted name node (``a.b.c``) to a string, or None."""
    parts: list[str] = []
    current: cst.BaseExpression = node
    while isinstance(current, cst.Attribute):
        parts.append(current.attr.value)
        current = current.value
    if not isinstance(current, cst.Name):
        return None
    parts.append(current.value)
    return ".".join(reversed(parts))


def _build_dotted(path: str) -> cst.BaseExpression:
    """Rebuild a dotted name node from a string."""
    head, *rest = path.split(".")
    node: cst.BaseExpression = cst.Name(head)
    for part in rest:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node


def _timedelta_seconds(call: cst.Call) -> float | None:
    """Total seconds for a ``timedelta(...)`` built from numeric literals.

    Returns None when the call uses positional arguments, non-literal values,
    or an unrecognised unit — all cases where guessing would be worse than
    reporting.
    """
    if not call.args:
        return None
    total = 0.0
    for arg in call.args:
        if arg.keyword is None or arg.star:
            return None
        unit = TIMEDELTA_UNITS.get(arg.keyword.value)
        if unit is None:
            return None
        value = arg.value
        if isinstance(value, cst.Integer):
            magnitude = float(int(value.value.replace("_", ""), 0))
        elif isinstance(value, cst.Float):
            magnitude = float(value.value.replace("_", ""))
        else:
            return None
        total += magnitude * unit
    return total


def _is_timedelta_call(call: cst.Call) -> bool:
    name = _dotted(call.func)
    return name is not None and name.split(".")[-1] == "timedelta"


def _number_node(seconds: float) -> cst.BaseExpression:
    if seconds.is_integer():
        return cst.Integer(value=str(int(seconds)))
    return cst.Float(value=repr(seconds))


class MCPv2Codemod(cst.CSTTransformer):
    """Rewrites the mechanical parts of the v1 to v2 migration."""

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self) -> None:
        super().__init__()
        self.findings: list[Finding] = []
        self.changed = False
        self._imports_mcp = False
        self._imported_names: set[str] = set()
        self._uses_lowlevel_server = False
        self._context_params: set[str] = set()

    # -- helpers ---------------------------------------------------------

    def _line(self, node: cst.CSTNode) -> int:
        return self.get_metadata(PositionProvider, node).start.line

    def _report(
        self, code: str, node: cst.CSTNode, message: str, section: str = ""
    ) -> None:
        self.findings.append(Finding(code, self._line(node), message, section))

    # -- import scanning (guards + detections) ----------------------------

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            name = _dotted(alias.name)
            if not name:
                continue
            if name == "mcp" or name.startswith("mcp."):
                self._imports_mcp = True
            if name in ("httpx", "httpx_sse"):
                self._report(
                    "F003",
                    node,
                    f"{name} imported; the SDK now uses httpx2. Objects you "
                    "hand to the SDK (http_client, auth, httpx_client_factory "
                    "results) must become httpx2 types. Unrelated httpx usage "
                    "in your project can stay, which is why this is not "
                    "rewritten for you.",
                    "httpx-and-httpx-sse-replaced-by-httpx2",
                )

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if node.module is None:
            return
        module = _dotted(node.module)
        if not module:
            return

        if module == "mcp" or module.startswith("mcp."):
            self._imports_mcp = True
        if module in ("httpx", "httpx_sse"):
            self._report(
                "F003",
                node,
                f"{module} imported; the SDK now uses httpx2.",
                "httpx-and-httpx-sse-replaced-by-httpx2",
            )
        if "lowlevel" in module:
            self._uses_lowlevel_server = True

        if isinstance(node.names, cst.ImportStar):
            return
        for alias in node.names:
            if not isinstance(alias.name, cst.Name):
                continue
            imported = alias.name.value
            self._imported_names.add(
                alias.asname.name.value
                if alias.asname is not None
                and isinstance(alias.asname.name, cst.Name)
                else imported
            )
            if imported == "Server" and module.startswith("mcp."):
                self._uses_lowlevel_server = True
            if module.startswith("mcp") and imported in REMOVED_NO_REPLACEMENT:
                self._report(
                    "F004",
                    node,
                    f"`{imported}` was removed in v2: "
                    f"{REMOVED_NO_REPLACEMENT[imported]}.",
                    "removed-type-aliases-and-classes",
                )
            if module.startswith("mcp") and imported in DEPRECATED_NAMES:
                self._report(
                    "F006",
                    node,
                    f"`{imported}` is deprecated: {DEPRECATED_NAMES[imported]}",
                    "supported_protocol_versions-deprecated",
                )

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        """Record parameters annotated ``Context``.

        ``ctx.fastmcp`` only becomes ``ctx.mcp_server`` on a Context object.
        Renaming ``.fastmcp`` on an unrelated object would be a bug, so the
        rename is gated on an annotation we can actually see.
        """
        for param in list(node.params.params) + list(node.params.kwonly_params):
            annotation = param.annotation
            if annotation is None:
                continue
            annotated = _dotted(annotation.annotation)
            if annotated and annotated.split(".")[-1] == "Context":
                self._context_params.add(param.name.value)

    def visit_Decorator(self, node: cst.Decorator) -> None:
        """Flag lowlevel ``@server.list_tools()``-style handlers."""
        if not self._uses_lowlevel_server:
            return
        target = node.decorator
        if isinstance(target, cst.Call):
            target = target.func
        if not isinstance(target, cst.Attribute):
            return
        handler = target.attr.value
        if handler in _LOWLEVEL_DECORATORS:
            self._report(
                "F008",
                node,
                f"Lowlevel `@…{handler}()` decorator. v2 replaces decorator "
                f"handlers with an `on_{handler}=` constructor parameter on "
                "Server; the rewrite depends on where you construct it.",
                "lowlevel-server-decorator-based-handlers-replaced-with-"
                "constructor-on_-params",
            )

    # -- import rewrites -------------------------------------------------

    def _move_module(self, path: str) -> str | None:
        if path in DO_NOT_REWRITE_MODULES:
            return None
        for old, new in MODULE_PREFIX_MOVES.items():
            if path == old or path.startswith(old + "."):
                return new + path[len(old) :]
        return None

    def leave_ImportFrom(
        self, original: cst.ImportFrom, updated: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated.module is None:
            return updated
        path = _dotted(updated.module)
        if path is None:
            return updated
        moved = self._move_module(path)
        if moved is not None:
            self.changed = True
            return updated.with_changes(module=_build_dotted(moved))
        return updated

    def leave_Import(self, original: cst.Import, updated: cst.Import) -> cst.Import:
        new_names = []
        touched = False
        for alias in updated.names:
            path = _dotted(alias.name)
            replacement = alias
            if path is not None:
                moved = self._move_module(path)
                if moved is not None:
                    replacement = alias.with_changes(name=_build_dotted(moved))
                    touched = True
            new_names.append(replacement)
        if touched:
            self.changed = True
            return updated.with_changes(names=new_names)
        return updated

    # -- symbol rewrites -------------------------------------------------

    def leave_Name(self, original: cst.Name, updated: cst.Name) -> cst.Name:
        """Rename SDK identifiers that changed name in v2.

        Gated on the file importing ``mcp`` at all, so a variable
        coincidentally named ``Content`` or ``FastMCP`` in an unrelated module
        is left alone.
        """
        if not self._imports_mcp:
            return updated
        new = SYMBOL_RENAMES.get(updated.value)
        if new is not None:
            self.changed = True
            return updated.with_changes(value=new)
        return updated

    # -- attribute rewrites and detections --------------------------------

    def leave_Attribute(
        self, original: cst.Attribute, updated: cst.Attribute
    ) -> cst.Attribute:
        """Rewrite camelCase *attribute access* to snake_case.

        Constructor keyword arguments are untouched, and not by accident: in
        the CST a kwarg is ``Arg(keyword=Name("inputSchema"))``, a ``Name``
        node, never an ``Attribute``. Visiting only ``Attribute`` nodes cannot
        reach them. That is exactly the distinction a textual find-and-replace
        loses.
        """
        if not self._imports_mcp:
            return updated

        attr = updated.attr.value
        receiver = updated.value

        # `.root` on a union type that is no longer a RootModel.
        if attr == "root" and self._imported_names & ROOTMODEL_UNIONS:
            self._report(
                "F002",
                original,
                "`.root` access. ClientRequest, ServerRequest, "
                "ClientNotification, ServerNotification, ClientResult, "
                "ServerResult and JSONRPCMessage are plain unions in v2, not "
                "RootModel subclasses. Use the provided TypeAdapter instances "
                "to validate instead.",
                "replace-rootmodel-by-union-types-with-typeadapter-validation",
            )
            return updated

        # RequestParams.Meta is now a TypedDict — attribute access must become
        # dict access, which is a different expression shape, not a rename.
        if isinstance(receiver, cst.Name) and receiver.value in _META_RECEIVERS:
            if attr in ("progressToken", "progress_token"):
                self._report(
                    "F005",
                    original,
                    f"`{receiver.value}.{attr}`. RequestParams.Meta is now the "
                    "RequestParamsMeta TypedDict; use "
                    f'`{receiver.value}.get("progress_token")` instead of '
                    "attribute access.",
                    "requestparamsmeta-replaced-with-requestparamsmeta-typeddict",
                )
                return updated

        if _dotted(updated) == "RequestParams.Meta":
            self._report(
                "F005",
                original,
                "`RequestParams.Meta` is now the top-level `RequestParamsMeta` "
                "TypedDict.",
                "requestparamsmeta-replaced-with-requestparamsmeta-typeddict",
            )
            return updated

        if (
            isinstance(receiver, cst.Name)
            and receiver.value in self._context_params
            and attr in CONTEXT_ATTR_RENAMES
        ):
            self.changed = True
            return updated.with_changes(attr=cst.Name(CONTEXT_ATTR_RENAMES[attr]))

        new = FIELD_RENAMES.get(attr)
        if new is not None:
            self.changed = True
            return updated.with_changes(attr=cst.Name(new))
        return updated

    # -- call rewrites and detections -------------------------------------

    def visit_Call(self, node: cst.Call) -> None:
        """Flag ``model_dump()`` calls that lack ``by_alias=True``.

        Not auto-fixed on purpose. We cannot statically prove the receiver is
        an MCP protocol type, and adding ``by_alias=True`` to an unrelated
        Pydantic model would corrupt *that* model's output. The guide calls
        this silent — the wrong shape goes on the wire and nothing raises — so
        a report is the safe intervention.
        """
        if not self._imports_mcp:
            return
        if not isinstance(node.func, cst.Attribute):
            return
        if node.func.attr.value not in MODEL_DUMP_METHODS:
            return
        for arg in node.args:
            if arg.keyword is not None and arg.keyword.value == "by_alias":
                return
        self._report(
            "F001",
            node,
            f"{node.func.attr.value}() without by_alias=True. In v1 this "
            "emitted camelCase wire format; in v2 it emits snake_case, which "
            "other MCP implementations will not recognise. No error is raised. "
            "If this is an MCP protocol type, add by_alias=True.",
            "field-names-changed-from-camelcase-to-snake_case",
        )

    def leave_Call(self, original: cst.Call, updated: cst.Call) -> cst.BaseExpression:
        """Convert ``timedelta`` timeout arguments to float seconds."""
        if not self._imports_mcp:
            return updated

        new_args = []
        touched = False
        for index, arg in enumerate(updated.args):
            if (
                arg.keyword is None
                or arg.keyword.value not in TIMEOUT_KWARGS
                or not isinstance(arg.value, cst.Call)
                or not _is_timedelta_call(arg.value)
            ):
                new_args.append(arg)
                continue

            seconds = _timedelta_seconds(arg.value)
            if seconds is None:
                self._report(
                    "F007",
                    original.args[index].value,
                    f"`{arg.keyword.value}` now takes float seconds, but this "
                    "timedelta is not built from plain numeric literals so it "
                    "cannot be converted safely. Convert it by hand.",
                    "timeouts-take-float-seconds-instead-of-timedelta",
                )
                new_args.append(arg)
                continue

            new_args.append(arg.with_changes(value=_number_node(seconds)))
            touched = True

        if touched:
            self.changed = True
            return updated.with_changes(args=new_args)
        return updated


def run(source: str) -> tuple[str, list[Finding], bool]:
    """Apply the codemod to ``source``.

    Returns the rewritten source, findings needing human review, and whether
    anything was actually changed.
    """
    module = cst.parse_module(source)
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    codemod = MCPv2Codemod()
    updated = wrapper.visit(codemod)
    findings = sorted(codemod.findings, key=lambda f: (f.line, f.code))
    return updated.code, findings, codemod.changed
