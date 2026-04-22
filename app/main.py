"""
FastAPI application entry point for GraphRagExec.

This module initializes the web server, loads all services,
configures API routes, and serves the web interface.
"""

import logging
import yaml
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __app_name__
from app.config import (
    get_app_settings,
    ensure_data_directories,
    log_startup_info,
    APP_VERSION
)
from app.api.routes import (
    documents_router,
    search_router,
    graph_router,
    libraries_router,
    settings_router,
    google_drive_router,
    agents_router,
    logs_router,
    viewer_router,
)
from app.services.vector_db import get_vector_db_service
from app.services.graph_db import get_graph_db_service
from app.services.library_manager import get_library_manager
from app.services.mcp_client_manager import get_mcp_client_manager
from app.services.mcp_registry import get_mcp_registry
from app.models.agents import MCPServerConfig

logger = logging.getLogger(__name__)

# Get the app directory path
APP_DIR = Path(__file__).parent
_MCP_CONFIG_PATH = Path(__file__).parent.parent / "config" / "mcp_servers.yaml"


def _start_mcp_servers() -> None:
    """
    Carga config/mcp_servers.yaml y arranca en background los servidores habilitados.

    El arranque es asíncrono (fire-and-forget) para no bloquear el startup de FastAPI.
    Los servidores estarán disponibles unos segundos después del arranque.
    """
    if not _MCP_CONFIG_PATH.exists():
        logger.info("No MCP config found at %s — skipping MCP startup", _MCP_CONFIG_PATH)
        return

    try:
        with open(_MCP_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as exc:
        logger.error("Failed to load MCP config: %s", exc)
        return

    servers = raw.get("servers", [])
    enabled = [s for s in servers if s.get("enabled", False)]

    if not enabled:
        logger.info("No MCP servers enabled in %s", _MCP_CONFIG_PATH.name)
        return

    import asyncio

    mcp_manager = get_mcp_client_manager()
    registry = get_mcp_registry()

    async def _launch_all() -> None:
        for srv_dict in enabled:
            config = MCPServerConfig(**srv_dict)
            await mcp_manager.start_server(config)
        await registry.refresh()

    # Schedule in the running event loop (FastAPI's lifespan is already async)
    asyncio.ensure_future(_launch_all())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting GraphRagExec server...")
    log_startup_info()

    # Ensure data directories exist
    logger.info("Ensuring data directories...")
    ensure_data_directories()

    # Initialize databases
    logger.info("Initializing vector database...")
    vector_db = get_vector_db_service()
    vector_db.initialize()

    logger.info("Initializing graph database...")
    graph_db = get_graph_db_service()
    graph_db.initialize()

    # Initialize library manager (creates default library if none exist)
    logger.info("Initializing library manager...")
    get_library_manager()

    # Start MCP servers declared in config/mcp_servers.yaml
    _start_mcp_servers()

    logger.info("All services initialized successfully!")
    logger.info("Server is ready to accept requests.")

    yield

    # Shutdown: close all MCP sessions
    logger.info("Stopping MCP servers...")
    await get_mcp_client_manager().stop_all()

    logger.info("Shutting down GRAEM server...")


# Create FastAPI application
app = FastAPI(
    title=__app_name__,
    description="""
    GraphRagExec - Local AI Server

    A hybrid search system combining vector similarity (ChromaDB) and
    graph traversal (Kùzu) for intelligent document retrieval.

    ## Features

    - **Document Libraries**: Organize documents into separate libraries
    - **Multiple Formats**: Import PDF, DOCX, TXT, Markdown, Excel
    - **Hybrid Search**: Combine vector and graph search for better results
    - **External AI APIs**: Use OpenAI-compatible APIs (Ollama, OpenAI, etc.)
    - **Persistent Storage**: Data persists in %APPDATA% across updates
    - **Web Interface**: Built-in web UI with drag-and-drop support
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all for local development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception
) -> JSONResponse:
    """Handle uncaught exceptions globally."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    settings = get_app_settings()
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred",
            "details": str(exc) if settings.debug else None
        }
    )


# Include API routers
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(libraries_router)
app.include_router(settings_router)
app.include_router(google_drive_router)
app.include_router(agents_router)
app.include_router(graph_router)
app.include_router(logs_router)
app.include_router(viewer_router)


# Mount static files
static_path = APP_DIR / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# Health check endpoint
@app.get("/api/health", tags=["System"])
async def health_check() -> dict:
    """Check health status of all services."""
    vector_db = get_vector_db_service()
    graph_db = get_graph_db_service()

    return {
        "status": "healthy" if vector_db.is_initialized and graph_db.is_initialized else "degraded",
        "version": APP_VERSION,
        "vector_db": vector_db.get_status(),
        "graph_db": graph_db.get_status()
    }


# Serve web interface
@app.get("/", tags=["UI"])
async def serve_index() -> FileResponse:
    """Serve the main web interface."""
    index_path = APP_DIR / "static" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        content={"message": "Web interface not found", "api_docs": "/docs"},
        status_code=404
    )
