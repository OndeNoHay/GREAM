"""
API routes for GraphRagExec.
"""

from app.api.routes.documents import router as documents_router
from app.api.routes.search import router as search_router, graph_router
from app.api.routes.libraries import router as libraries_router
from app.api.routes.settings import router as settings_router
from app.api.routes.google_drive import router as google_drive_router
from app.api.routes.agents import router as agents_router
from app.api.routes.logs import router as logs_router
from app.api.routes.viewer import router as viewer_router
from app.api.routes.mcp import router as mcp_router

__all__ = [
    "documents_router",
    "search_router",
    "graph_router",
    "libraries_router",
    "settings_router",
    "google_drive_router",
    "agents_router",
    "logs_router",
    "viewer_router",
    "mcp_router",
]
