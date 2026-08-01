# mcp-codemod

[![tests](https://github.com/8crsk/mcp-codemod/actions/workflows/test.yml/badge.svg)](https://github.com/8crsk/mcp-codemod/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-codemod)](https://pypi.org/project/mcp-codemod/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-codemod)](https://pypi.org/project/mcp-codemod/)

Migrates MCP Python SDK v1 code to v2 (the 2026-07-28 spec revision).

The TypeScript and Go SDKs ship a codemod for this migration. Python does not.
Python developers get a 2,879-line migration guide and a lot of manual
find-and-replace. This tool fills that gap.

## Installation

```bash
pipx install mcp-codemod
```

`pipx` is the recommended way to install a command-line tool. It keeps the tool
in its own environment and puts the command on your PATH.

If you would rather install into an existing virtualenv, `pip` works too:

```bash
pip install mcp-codemod
```

Note that on Ubuntu 24.04, Debian 12, Fedora, and other distributions that mark
the system Python as externally managed (PEP 668), a bare `pip install` outside
a virtualenv will be refused. Use `pipx` there.

## Usage

```bash
mcp-codemod path/to/your/server           # dry run, prints a diff
mcp-codemod path/to/your/server --write   # applies the changes
```

Dry run is the default. Review the diff before writing.

## Why not find-and-replace

The v2 models still accept camelCase at construction time. Only attribute
access changed:

```python
tool = Tool(name="forecast", inputSchema={"type": "object"})   # valid in v2
print(tool.inputSchema)                                        # broken in v2
```

Running `sed -i 's/inputSchema/input_schema/g'` breaks the first line while
fixing the second. This tool handles both correctly because it operates on the
concrete syntax tree, where a keyword argument is a `Name` node and attribute
access is an `Attribute` node. Visiting only `Attribute` nodes cannot reach the
keyword argument.

```diff
 tool = Tool(name="forecast", inputSchema={"type": "object"})
-print(tool.inputSchema, tool.outputSchema)
+print(tool.input_schema, tool.output_schema)
```

The implementation uses [LibCST](https://github.com/Instagram/LibCST) rather
than the standard library `ast` module. A round trip through `ast` discards
formatting: it reflows the file, strips comments, and normalises quote style.
LibCST preserves every byte it does not deliberately change, so the resulting
diff contains only the migration.

## Changes applied automatically

| Change | Example |
|---|---|
| `FastMCP` to `MCPServer` | `mcp = FastMCP("x")` becomes `mcp = MCPServer("x")` |
| `mcp.server.fastmcp.*` to `mcp.server.mcpserver.*` | includes all submodules |
| `McpError` to `MCPError` | |
| camelCase to snake_case attribute access | 11 fields including `inputSchema`, `isError`, `nextCursor`, `mimeType` |
| `ctx.fastmcp` to `ctx.mcp_server` | only on parameters annotated `Context` |
| `Content` to `ContentBlock` | |
| `ResourceReference` to `ResourceTemplateReference` | |
| `ClientRequestType` to `ClientRequest` | and the five other `*Type` unions |
| `streamablehttp_client` to `streamable_http_client` | |
| `mcp.shared.version` to `mcp.types.version` | module was removed in v2 |
| `timedelta` timeouts to float seconds | `read_timeout_seconds=timedelta(minutes=2)` becomes `=120` |

## Changes reported for manual review

Some v2 changes depend on runtime types, or on intent that the source code does
not express. Applying those automatically would risk introducing bugs, so they
are reported and the source is left unmodified.

| Code | Description |
|---|---|
| F001 | `model_dump()` without `by_alias=True` |
| F002 | `.root` access on a union that is no longer a `RootModel` |
| F003 | `httpx` or `httpx-sse` imported, SDK moved to `httpx2` |
| F004 | Name removed in v2 with no drop-in replacement (`Cursor`, `AnyFunction`, others) |
| F005 | `RequestParams.Meta` is now a TypedDict, attribute access becomes `.get()` |
| F006 | Deprecated name (`SUPPORTED_PROTOCOL_VERSIONS`) |
| F007 | `timedelta` timeout too dynamic to convert safely |
| F008 | Lowlevel `@server.list_tools()` decorator, now an `on_list_tools=` constructor parameter |

F001 deserves particular attention. In v1, `model_dump()` emitted camelCase
because the model fields themselves were camelCase. In v2 the same call emits
snake_case, which other MCP implementations will not recognise. The migration
guide describes the consequence directly:

> No error is raised; the output is silently in the wrong shape.

This is not corrected automatically because the receiver cannot be statically
proven to be an MCP protocol type. Adding `by_alias=True` to an unrelated
Pydantic model would corrupt that model's output instead.

## Imports that are intentionally left alone

The `mcp.types` module moved to a standalone `mcp-types` distribution, but this
is a no-op for projects that depend on `mcp`. The migration guide is explicit:

> `mcp.types` is a permanent alias that mirrors `mcp_types` exactly. Keep
> importing through `mcp`, the package you actually depend on, rather than
> writing `import mcp_types`, which would reach past your declared dependency
> into a transitive one.

Rewriting those imports would introduce an undeclared dependency, so
`mcp-codemod` never does. There is a test covering this.

## Relationship to mcp-migration

[`mcp-migration`](https://pypi.org/project/mcp-migration/) detects behavioural
hazards that a codemod cannot rewrite, such as in-memory state mutated inside
tool handlers, session-id dependencies, and live server readiness. It is not a
codemod. This tool is not a hazard detector. The two are complementary:

```bash
mcp-codemod .          # apply the mechanical changes
mcp-migration scan .   # then check for behavioural hazards
```

## Limitations

* Coverage is the migration guide's "changes almost every project hits" table
  plus the removed-alias set. The full guide is 2,879 lines and this tool does
  not implement all of it.
* Rewrites are skipped in files that do not import `mcp`. A module that handles
  MCP types without importing from `mcp` will not be processed.
* Symbol renames are name-based. A local variable named `Content` in a file that
  also imports `mcp` would be renamed. Review the diff.
* Import order is left untouched. Use `isort` if you need it.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Author

Sanjay Keerthan ([@8crsk](https://github.com/8crsk))

## License

MIT. See [LICENSE](LICENSE).
