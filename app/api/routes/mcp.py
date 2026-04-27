"""
MCP server management API routes.

Provides endpoints to inspect and restart MCP (Model Context Protocol) servers
at runtime, without restarting the full application.
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.models.agents import MCPServerConfig
from app.services.mcp_client_manager import get_mcp_client_manager
from app.services.mcp_registry import get_mcp_registry


class MCPServerPatch(BaseModel):
    enabled: bool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mcp", tags=["MCP"])

_MCP_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "mcp_servers.yaml"


def _load_yaml_servers() -> list[dict]:
    """Return all server entries from config/mcp_servers.yaml."""
    if not _MCP_CONFIG_PATH.exists():
        return []
    try:
        with open(_MCP_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return raw.get("servers", [])
    except Exception as exc:
        logger.error("Failed to load MCP config: %s", exc)
        return []


def _server_status(name: str, config: dict) -> dict[str, Any]:
    """Build a status dict for one server."""
    manager = get_mcp_client_manager()
    registry = get_mcp_registry()

    running = manager.is_running(name)
    tools = registry.catalog.get(name, [])

    return {
        "name": name,
        "enabled": config.get("enabled", False),
        "running": running,
        "tool_count": len(tools),
        "tools": [t.name for t in tools],
        "command": config.get("command", ""),
        "args": config.get("args", []),
        "timeout_seconds": config.get("timeout_seconds", 30),
        "type": config.get("type", "stdio"),
    }


@router.get("/servers", summary="List all MCP servers with status")
async def list_mcp_servers() -> dict:
    """
    Returns all MCP servers defined in config/mcp_servers.yaml,
    each annotated with its current running state and tool count.
    """
    entries = _load_yaml_servers()
    servers = [_server_status(e["name"], e) for e in entries if "name" in e]
    running_count = sum(1 for s in servers if s["running"])
    return {
        "servers": servers,
        "total": len(servers),
        "running": running_count,
    }


@router.post("/servers/{server_name}/restart", summary="Restart a single MCP server")
async def restart_mcp_server(server_name: str) -> dict:
    """
    Stops the named MCP server (if running) and starts it again from config.
    Also refreshes the MCPRegistry catalog.
    """
    entries = _load_yaml_servers()
    config_dict = next((e for e in entries if e.get("name") == server_name), None)
    if config_dict is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_name}' not found in config/mcp_servers.yaml",
        )

    manager = get_mcp_client_manager()
    registry = get_mcp_registry()

    # Stop if running
    if manager.is_running(server_name):
        await manager.stop_server(server_name)

    # Start
    config = MCPServerConfig(**config_dict)
    ok = await manager.start_server(config)
    await registry.refresh()

    return {
        "success": ok,
        "name": server_name,
        **_server_status(server_name, config_dict),
    }


@router.post("/restart-all", summary="Restart all enabled MCP servers")
async def restart_all_mcp_servers() -> dict:
    """
    Stops every running MCP server and restarts all enabled ones from config.
    """
    entries = _load_yaml_servers()
    manager = get_mcp_client_manager()
    registry = get_mcp_registry()

    # Stop all currently running
    await manager.stop_all()

    # Start all enabled
    results = []
    for entry in entries:
        name = entry.get("name")
        if not name or not entry.get("enabled", False):
            continue
        config = MCPServerConfig(**entry)
        ok = await manager.start_server(config)
        results.append({"name": name, "started": ok})

    await registry.refresh()

    return {
        "success": True,
        "results": results,
        "running": len(manager.running_servers),
    }


def _save_yaml_servers(entries: list[dict]) -> None:
    """Write the servers list back to config/mcp_servers.yaml, preserving structure."""
    try:
        if _MCP_CONFIG_PATH.exists():
            with open(_MCP_CONFIG_PATH, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        else:
            raw = {}
        raw["servers"] = entries
        with open(_MCP_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except Exception as exc:
        logger.error("Failed to save MCP config: %s", exc)
        raise


@router.patch("/servers/{server_name}", summary="Enable or disable an MCP server")
async def patch_mcp_server(server_name: str, body: MCPServerPatch) -> dict:
    """
    Enables or disables an MCP server:
    - enable  (enabled=true):  updates YAML, starts the server, refreshes registry.
    - disable (enabled=false): updates YAML, stops the server, refreshes registry.
    """
    entries = _load_yaml_servers()
    entry = next((e for e in entries if e.get("name") == server_name), None)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_name}' not found in config/mcp_servers.yaml",
        )

    manager = get_mcp_client_manager()
    registry = get_mcp_registry()

    # Persist the change
    entry["enabled"] = body.enabled
    _save_yaml_servers(entries)

    if body.enabled:
        # Start if not already running
        if not manager.is_running(server_name):
            config = MCPServerConfig(**entry)
            await manager.start_server(config)
    else:
        # Stop if running
        if manager.is_running(server_name):
            await manager.stop_server(server_name)

    await registry.refresh()

    return {
        "success": True,
        "name": server_name,
        **_server_status(server_name, entry),
    }
