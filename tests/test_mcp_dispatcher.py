"""
Tests del dispatcher MCP en agent_executor.py (Fase 1).

Verifica que:
- _parse_tool_call acepta nombres "mcp:server.tool"
- _execute_tool enruta al MCPClientManager cuando el nombre empieza por "mcp:"
- Las herramientas nativas siguen funcionando sin cambios
- MCPRegistry.parse_mcp_tool_name descompone correctamente los nombres
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.agents import AgentDefinition, MCPServerConfig, ApprovalMode, ToolPermission
from app.services.agent_executor import AgentExecutor
from app.services.mcp_registry import MCPRegistry


# ---------------------------------------------------------------------------
# MCPRegistry unit tests
# ---------------------------------------------------------------------------

class TestMCPRegistry:
    def test_is_mcp_tool_true(self):
        assert MCPRegistry.is_mcp_tool("mcp:filesystem.read_file") is True
        assert MCPRegistry.is_mcp_tool("mcp:s1000d_csdb.query_related_dms") is True

    def test_is_mcp_tool_false(self):
        assert MCPRegistry.is_mcp_tool("search_documents") is False
        assert MCPRegistry.is_mcp_tool("get_entities") is False
        assert MCPRegistry.is_mcp_tool("") is False

    def test_parse_mcp_tool_name_valid(self):
        server, tool = MCPRegistry.parse_mcp_tool_name("mcp:filesystem.read_file")
        assert server == "filesystem"
        assert tool == "read_file"

    def test_parse_mcp_tool_name_with_underscore(self):
        server, tool = MCPRegistry.parse_mcp_tool_name("mcp:s1000d_csdb.query_related_dms")
        assert server == "s1000d_csdb"
        assert tool == "query_related_dms"

    def test_parse_mcp_tool_name_invalid(self):
        with pytest.raises(ValueError):
            MCPRegistry.parse_mcp_tool_name("mcp:no_separator_here")

    def test_make_mcp_tool_name(self):
        name = MCPRegistry.make_mcp_tool_name("filesystem", "read_file")
        assert name == "mcp:filesystem.read_file"

    def test_agent_can_use_tool_allowed(self):
        registry = MCPRegistry()
        agent = AgentDefinition(
            name="test",
            system_prompt="test",
            mcp_servers=[
                MCPServerConfig(name="filesystem", type="stdio", command="npx", enabled=True)
            ],
        )
        assert registry.agent_can_use_tool(agent, "mcp:filesystem.read_file") is True

    def test_agent_can_use_tool_not_assigned(self):
        registry = MCPRegistry()
        agent = AgentDefinition(
            name="test",
            system_prompt="test",
            mcp_servers=[
                MCPServerConfig(name="filesystem", type="stdio", command="npx", enabled=True)
            ],
        )
        assert registry.agent_can_use_tool(agent, "mcp:playwright.navigate") is False

    def test_agent_can_use_tool_disabled_server(self):
        registry = MCPRegistry()
        agent = AgentDefinition(
            name="test",
            system_prompt="test",
            mcp_servers=[
                MCPServerConfig(name="filesystem", type="stdio", command="npx", enabled=False)
            ],
        )
        assert registry.agent_can_use_tool(agent, "mcp:filesystem.read_file") is False

    def test_get_tool_descriptions_for_agent_empty(self):
        registry = MCPRegistry()
        agent = AgentDefinition(name="test", system_prompt="test")
        assert registry.get_tool_descriptions_for_agent(agent) == []

    def test_get_tool_descriptions_for_agent_with_catalog(self):
        registry = MCPRegistry()

        mock_tool = MagicMock()
        mock_tool.name = "read_file"
        mock_tool.description = "Reads a file"
        registry.register_server_tools("filesystem", [mock_tool])

        agent = AgentDefinition(
            name="test",
            system_prompt="test",
            mcp_servers=[
                MCPServerConfig(name="filesystem", type="stdio", command="npx", enabled=True)
            ],
        )
        descriptions = registry.get_tool_descriptions_for_agent(agent)
        assert len(descriptions) == 1
        assert "mcp:filesystem.read_file" in descriptions[0]
        assert "Reads a file" in descriptions[0]


# ---------------------------------------------------------------------------
# _parse_tool_call tests
# ---------------------------------------------------------------------------

class TestParseToolCall:
    def setup_method(self):
        self.executor = AgentExecutor()
        self.allowed_tools = [ToolPermission.SEARCH_DOCUMENTS]

    def test_parse_native_tool(self):
        response = "[TOOL_CALL]\ntool: search_documents\nargs:\n  query: test\n  top_k: 5\n[/TOOL_CALL]"
        result = self.executor._parse_tool_call(response, self.allowed_tools)
        assert result is not None
        assert result["tool"] == "search_documents"
        assert result["args"]["query"] == "test"

    def test_parse_mcp_tool(self):
        response = "[TOOL_CALL]\ntool: mcp:filesystem.read_file\nargs:\n  path: /input/doc.pdf\n[/TOOL_CALL]"
        result = self.executor._parse_tool_call(response, self.allowed_tools)
        assert result is not None
        assert result["tool"] == "mcp:filesystem.read_file"
        assert result["args"]["path"] == "/input/doc.pdf"

    def test_parse_mcp_tool_not_blocked_by_allowed_list(self):
        # MCP tools are NOT filtered by the native ToolPermission allowed list
        response = "[TOOL_CALL]\ntool: mcp:playwright.navigate\nargs:\n  url: http://localhost\n[/TOOL_CALL]"
        result = self.executor._parse_tool_call(response, [])  # empty allowed list
        assert result is not None
        assert result["tool"] == "mcp:playwright.navigate"

    def test_no_tool_call(self):
        result = self.executor._parse_tool_call("Just a plain response", self.allowed_tools)
        assert result is None

    def test_unknown_native_tool(self):
        response = "[TOOL_CALL]\ntool: nonexistent_tool\nargs:\n[/TOOL_CALL]"
        result = self.executor._parse_tool_call(response, self.allowed_tools)
        assert result is None


# ---------------------------------------------------------------------------
# _execute_tool MCP routing test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_mcp_tool_routes_to_manager():
    """_execute_tool con nombre 'mcp:...' llama a MCPClientManager.call_tool."""
    executor = AgentExecutor()

    agent = AgentDefinition(
        name="test",
        system_prompt="test",
        mcp_servers=[
            MCPServerConfig(name="filesystem", type="stdio", command="npx", enabled=True)
        ],
    )

    from dataclasses import dataclass, field as dc_field
    import asyncio
    from app.models.agents import AgentTask, TaskStatus
    from app.services.agent_executor import ExecutionContext

    task = AgentTask(
        id="task-001",
        agent_id="agent-001",
        library_id="lib-001",
        prompt="test",
        status=TaskStatus.RUNNING,
    )
    context = ExecutionContext(task=task, agent=agent, library_id="lib-001")

    mock_result = {"content": [{"type": "text", "text": "file content here"}], "isError": False}

    with patch("app.services.agent_executor.get_mcp_client_manager") as mock_get_mgr, \
         patch("app.services.agent_executor.get_mcp_registry") as mock_get_reg:

        mock_mgr = AsyncMock()
        mock_mgr.call_tool = AsyncMock(return_value=mock_result)
        mock_get_mgr.return_value = mock_mgr

        mock_reg = MagicMock()
        mock_reg.agent_can_use_tool = MagicMock(return_value=True)
        mock_get_reg.return_value = mock_reg

        result_str, raw = await executor._execute_tool(
            "mcp:filesystem.read_file",
            {"path": "/input/doc.pdf"},
            "lib-001",
            context=context,
        )

    assert "file content here" in result_str
    mock_mgr.call_tool.assert_called_once_with("filesystem", "read_file", {"path": "/input/doc.pdf"})


@pytest.mark.asyncio
async def test_execute_mcp_tool_access_denied():
    """_execute_mcp_tool devuelve error si el agente no tiene acceso al servidor."""
    executor = AgentExecutor()

    agent = AgentDefinition(name="test", system_prompt="test")  # sin mcp_servers

    from app.models.agents import AgentTask, TaskStatus
    from app.services.agent_executor import ExecutionContext

    task = AgentTask(
        id="task-002", agent_id="agent-002", library_id="lib-001",
        prompt="test", status=TaskStatus.RUNNING,
    )
    context = ExecutionContext(task=task, agent=agent, library_id="lib-001")

    with patch("app.services.agent_executor.get_mcp_registry") as mock_get_reg:
        mock_reg = MagicMock()
        mock_reg.agent_can_use_tool = MagicMock(return_value=False)
        mock_get_reg.return_value = mock_reg

        result_str, raw = await executor._execute_tool(
            "mcp:filesystem.read_file",
            {"path": "/input/doc.pdf"},
            "lib-001",
            context=context,
        )

    assert "does not have access" in result_str
    assert raw is None
