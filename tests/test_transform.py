"""Tests for the v1 to v2 codemod.

The most important tests here are the negative ones. Any regex can rewrite
``inputSchema`` to ``input_schema``; the value of a CST-based codemod is
entirely in what it declines to touch.
"""

from __future__ import annotations

from mcp_codemod import run


def test_fastmcp_import_and_construction_are_renamed() -> None:
    before = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Demo")
"""
    after, findings, changed = run(before)
    assert changed
    assert "from mcp.server.mcpserver import MCPServer" in after
    assert 'mcp = MCPServer("Demo")' in after
    assert "FastMCP" not in after
    assert findings == []


def test_submodule_paths_move_with_the_same_structure() -> None:
    before = "from mcp.server.fastmcp.utilities.types import Image\n"
    after, _, changed = run(before)
    assert changed
    assert after == "from mcp.server.mcpserver.utilities.types import Image\n"


def test_plain_import_statement_is_moved() -> None:
    before = "import mcp.server.fastmcp\n"
    after, _, changed = run(before)
    assert changed
    assert after == "import mcp.server.mcpserver\n"


def test_attribute_access_is_rewritten_to_snake_case() -> None:
    before = """\
import mcp

schema = tool.inputSchema
failed = result.isError
cursor = page.nextCursor
"""
    after, _, changed = run(before)
    assert changed
    assert "tool.input_schema" in after
    assert "result.is_error" in after
    assert "page.next_cursor" in after


def test_constructor_kwargs_are_left_alone() -> None:
    """The differentiator.

    The migration guide states v2 models still accept camelCase at
    construction. Rewriting these would change working code for no reason —
    exactly the regression a textual find-and-replace introduces.
    """
    before = """\
import mcp
from mcp.types import Tool

t = Tool(name="x", inputSchema={"type": "object"}, outputSchema=None)
"""
    after, _, _ = run(before)
    assert 'inputSchema={"type": "object"}' in after
    assert "outputSchema=None" in after
    assert "input_schema=" not in after


def test_kwarg_and_attribute_in_the_same_file_are_handled_differently() -> None:
    """Both forms, one file: rewrite the access, preserve the kwarg."""
    before = """\
import mcp
from mcp.types import Tool

t = Tool(name="x", inputSchema={"type": "object"})
print(t.inputSchema)
"""
    after, _, _ = run(before)
    assert 'inputSchema={"type": "object"}' in after
    assert "print(t.input_schema)" in after


def test_files_that_do_not_import_mcp_are_untouched() -> None:
    """A ``.isError`` on someone else's object is not our business."""
    before = """\
import requests

response = requests.get("https://example.com")
if response.isError:
    pass

class FastMCP:
    pass
"""
    after, findings, changed = run(before)
    assert not changed
    assert after == before
    assert findings == []


def test_context_attribute_renamed_only_when_annotated() -> None:
    before = """\
import mcp
from mcp.server.mcpserver import Context

def handler(ctx: Context) -> None:
    return ctx.fastmcp

def unrelated(thing) -> None:
    return thing.fastmcp
"""
    after, _, changed = run(before)
    assert changed
    assert "return ctx.mcp_server" in after
    assert "return thing.fastmcp" in after


def test_model_dump_without_by_alias_is_reported_not_rewritten() -> None:
    before = """\
import mcp

payload = tool.model_dump()
"""
    after, findings, _ = run(before)
    assert after == before, "F001 must never be auto-fixed"
    assert len(findings) == 1
    assert findings[0].code == "F001"
    assert findings[0].line == 3


def test_model_dump_with_by_alias_is_not_reported() -> None:
    before = """\
import mcp

payload = tool.model_dump(by_alias=True)
"""
    _, findings, _ = run(before)
    assert findings == []


def test_mcperror_is_renamed() -> None:
    before = """\
from mcp import McpError

raise McpError("boom")
"""
    after, _, changed = run(before)
    assert changed
    assert "from mcp import MCPError" in after
    assert 'raise MCPError("boom")' in after


def test_comments_blank_lines_and_quote_style_survive() -> None:
    """LibCST over ast: the diff must contain the migration and nothing else."""
    before = """\
from mcp.server.fastmcp import FastMCP  # the server


# Two blank lines above, single quotes below.
mcp = FastMCP('Demo')


def handler():
    '''Docstring stays put.'''
    return mcp
"""
    after, _, _ = run(before)
    assert "# the server" in after
    assert "# Two blank lines above, single quotes below." in after
    assert "'''Docstring stays put.'''" in after
    assert "MCPServer('Demo')" in after, "quote style must be preserved"
    assert "\n\n\ndef handler():" in after, "blank line runs must be preserved"


def test_idempotent() -> None:
    before = "from mcp.server.fastmcp import FastMCP\n\nmcp = FastMCP('D')\n"
    once, _, _ = run(before)
    twice, _, changed_again = run(once)
    assert once == twice
    assert not changed_again
