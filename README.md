# mcp-codemod

Migrates MCP Python SDK **v1** code to **v2** (the 2026-07-28 spec revision).

The TypeScript and Go SDKs ship a codemod for this. Python does not — Python
gets a 2,879-line migration guide and a lot of find-and-replace. This is that
missing codemod.

```bash
pip install mcp-codemod
mcp-codemod path/to/your/server        # dry run: prints a diff, changes nothing
mcp-codemod path/to/your/server --write
```

Dry run is the default on purpose. A tool that rewrites your source tree the
first time you try it, before you know whether it's any good, doesn't get a
second try.

## Why not `sed`

Because `sed` corrupts working code. The v2 models still accept camelCase at
construction time, and only *attribute access* changed:

```python
tool = Tool(name="forecast", inputSchema={"type": "object"})   # still valid in v2
print(tool.inputSchema)                                        # broken in v2
```

`sed -i 's/inputSchema/input_schema/g'` breaks the first line. `mcp-codemod`
doesn't, because in the concrete syntax tree a keyword argument is a `Name`
node and attribute access is an `Attribute` node — visiting only `Attribute`
nodes cannot reach the kwarg. Same file, three lines apart, handled
differently:

```diff
 tool = Tool(name="forecast", inputSchema={"type": "object"})
-print(tool.inputSchema, tool.outputSchema)
+print(tool.input_schema, tool.output_schema)
```

It's built on [LibCST](https://github.com/Instagram/LibCST) rather than `ast`
for the same reason: `ast` discards formatting, so a round-trip reflows your
file, strips comments, and normalises quotes. The resulting diff is
unreviewable. LibCST preserves every byte it doesn't deliberately change, so
the diff contains the migration and nothing else.

## What it rewrites

| Change | Example |
|---|---|
| `FastMCP` → `MCPServer` | `mcp = FastMCP("x")` → `mcp = MCPServer("x")` |
| `mcp.server.fastmcp.*` → `mcp.server.mcpserver.*` | including all submodules |
| `McpError` → `MCPError` | |
| camelCase → snake_case attribute access | 11 fields: `inputSchema`, `isError`, `nextCursor`, `mimeType`, … |
| `ctx.fastmcp` → `ctx.mcp_server` | only on parameters annotated `Context` |
| `Content` → `ContentBlock` | |
| `ResourceReference` → `ResourceTemplateReference` | |
| `ClientRequestType` → `ClientRequest` | and the five other `*Type` unions |
| `streamablehttp_client` → `streamable_http_client` | |
| `mcp.shared.version` → `mcp.types.version` | module was removed |
| `timedelta` timeouts → float seconds | `read_timeout_seconds=timedelta(minutes=2)` → `=120` |

## What it reports instead of rewriting

Some v2 changes depend on runtime types or on intent the source doesn't
express. Guessing at those would be worse than leaving them alone, so they're
reported and the source is left byte-identical.

| Code | What |
|---|---|
| **F001** | `model_dump()` without `by_alias=True` |
| F002 | `.root` access on a union that's no longer a `RootModel` |
| F003 | `httpx` / `httpx-sse` imported; the SDK moved to `httpx2` |
| F004 | Name removed in v2 with no drop-in replacement (`Cursor`, `AnyFunction`, …) |
| F005 | `RequestParams.Meta` is now a TypedDict — attribute access becomes `.get()` |
| F006 | Deprecated name (`SUPPORTED_PROTOCOL_VERSIONS`) |
| F007 | `timedelta` timeout too dynamic to convert safely |
| F008 | Lowlevel `@server.list_tools()` decorator → `on_list_tools=` constructor param |

**F001 is the one to care about.** In v1, `model_dump()` emitted camelCase
because the fields themselves were camelCase. In v2 it emits snake_case, which
other MCP implementations don't recognise. The migration guide's own words:

> No error is raised; the output is silently in the wrong shape.

It isn't auto-fixed because we can't statically prove the receiver is an MCP
protocol type, and adding `by_alias=True` to an unrelated Pydantic model would
corrupt *that* model's output.

## What it deliberately won't do

`mcp.types` moved to a standalone `mcp-types` distribution, but the guide is
emphatic that this is a no-op if you depend on `mcp`:

> `mcp.types` is a permanent alias that mirrors `mcp_types` exactly. Keep
> importing through `mcp` — the package you actually depend on — rather than
> writing `import mcp_types`, which would reach past your declared dependency
> into a transitive one.

So `mcp-codemod` never rewrites `mcp.types` → `mcp_types`. That's a tested
guarantee, not an accident of implementation.

## Use it with `mcp-migration`, not instead of it

[`mcp-migration`](https://pypi.org/project/mcp-migration/) finds *behavioural*
hazards a codemod can't rewrite — in-memory state mutated inside tool handlers,
session-id dependencies, live-server readiness probing. It explicitly isn't a
codemod. This is explicitly not a hazard detector. Run both:

```bash
mcp-codemod .          # rewrite the mechanical changes
mcp-migration scan .   # then find the behavioural hazards
```

## Limitations

- Covers the changes in the guide's "changes almost every project hits" table
  plus the removed-alias set. The guide is 2,879 lines; this is not all of it.
- Rewrites are gated on the file importing `mcp`. A module that touches MCP
  types without importing anything from `mcp` is skipped.
- Symbol renames are name-based. A local variable called `Content` in a file
  that also imports `mcp` would be renamed. Read the diff.
- No import reordering — that's `isort`'s job, not this tool's.

Read the diff before passing `--write`. That's what the dry run is for.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
