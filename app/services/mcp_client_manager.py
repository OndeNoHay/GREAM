"""
MCPClientManager — gestión de conexiones stdio a servidores MCP.

Arquitectura de tareas:
  Cada servidor MCP corre como una tarea asyncio de larga vida que posee
  el contexto stdio_client y ClientSession completo desde el arranque hasta
  el apagado. Esto garantiza que los cancel scopes de anyio siempre se abren
  y cierran en la misma tarea, evitando RuntimeError "Attempted to exit cancel
  scope in a different task".

  start_server()  → lanza la tarea y espera a que la sesión esté lista
  call_tool()     → envía la llamada desde cualquier tarea; la sesión es segura
                    cross-task porque asyncio.Future puede ser awaited desde
                    cualquier tarea
  stop_all()      → señaliza a todas las tareas para que terminen limpiamente
"""

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import pathlib
import sys
import time
from typing import Any, Optional

# Derive the venv Python from the project root (this file is at
# app/services/mcp_client_manager.py, so project root is 3 levels up).
# Using sys.executable is unreliable when uvicorn --reload spawns workers via
# multiprocessing.spawn, which inherits the BASE Python (Python311) rather than
# the venv Python, causing ImportError for packages only installed in the venv
# (e.g. python-docx, python-pptx).
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
_VENV_PYTHON_CANDIDATES = [
    _PROJECT_ROOT / "venv" / "Scripts" / "python.exe",  # Windows
    _PROJECT_ROOT / "venv" / "bin" / "python",          # Linux/Mac
    _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
    _PROJECT_ROOT / ".venv" / "bin" / "python",
]
_VENV_PYTHON = next(
    (str(p) for p in _VENV_PYTHON_CANDIDATES if p.exists()),
    sys.executable,  # fallback
)

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.models.agents import MCPServerConfig

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("mcp.audit")

# Context variable: set by the agent executor to correlate MCP calls with tasks.
mcp_task_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mcp_task_id", default="—"
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
        record["error"] = error[:200]
    _audit_logger.info(json.dumps(record))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class MCPClientManager:
    """
    Singleton que gestiona subprocesos stdio MCP.

    Cada servidor corre en su propia tarea asyncio de larga vida que posee
    el contexto stdio_client y ClientSession. Las llamadas a call_tool()
    pueden venir de cualquier tarea — asyncio.Future es cross-task safe.
    """

    _instance: Optional["MCPClientManager"] = None

    def __new__(cls) -> "MCPClientManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # Active ClientSession objects, keyed by server name
            cls._instance._sessions: dict[str, ClientSession] = {}
            # Config for each running server (needed for timeout in call_tool)
            cls._instance._configs: dict[str, MCPServerConfig] = {}
            # Long-lived asyncio tasks, one per server
            cls._instance._server_tasks: dict[str, asyncio.Task] = {}
            # Stop events — set() triggers graceful shutdown of the server task
            cls._instance._stop_events: dict[str, asyncio.Event] = {}
        return cls._instance

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start_server(self, config: MCPServerConfig) -> bool:
        """
        Lanza una tarea de larga vida para el servidor MCP y espera a que
        la sesión esté lista para recibir llamadas.

        La tarea mantiene el contexto stdio_client y ClientSession abierto
        hasta que se llame a stop_all().
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

        # Resolve bare "python"/"python3" to the venv interpreter
        cmd = config.command
        if cmd in ("python", "python3"):
            cmd = _VENV_PYTHON

        env = {**os.environ, **config.env} if config.env else None
        params = StdioServerParameters(command=cmd, args=config.args, env=env)
        timeout_s = config.timeout_seconds or 30

        # Coordination events
        ready_event: asyncio.Event = asyncio.Event()
        stop_event: asyncio.Event = asyncio.Event()
        error_holder: list[Exception] = []

        async def _run_server() -> None:
            """
            Tarea de larga vida que posee el ciclo completo del servidor MCP.

            El contexto stdio_client y la ClientSession se abren y cierran
            dentro de esta misma tarea, por lo que los cancel scopes de anyio
            nunca cruzan tareas.
            """
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as sess:
                        await sess.initialize()
                        tools_result = await sess.list_tools()
                        tool_names = [t.name for t in tools_result.tools]

                        # Register session — visible to call_tool() immediately
                        self._sessions[config.name] = sess
                        self._configs[config.name] = config

                        logger.info(
                            "MCP server '%s' started — %d tools: %s",
                            config.name, len(tool_names), tool_names,
                        )
                        ready_event.set()

                        # Keep the session alive until stop is requested
                        await stop_event.wait()

            except asyncio.CancelledError:
                logger.info("MCP server '%s' task cancelled", config.name)
                raise
            except Exception as exc:
                logger.error("MCP server '%s' error: %s", config.name, exc)
                error_holder.append(exc)
                ready_event.set()  # Unblock waiter even on failure
            finally:
                # Always clean up — whether normal exit or error
                self._sessions.pop(config.name, None)
                self._configs.pop(config.name, None)
                self._server_tasks.pop(config.name, None)
                self._stop_events.pop(config.name, None)

        task = asyncio.create_task(_run_server(), name=f"mcp-{config.name}")
        self._server_tasks[config.name] = task
        self._stop_events[config.name] = stop_event

        # Wait for the server to signal it is ready (or fail)
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.error(
                "MCP server '%s' startup timed out after %ss — stopping",
                config.name, timeout_s,
            )
            stop_event.set()
            task.cancel()
            return False

        if error_holder:
            logger.error(
                "MCP server '%s' failed to start: %s", config.name, error_holder[0]
            )
            return False

        return True

    async def stop_all(self) -> None:
        """Signal all server tasks to stop and wait for clean exit."""
        names = list(self._stop_events.keys())
        for name in names:
            ev = self._stop_events.get(name)
            if ev:
                ev.set()

        tasks = [t for t in self._server_tasks.values() if not t.done()]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                for t in tasks:
                    if not t.done():
                        t.cancel()

        logger.info("All MCP servers stopped")

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------

    async def list_tools(self, server_name: str) -> list[Any]:
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
        Ejecuta una herramienta MCP desde cualquier tarea asyncio.

        La sesión es safe cross-task: la escritura va al stream del
        subproceso y la respuesta llega vía asyncio.Future que puede
        ser awaited desde cualquier tarea.
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
                session.call_tool(tool_name, args),
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
        return list(self._sessions.keys())

    def is_running(self, server_name: str) -> bool:
        return server_name in self._sessions


_manager: Optional[MCPClientManager] = None


def get_mcp_client_manager() -> MCPClientManager:
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager
