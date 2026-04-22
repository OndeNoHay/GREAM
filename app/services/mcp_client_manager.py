"""
MCPClientManager — gestión de conexiones stdio a servidores MCP.

Mantiene un pool de ClientSession (una por servidor) y ofrece
una interfaz uniforme para llamar herramientas y descubrir el catálogo.
"""

import asyncio
import logging
import os
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.models.agents import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPClientManager:
    """
    Singleton que gestiona subprocesos stdio MCP.

    Ciclo de vida:
      - start_server(config)  → lanza el subproceso y crea la ClientSession
      - call_tool(...)        → ejecuta una herramienta en el servidor indicado
      - list_tools(...)       → descubre las herramientas disponibles
      - stop_all()            → cierra todas las sesiones (llamado en shutdown)
    """

    _instance: Optional["MCPClientManager"] = None

    def __new__(cls) -> "MCPClientManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: dict[str, ClientSession] = {}
            cls._instance._contexts: dict[str, Any] = {}
            cls._instance._configs: dict[str, MCPServerConfig] = {}
        return cls._instance

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start_server(self, config: MCPServerConfig) -> bool:
        """
        Lanza el subproceso stdio y negocia la sesión MCP.

        Devuelve True si el servidor arrancó correctamente.
        """
        if not config.enabled:
            logger.debug("MCP server %s is disabled, skipping", config.name)
            return False

        if config.type != "stdio":
            logger.warning("Only stdio MCP servers are supported; skipping %s", config.name)
            return False

        if config.name in self._sessions:
            logger.debug("MCP server %s already running", config.name)
            return True

        if not config.command:
            logger.error("MCP server %s has no command configured", config.name)
            return False

        env = {**os.environ, **config.env} if config.env else None

        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=env,
        )

        try:
            ctx = stdio_client(params)
            read_stream, write_stream = await ctx.__aenter__()
            session = ClientSession(read_stream, write_stream)
            await session.__aenter__()
            await session.initialize()

            self._sessions[config.name] = session
            self._contexts[config.name] = ctx
            self._configs[config.name] = config

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            logger.info(
                "MCP server '%s' started — %d tools: %s",
                config.name, len(tool_names), tool_names,
            )
            return True

        except Exception as exc:
            logger.error("Failed to start MCP server '%s': %s", config.name, exc)
            return False

    async def stop_all(self) -> None:
        """Cierra todas las sesiones MCP activas."""
        for name in list(self._sessions.keys()):
            await self._stop_server(name)
        logger.info("All MCP servers stopped")

    async def _stop_server(self, name: str) -> None:
        session = self._sessions.pop(name, None)
        ctx = self._contexts.pop(name, None)
        self._configs.pop(name, None)
        try:
            if session:
                await session.__aexit__(None, None, None)
            if ctx:
                await ctx.__aexit__(None, None, None)
        except Exception as exc:
            logger.warning("Error stopping MCP server '%s': %s", name, exc)

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------

    async def list_tools(self, server_name: str) -> list[Any]:
        """
        Devuelve la lista de Tool objects del servidor indicado.

        Retorna lista vacía si el servidor no está disponible.
        """
        session = self._sessions.get(server_name)
        if not session:
            return []
        try:
            result = await session.list_tools()
            return result.tools
        except Exception as exc:
            logger.error("list_tools failed for '%s': %s", server_name, exc)
            return []

    async def list_all_tools(self) -> dict[str, list[Any]]:
        """Devuelve el catálogo completo {server_name: [Tool, ...]}."""
        catalog: dict[str, list[Any]] = {}
        for name in list(self._sessions.keys()):
            catalog[name] = await self.list_tools(name)
        return catalog

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Ejecuta una herramienta MCP y devuelve el resultado como dict.

        El resultado tiene la forma:
          {"content": [{"type": "text", "text": "..."}, ...], "isError": False}
        """
        session = self._sessions.get(server_name)
        if not session:
            raise RuntimeError(
                f"MCP server '{server_name}' is not running. "
                "Check that it is enabled in config/mcp_servers.yaml."
            )

        config = self._configs.get(server_name)
        timeout = config.timeout_seconds if config else 30

        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, args),
                timeout=timeout,
            )
            # Serializar a dict plano para que el executor pueda manejarlo
            return {
                "content": [
                    {"type": c.type, "text": getattr(c, "text", str(c))}
                    for c in result.content
                ],
                "isError": result.isError if hasattr(result, "isError") else False,
            }
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"MCP tool '{server_name}.{tool_name}' timed out after {timeout}s"
            )
        except Exception as exc:
            logger.error(
                "call_tool failed: server=%s tool=%s: %s", server_name, tool_name, exc
            )
            raise

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def running_servers(self) -> list[str]:
        """Nombres de los servidores MCP activos."""
        return list(self._sessions.keys())

    def is_running(self, server_name: str) -> bool:
        return server_name in self._sessions


_manager: Optional[MCPClientManager] = None


def get_mcp_client_manager() -> MCPClientManager:
    """Devuelve el singleton MCPClientManager."""
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager
