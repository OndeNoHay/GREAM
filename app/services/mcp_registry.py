"""
MCPRegistry — catálogo dinámico de herramientas MCP.

Descubre las tools de cada servidor al arranque y provee:
  - subset injection: solo las tools de los servidores asignados al agente activo
  - resolución de nombres: "mcp:server.tool" → (server_name, tool_name)
"""

import logging
from typing import Any, Optional

from app.models.agents import AgentDefinition
from app.services.mcp_client_manager import get_mcp_client_manager

logger = logging.getLogger(__name__)

# Formato del nombre de tool MCP tal y como aparece en el system prompt y los tool calls
MCP_TOOL_PREFIX = "mcp:"
MCP_TOOL_SEP = "."


class MCPRegistry:
    """
    Catálogo de herramientas MCP descubiertas en el arranque.

    El catálogo se construye llamando a refresh() después de que los servidores
    MCP hayan arrancado. Se puede llamar de nuevo si se añaden servidores en caliente.
    """

    _instance: Optional["MCPRegistry"] = None

    def __new__(cls) -> "MCPRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # {server_name: [Tool, ...]}
            cls._instance._catalog: dict[str, list[Any]] = {}
        return cls._instance

    # ------------------------------------------------------------------
    # Catálogo
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """
        Re-descubre todas las herramientas de los servidores MCP activos.

        Llamar después de start_server() para tener el catálogo actualizado.
        """
        manager = get_mcp_client_manager()
        self._catalog = await manager.list_all_tools()
        total = sum(len(tools) for tools in self._catalog.values())
        logger.info(
            "MCPRegistry refreshed: %d servers, %d tools total",
            len(self._catalog), total,
        )

    def register_server_tools(self, server_name: str, tools: list[Any]) -> None:
        """Registra manualmente las tools de un servidor (útil en tests)."""
        self._catalog[server_name] = tools

    # ------------------------------------------------------------------
    # Resolución de nombres
    # ------------------------------------------------------------------

    @staticmethod
    def is_mcp_tool(tool_name: str) -> bool:
        """True si el nombre tiene el prefijo "mcp:"."""
        return tool_name.startswith(MCP_TOOL_PREFIX)

    @staticmethod
    def parse_mcp_tool_name(mcp_tool_name: str) -> tuple[str, str]:
        """
        Descompone "mcp:server_name.tool_name" en (server_name, tool_name).

        Lanza ValueError si el formato no es válido.
        """
        without_prefix = mcp_tool_name[len(MCP_TOOL_PREFIX):]
        if MCP_TOOL_SEP not in without_prefix:
            raise ValueError(
                f"Invalid MCP tool name '{mcp_tool_name}'. "
                f"Expected format: mcp:<server_name>.<tool_name>"
            )
        server_name, tool_name = without_prefix.split(MCP_TOOL_SEP, 1)
        return server_name, tool_name

    @staticmethod
    def make_mcp_tool_name(server_name: str, tool_name: str) -> str:
        """Construye el nombre completo "mcp:server.tool"."""
        return f"{MCP_TOOL_PREFIX}{server_name}{MCP_TOOL_SEP}{tool_name}"

    # ------------------------------------------------------------------
    # Subset injection para system prompt
    # ------------------------------------------------------------------

    def get_tool_descriptions_for_agent(self, agent: AgentDefinition) -> list[str]:
        """
        Devuelve las líneas de descripción de herramientas MCP para el system
        prompt del agente, limitadas a los servidores que el agente tiene asignados.

        Formato de cada línea:
          "- mcp:server.tool_name: <description>"
        """
        if not agent.mcp_servers:
            return []

        assigned_server_names = {
            srv.name for srv in agent.mcp_servers if srv.enabled
        }

        lines: list[str] = []
        for server_name, tools in self._catalog.items():
            if server_name not in assigned_server_names:
                continue
            for tool in tools:
                full_name = self.make_mcp_tool_name(server_name, tool.name)
                description = tool.description or "(sin descripción)"
                # Include parameter names from inputSchema so the LLM knows
                # exactly which kwargs to use instead of inventing them.
                params_str = ""
                schema = getattr(tool, "inputSchema", None)
                if isinstance(schema, dict):
                    props = schema.get("properties", {})
                    if props:
                        params_str = f"({', '.join(props.keys())})"
                lines.append(f"- {full_name}{params_str}: {description}")

        return lines

    def get_all_tool_descriptions(self) -> list[str]:
        """Devuelve descripciones de TODAS las tools (útil para debug/admin)."""
        lines: list[str] = []
        for server_name, tools in self._catalog.items():
            for tool in tools:
                full_name = self.make_mcp_tool_name(server_name, tool.name)
                lines.append(f"- {full_name}: {tool.description or '(sin descripción)'}")
        return lines

    # ------------------------------------------------------------------
    # Validación de acceso
    # ------------------------------------------------------------------

    def agent_can_use_tool(self, agent: AgentDefinition, mcp_tool_name: str) -> bool:
        """
        True si el agente tiene el servidor de la herramienta en su lista
        y el servidor está habilitado.
        """
        try:
            server_name, _ = self.parse_mcp_tool_name(mcp_tool_name)
        except ValueError:
            return False

        return any(
            srv.name == server_name and srv.enabled
            for srv in agent.mcp_servers
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def catalog(self) -> dict[str, list[Any]]:
        return dict(self._catalog)

    def tool_count(self) -> int:
        return sum(len(t) for t in self._catalog.values())


_registry: Optional[MCPRegistry] = None


def get_mcp_registry() -> MCPRegistry:
    """Devuelve el singleton MCPRegistry."""
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry
