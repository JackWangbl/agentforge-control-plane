from app.services.browser_runtime import browser_tool_specs, execute_browser_tool
from app.services.tool_runtime import execute_tool, tool_specs_for_mcp
from app.models import McpServer


def test_browser_tool_specs():
    names = {item["name"] for item in browser_tool_specs()}
    assert names == {"browser_open", "browser_text", "browser_links", "browser_close"}


def test_browser_open_rejects_bad_url():
    result = execute_browser_tool("browser_open", {"url": "file:///etc/passwd"})
    assert "http/https" in result


def test_browser_mcp_specs():
    row = McpServer(name="浏览器工具", transport="stdio", endpoint="builtin:browser", config={"kind": "builtin"})
    names = [item["name"] for item in tool_specs_for_mcp(row)]
    assert "browser_open" in names


def test_execute_tool_routes_browser_errors():
    raw = execute_tool("browser_open", {"url": "not-a-url"})
    assert "http/https" in raw
