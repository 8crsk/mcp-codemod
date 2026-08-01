"""Tests for the rules added beyond the first pass.

As with the core suite, the negative cases carry the weight. Anything can
rename a symbol; the value is in the renames it refuses to make.
"""

from __future__ import annotations

import pytest

from mcp_codemod import run
from mcp_codemod.rules import CHECKS


# -- mechanical rewrites ------------------------------------------------


def test_removed_aliases_with_replacements_are_renamed() -> None:
    before = """\
from mcp.types import Content, ResourceReference

def handle(block: Content, ref: ResourceReference) -> None:
    pass
"""
    after, _, changed = run(before)
    assert changed
    assert "from mcp.types import ContentBlock, ResourceTemplateReference" in after
    assert "block: ContentBlock" in after
    assert "ref: ResourceTemplateReference" in after


def test_union_type_suffix_is_dropped() -> None:
    before = """\
from mcp.types import ClientRequestType, ServerNotificationType

x: ClientRequestType
y: ServerNotificationType
"""
    after, _, changed = run(before)
    assert changed
    assert "ClientRequest" in after and "ClientRequestType" not in after
    assert "ServerNotification" in after and "ServerNotificationType" not in after


def test_streamablehttp_client_gains_its_underscore() -> None:
    before = """\
from mcp.client.streamable_http import streamablehttp_client

async def go():
    async with streamablehttp_client(url="http://x/mcp") as streams:
        return streams
"""
    after, _, changed = run(before)
    assert changed
    assert "import streamable_http_client" in after
    assert "async with streamable_http_client(" in after
    assert "streamablehttp_client" not in after


def test_shared_version_module_moves_to_types_version() -> None:
    before = "from mcp.shared.version import LATEST_PROTOCOL_VERSION\n"
    after, _, changed = run(before)
    assert changed
    assert after == "from mcp.types.version import LATEST_PROTOCOL_VERSION\n"


# -- the anti-rule ------------------------------------------------------


def test_mcp_types_is_never_rewritten_to_mcp_types_package() -> None:
    """``mcp.types`` is a permanent alias.

    The guide is explicit that rewriting these imports to ``mcp_types`` would
    reach past the user's declared dependency into a transitive one. A codemod
    that "helpfully" modernised them would introduce a packaging bug, so this
    is a tested guarantee rather than an implementation accident.
    """
    before = """\
import mcp.types
from mcp.types import Tool
from mcp.types.version import LATEST_PROTOCOL_VERSION
"""
    after, _, _ = run(before)
    assert "mcp_types" not in after
    assert "import mcp.types" in after
    assert "from mcp.types import Tool" in after
    assert "from mcp.types.version import" in after


# -- timedelta conversion ----------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("timedelta(seconds=30)", "30"),
        ("timedelta(minutes=2)", "120"),
        ("timedelta(hours=1)", "3600"),
        ("timedelta(minutes=1, seconds=30)", "90"),
        ("timedelta(milliseconds=500)", "0.5"),
    ],
)
def test_literal_timedelta_timeouts_become_float_seconds(
    expression: str, expected: str
) -> None:
    before = f"""\
import mcp
from datetime import timedelta

session.call_tool("t", {{}}, read_timeout_seconds={expression})
"""
    after, findings, changed = run(before)
    assert changed
    assert f"read_timeout_seconds={expected}" in after
    assert findings == []


def test_non_literal_timedelta_timeout_is_reported_not_guessed() -> None:
    before = """\
import mcp
from datetime import timedelta

session.call_tool("t", {}, read_timeout_seconds=timedelta(seconds=configured))
"""
    after, findings, _ = run(before)
    assert after == before
    assert [f.code for f in findings] == ["F007"]


def test_timedelta_outside_a_timeout_kwarg_is_left_alone() -> None:
    """A ``timeout=`` kwarg is too common across libraries to rewrite."""
    before = """\
import mcp
from datetime import timedelta

cache.set("k", "v", timeout=timedelta(seconds=30))
delay = timedelta(minutes=5)
"""
    after, _, changed = run(before)
    assert after == before
    assert not changed


# -- detections ---------------------------------------------------------


def test_root_access_is_reported_when_union_types_are_imported() -> None:
    before = """\
from mcp.types import ServerNotification

def handle(message: ServerNotification):
    return message.root
"""
    after, findings, _ = run(before)
    assert "message.root" in after, "F002 must not be auto-fixed"
    assert [f.code for f in findings] == ["F002"]


def test_root_access_without_union_imports_is_not_reported() -> None:
    """``.root`` is a common attribute name; only flag it where it means this."""
    before = """\
import mcp

tree = build_tree()
return tree.root
"""
    _, findings, _ = run(before)
    assert findings == []


def test_httpx_import_is_reported_not_rewritten() -> None:
    before = """\
import mcp
import httpx

client = httpx.AsyncClient(follow_redirects=True)
"""
    after, findings, _ = run(before)
    assert "import httpx\n" in after and "httpx2" not in after
    assert [f.code for f in findings] == ["F003"]


def test_transport_kwargs_on_the_constructor_are_reported() -> None:
    """Regression: renaming the class alone left code that crashes on startup.

    Found by running the codemod against glassflow/navflow, which constructs
    FastMCP with host= and port=. Those moved onto run() in v2, so a rename
    without this warning produces a diff that looks finished and raises
    TypeError the moment the server launches.
    """
    before = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo", host="127.0.0.1", port=8788)
"""
    after, findings, changed = run(before)
    assert changed
    assert 'MCPServer("demo", host="127.0.0.1", port=8788)' in after, (
        "the arguments are reported, not removed"
    )
    assert [f.code for f in findings] == ["F009"]
    assert "host" in findings[0].message and "port" in findings[0].message
    assert "TypeError" in findings[0].message


def test_all_moved_transport_kwargs_are_detected() -> None:
    before = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "demo",
    json_response=True,
    stateless_http=True,
    sse_path="/events",
    transport_security=None,
)
"""
    _, findings, _ = run(before)
    assert [f.code for f in findings] == ["F009"]
    for name in ("json_response", "stateless_http", "sse_path", "transport_security"):
        assert name in findings[0].message


def test_constructor_without_transport_kwargs_is_clean() -> None:
    """Identity and auth parameters stay on the constructor in v2."""
    before = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo", instructions="hi", debug=True, log_level="INFO")
"""
    _, findings, changed = run(before)
    assert changed
    assert findings == []


def test_httpx_in_a_file_that_does_not_touch_mcp_is_not_reported() -> None:
    """Regression: F003 used to fire on any file importing httpx.

    Found by running the codemod over agentic-ops/legal-mcp, where every
    finding was F003 against HTTP client modules that have nothing to do with
    the MCP SDK. A project's unrelated networking code is not our business.
    """
    before = """\
import httpx

async def fetch(url):
    async with httpx.AsyncClient() as client:
        return await client.get(url)
"""
    _, findings, changed = run(before)
    assert findings == []
    assert not changed


def test_httpx_imported_before_mcp_is_still_reported() -> None:
    """The guard must not become order-dependent.

    Import sorters put `httpx` above `mcp`, so deciding at visit time would
    trade the false positive above for a false negative here.
    """
    before = """\
import httpx

from mcp.types import Tool

def build(client: httpx.AsyncClient) -> Tool:
    return Tool(name="x", inputSchema={})
"""
    _, findings, _ = run(before)
    assert [f.code for f in findings] == ["F003"]
    assert findings[0].line == 1


def test_removed_name_without_replacement_is_reported() -> None:
    before = "from mcp.types import Cursor\n"
    after, findings, _ = run(before)
    assert after == before, "F004 names have no mechanical replacement"
    assert [f.code for f in findings] == ["F004"]
    assert "str" in findings[0].message


def test_deprecated_name_is_reported() -> None:
    before = "from mcp.types.version import SUPPORTED_PROTOCOL_VERSIONS\n"
    _, findings, _ = run(before)
    assert [f.code for f in findings] == ["F006"]


def test_meta_progress_token_is_reported_not_renamed() -> None:
    """RequestParamsMeta needs dict access, which is a different expression.

    ``progressToken`` is in the snake_case rename table, so the naive result
    would be ``meta.progress_token``, still broken, because meta is a
    TypedDict now. The receiver name disambiguates it.
    """
    before = """\
import mcp

def handler(meta):
    return meta.progressToken
"""
    after, findings, _ = run(before)
    assert "meta.progressToken" in after
    assert "meta.progress_token" not in after
    assert [f.code for f in findings] == ["F005"]
    assert 'get("progress_token")' in findings[0].message


def test_progress_token_on_other_receivers_still_renames() -> None:
    before = """\
import mcp

token = params.progressToken
"""
    after, findings, changed = run(before)
    assert changed
    assert "params.progress_token" in after
    assert findings == []


def test_lowlevel_decorators_are_reported() -> None:
    before = """\
from mcp.server.lowlevel import Server

server = Server("demo")


@server.list_tools()
async def list_tools():
    return []


@server.call_tool()
async def call_tool(name, args):
    return []
"""
    after, findings, _ = run(before)
    assert "@server.list_tools()" in after, "F008 must not be auto-fixed"
    assert [f.code for f in findings] == ["F008", "F008"]
    assert "on_list_tools=" in findings[0].message


def test_lowlevel_finding_names_the_exact_replacement() -> None:
    """F008 must be actionable, not just a warning that work exists.

    The v2 migration changes three things at once: registration moves to the
    constructor, the signature becomes (ctx, params), and the return value
    must be the full result type. Naming all three per handler is the whole
    value the tool can add here, since the rewrite itself is not safe.
    """
    before = """\
from mcp.server.lowlevel import Server

server = Server("demo")


@server.call_tool()
async def handle(name, arguments):
    return []
"""
    _, findings, _ = run(before)
    assert [f.code for f in findings] == ["F008"]
    msg = findings[0].message
    assert "on_call_tool" in msg
    assert "CallToolRequestParams" in msg
    assert "CallToolResult" in msg
    assert "NameError" in msg, "the constructor ordering trap must be called out"


def test_each_lowlevel_handler_maps_to_its_own_types() -> None:
    before = """\
from mcp.server.lowlevel import Server

server = Server("demo")


@server.list_prompts()
async def prompts():
    return []


@server.read_resource()
async def read(uri):
    return b""
"""
    _, findings, _ = run(before)
    assert [f.code for f in findings] == ["F008", "F008"]
    joined = " ".join(f.message for f in findings)
    assert "on_list_prompts" in joined and "ListPromptsResult" in joined
    assert "on_read_resource" in joined and "ReadResourceRequestParams" in joined


def test_request_ctx_import_is_reported() -> None:
    before = "from mcp.server.lowlevel.server import request_ctx\n"
    after, findings, _ = run(before)
    assert after == before, "F010 has no mechanical replacement"
    assert [f.code for f in findings] == ["F010"]
    assert "removed entirely" in findings[0].message


def test_server_request_context_property_is_reported() -> None:
    before = """\
from mcp.server.lowlevel import Server

server = Server("demo")


@server.call_tool()
async def handle(name, arguments):
    ctx = server.request_context
    return []
"""
    after, findings, _ = run(before)
    assert "server.request_context" in after, "F010 must not be rewritten"
    assert {f.code for f in findings} == {"F008", "F010"}


def test_request_context_on_unrelated_objects_is_ignored() -> None:
    """`request_context` is a plausible attribute name outside the SDK."""
    before = """\
import mcp

ctx = my_framework.request_context
"""
    _, findings, _ = run(before)
    assert findings == []


def test_lowlevel_decorator_names_on_non_lowlevel_code_are_ignored() -> None:
    before = """\
import mcp

@app.call_tool()
def handler():
    pass
"""
    _, findings, _ = run(before)
    assert findings == []


# -- invariants ---------------------------------------------------------


def test_findings_never_mutate_the_source() -> None:
    """Every detection-only code must leave the file byte-identical."""
    before = """\
from mcp.types import Cursor, ServerNotification
from mcp.types.version import SUPPORTED_PROTOCOL_VERSIONS
import httpx

def handler(meta, message: ServerNotification):
    payload = message.model_dump()
    return meta.progressToken, message.root, payload
"""
    after, findings, _ = run(before)
    assert after == before
    codes = {f.code for f in findings}
    assert codes == {"F001", "F002", "F003", "F004", "F005", "F006"}


def test_expanded_rules_are_idempotent() -> None:
    before = """\
from mcp.server.fastmcp import FastMCP
from mcp.types import Content
from datetime import timedelta

mcp = FastMCP("demo")
session.call_tool("t", {}, read_timeout_seconds=timedelta(seconds=30))
"""
    once, _, _ = run(before)
    twice, _, changed_again = run(once)
    assert once == twice
    assert not changed_again


def test_every_emitted_code_is_documented() -> None:
    """CHECKS is the README's source of truth; it must not drift."""
    source = """\
from mcp.types import Cursor, ServerNotification
from mcp.types.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.server.lowlevel import Server
from datetime import timedelta
import httpx

server = Server("d")


@server.call_tool()
async def handler(meta, message: ServerNotification):
    session.call_tool("t", {}, read_timeout_seconds=timedelta(seconds=n))
    return meta.progressToken, message.root, message.model_dump()
"""
    _, findings, _ = run(source)
    emitted = {f.code for f in findings}
    assert emitted, "sample should trigger findings"
    assert emitted <= set(CHECKS), f"undocumented codes: {emitted - set(CHECKS)}"
