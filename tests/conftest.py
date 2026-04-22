"""
Fixtures base para los tests de GRAEM.

Proporciona mocks ligeros de los servicios principales para tests unitarios
sin necesidad de bases de datos reales (Kùzu, ChromaDB) ni subprocesos MCP.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.agents import (
    AgentDefinition,
    MCPServerConfig,
    ApprovalMode,
    ToolPermission,
)


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: mark test as integration (requires real services)"
    )


# ---------------------------------------------------------------------------
# Agent fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_agent() -> AgentDefinition:
    """Agent mínimo con herramienta search_documents."""
    return AgentDefinition(
        id="test-agent-001",
        name="Test Agent",
        description="Agent for unit tests",
        system_prompt="You are a test agent. Use tools and complete tasks.",
        tools=[ToolPermission.SEARCH_DOCUMENTS],
        approval_mode=ApprovalMode.NEVER,
        max_iterations=5,
        temperature=0.0,
    )


@pytest.fixture
def mcp_agent(base_agent: AgentDefinition) -> AgentDefinition:
    """Agent con un servidor MCP mock configurado."""
    base_agent.mcp_servers = [
        MCPServerConfig(
            name="mock_server",
            type="stdio",
            command="python",
            args=["-c", "print('hello')"],
            enabled=True,
        )
    ]
    return base_agent


# ---------------------------------------------------------------------------
# MCP service mocks
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_mcp_client_manager():
    """Mock de MCPClientManager para tests que no necesitan subprocesos reales."""
    with patch("app.services.mcp_client_manager.MCPClientManager") as MockClass:
        instance = MockClass.return_value
        instance.call_tool = AsyncMock(
            return_value={"content": [{"type": "text", "text": "mock result"}]}
        )
        instance.list_tools = AsyncMock(return_value=[])
        instance.start_server = AsyncMock()
        instance.stop_all = AsyncMock()
        yield instance


@pytest.fixture
def mock_mcp_registry():
    """Mock de MCPRegistry."""
    with patch("app.services.mcp_registry.MCPRegistry") as MockClass:
        instance = MockClass.return_value
        instance.get_tool_descriptions_for_agent = MagicMock(return_value=[])
        instance.lookup_server_tool = MagicMock(return_value=("mock_server", "mock_tool"))
        instance.is_mcp_tool = MagicMock(return_value=True)
        yield instance


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Event loop compartido para la sesión de tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
