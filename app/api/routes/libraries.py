"""
Library management API routes.

Handles CRUD operations for document libraries.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.library_manager import get_library_manager, Library
from app.services.vector_db import get_vector_db_service
from app.services.graph_db import get_graph_db_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/libraries", tags=["Libraries"])


class LibraryCreate(BaseModel):
    """Request model for creating a library."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class LibraryUpdate(BaseModel):
    """Request model for updating a library."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)


class LibraryResponse(BaseModel):
    """Response model for a library."""
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str
    document_count: int


class LibraryListResponse(BaseModel):
    """Response model for library list."""
    libraries: list[LibraryResponse]
    total: int


@router.get(
    "",
    response_model=LibraryListResponse,
    summary="List all libraries"
)
async def list_libraries() -> LibraryListResponse:
    """Get all libraries."""
    library_mgr = get_library_manager()
    libraries = library_mgr.list_libraries()

    return LibraryListResponse(
        libraries=[
            LibraryResponse(
                id=lib.id,
                name=lib.name,
                description=lib.description,
                created_at=lib.created_at,
                updated_at=lib.updated_at,
                document_count=lib.document_count
            )
            for lib in libraries
        ],
        total=len(libraries)
    )


@router.get(
    "/{library_id}",
    response_model=LibraryResponse,
    summary="Get a library by ID"
)
async def get_library(library_id: str) -> LibraryResponse:
    """Get a specific library by ID."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)

    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    return LibraryResponse(
        id=library.id,
        name=library.name,
        description=library.description,
        created_at=library.created_at,
        updated_at=library.updated_at,
        document_count=library.document_count
    )


@router.post(
    "",
    response_model=LibraryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new library"
)
async def create_library(request: LibraryCreate) -> LibraryResponse:
    """Create a new document library."""
    library_mgr = get_library_manager()

    try:
        library = library_mgr.create_library(
            name=request.name,
            description=request.description
        )

        logger.info(f"Created library: {library.name}")

        return LibraryResponse(
            id=library.id,
            name=library.name,
            description=library.description,
            created_at=library.created_at,
            updated_at=library.updated_at,
            document_count=library.document_count
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.put(
    "/{library_id}",
    response_model=LibraryResponse,
    summary="Update a library"
)
async def update_library(library_id: str, request: LibraryUpdate) -> LibraryResponse:
    """Update an existing library."""
    library_mgr = get_library_manager()

    try:
        library = library_mgr.update_library(
            library_id=library_id,
            name=request.name,
            description=request.description
        )

        logger.info(f"Updated library: {library.name}")

        return LibraryResponse(
            id=library.id,
            name=library.name,
            description=library.description,
            created_at=library.created_at,
            updated_at=library.updated_at,
            document_count=library.document_count
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if "not found" in str(e).lower()
            else status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete(
    "/{library_id}",
    summary="Delete a library"
)
async def delete_library(library_id: str) -> dict:
    """
    Delete a library and all its data.

    WARNING: This will delete all documents in the library.
    """
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)

    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    try:
        # Delete from vector DB
        vector_db = get_vector_db_service()
        vector_db.delete_library(library_id)

        # Delete from graph DB
        graph_db = get_graph_db_service()
        graph_db.delete_library(library_id)

        # Delete library metadata
        library_mgr.delete_library(library_id)

        logger.info(f"Deleted library: {library.name}")

        return {
            "success": True,
            "message": f"Library '{library.name}' deleted successfully"
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get(
    "/{library_id}/stats",
    summary="Get library statistics"
)
async def get_library_stats(library_id: str) -> dict:
    """Get statistics for a library."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)

    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    vector_db = get_vector_db_service()
    graph_db = get_graph_db_service()

    return {
        "library_id": library_id,
        "name": library.name,
        "document_count": library.document_count,
        "chunk_count": vector_db.count(library_id),
        "entity_count": graph_db.count_nodes(library_id, "Entity"),
        "sources": vector_db.list_sources(library_id)
    }
