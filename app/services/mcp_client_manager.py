"""
MCPClientManager — gestión de conexiones stdio a servidores MCP.

Mantiene un pool de ClientSession (una por servidor) y ofrece
una interfaz uniforme para llamar herramientas y descubrir el catálogo.

Fase 7 — Hardening:
  - Structured JSON logging (logger "mcp.audit") por cada tool call
  - Auto-restart con backoff exponencial en errores de transporte
  - task_id contextvar para correlacionar llamadas con tareas del agente
"""

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.models.agents import MCPServerConfig

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("mcp.audit")

# Context variable: set by the agent executor to correlate MCP calls with tasks.
# Usage: token = mcp_task_id.set("task-uuid"); ... ; mcp_task_id.reset(token)
mcp_task_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_task_id", default="—"
)

# Delays (seconds) between successive restart attempts: 1s → 2s → 4s
_RESTART_DELAYS = (1.0, 2.0, 4.0)

# Exceptions that indicate the MCP subprocess transport has died
try:
    import anyio
    _TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
        BrokenPipeError,
        ConnectionResetError,
        ConnectionAbortedError,
        EOFError,
        anyio.ClosedResourceError,
        anyio.BrokenResourceError,
    )
except (ImportError, AttributeError):
    _TRANSPORT_ERRORS = (
        BrokenPipeError,
        ConnectionResetError,
        ConnectionAbortedError,
        EOFError,
    )


# ---------------------------------------------------------------------------
# Structured audit logging
# ---------------------------------------------------------------------------

def _log_mcp_call(
    server: str,
    tool: str,
    args: dict[str, Any],
    duration_ms: float,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Emite un registro JSON en el logger mcp.audit."""
    args_hash = hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]

    record: dict[str, Any] = {
        "server": server,
        "tool": tool,
        "args_hash": args_hash,
        "duration_ms": round(duration_ms, 1),
        "status": status,
        "task_id": mcp_task_id.get(),
    }
    if error:
        record["error"] = error[:200]  # cap to avoid huge log lines

    _audit_logger.info(json.dumps(record))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class MCPClientManager:
    """
    Singleton que gestiona subprocesos stdio MCP.

    Ciclo de vida:
      - start_server(config)    → lanza el subproceso y crea la ClientSession
      - call_tool(...)          → ejecuta una herramienta con logging + auto-restart
      - list_tools(...)         → descubre las herramientas disponibles
      - restart_server(name)    → reinicia con backoff exponencial
      - stop_all()              → cierra todas las sesiones (llamado en shutdown)
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
    # Auto-restart with exponential backoff
    # ------------------------------------------------------------------

    async def restart_server(self, name: str) -> bool:
        """
        Reinicia un servidor MCP con backoff exponencial.

        Intenta hasta len(_RESTART_DELAYS) veces con delays crecientes.
        Devuelve True si el servidor arrancó correctamente en algún intento.
        """
        config = self._configs.get(name)
        if not config:
            logger.warning("No config found for server '%s'; cannot restart", name)
            return False

        # Tear down dead session without touching _configs yet
        session = self._sessions.pop(name, None)
        ctx = self._contexts.pop(name, None)
        self._configs.pop(name, None)
        try:
            if session:
                await session.__aexit__(None, None, None)
            if ctx:
                await ctx.__aexit__(None, None, None)
        except Exception:
            pass

        for attempt, delay in enumerate(_RESTART_DELAYS, 1):
            logger.info(
                "Restarting MCP server '%s' — attempt %d/%d (wait %.0fs)",
                name, attempt, len(_RESTART_DELAYS), delay,
            )
            await asyncio.sleep(delay)
            if await self.start_server(config):
                logger.info("MCP server '%s' restarted successfully", name)
                return True

        logger.error(
            "MCP server '%s' could not be restarted after %d attempts",
            name, len(_RESTART_DELAYS),
        )
        return False

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

        - Emite un registro estructurado JSON en el logger "mcp.audit".
        - En errores de transporte (subprocess muerto), intenta restart + retry.

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
        t0 = time.monotonic()

        def _to_dict(result: Any) -> dict[str, Any]:
            return {
                "content": [
                    {"type": c.type, "text": getattr(c, "text", str(c))}
                    for c in result.content
                ],
                "isError": result.isError if hasattr(result, "isError") else False,
            }

        try:
            result = await asyncio.wait_for(
                self._sessions[server_name].call_tool(tool_name, args),
                timeout=timeout,
            )
            duration_ms = (time.monotonic() - t0) * 1000
            _log_mcp_call(server_name, tool_name, args, duration_ms, "ok")
            return _to_dict(result)

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_mcp_call(server_name, tool_name, args, duration_ms, "timeout")
            raise TimeoutError(
                f"MCP tool '{server_name}.{tool_name}' timed out after {timeout}s"
            )

        except _TRANSPORT_ERRORS as exc:
            # Subprocess likely died — attempt restart then retry once
            duration_ms = (time.monotonic() - t0) * 1000
            _log_mcp_call(server_name, tool_name, args, duration_ms, "transport_error", str(exc))
            logger.warning(
                "Transport error on '%s.%s' — attempting restart: %s",
                server_name, tool_name, exc,
            )
            if await self.restart_server(server_name):
                t1 = time.monotonic()
                result = await asyncio.wait_for(
                    self._sessions[server_name].call_tool(tool_name, args),
                    timeout=timeout,
                )
                _log_mcp_call(server_name, tool_name, args, (time.monotonic() - t1) * 1000, "ok_after_restart")
                return _to_dict(result)
            raise

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            _log_mcp_call(server_name, tool_name, args, duration_ms, "error", str(exc))
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
