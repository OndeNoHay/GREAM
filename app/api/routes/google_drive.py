"""
Google Drive API routes.

Handles authentication, folder browsing, and file import from Google Drive.
"""

import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings_manager
from app.services.google_drive import get_google_drive_service
from app.services.document_processor import get_document_processor
from app.services.ai_client import get_ai_client
from app.services.vector_db import get_vector_db_service
from app.services.graph_db import get_graph_db_service
from app.services.library_manager import get_library_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/google-drive", tags=["Google Drive"])


# =============================================================================
# Request/Response Models
# =============================================================================

class AuthStatusResponse(BaseModel):
    """Response for authentication status."""
    available: bool
    authenticated: bool
    email: Optional[str] = None
    has_credentials: bool


class AuthStartRequest(BaseModel):
    """Request to start OAuth flow with credentials JSON."""
    credentials_json: str


class AuthStartResponse(BaseModel):
    """Response with OAuth authorization URL."""
    success: bool
    auth_url: Optional[str] = None
    message: str


class AuthCompleteRequest(BaseModel):
    """Request to complete OAuth with auth code."""
    auth_code: str


class AuthCompleteResponse(BaseModel):
    """Response for OAuth completion."""
    success: bool
    email: Optional[str] = None
    message: str


class FileInfo(BaseModel):
    """Google Drive file information."""
    id: str
    name: str
    mimeType: str
    modifiedTime: Optional[str] = None
    size: Optional[str] = None
    isFolder: bool
    isGoogleWorkspace: bool
    supported: bool
    typeLabel: str = ""


class PathItem(BaseModel):
    """Breadcrumb path item."""
    id: str
    name: str


class FolderListResponse(BaseModel):
    """Response for folder listing."""
    files: list[FileInfo]
    path: list[PathItem]
    error: Optional[str] = None


class ImportRequest(BaseModel):
    """Request to import files from Google Drive."""
    file_ids: list[str]
    library_id: str


# =============================================================================
# Authentication Endpoints
# =============================================================================

@router.get(
    "/status",
    response_model=AuthStatusResponse,
    summary="Get Google Drive authentication status"
)
async def get_auth_status() -> AuthStatusResponse:
    """Check if Google Drive is available and authenticated."""
    service = get_google_drive_service()

    # Try to load existing token
    if service.is_available and not service.is_authenticated:
        service.try_load_token()

    return AuthStatusResponse(
        available=service.is_available,
        authenticated=service.is_authenticated,
        email=service.user_email,
        has_credentials=service.has_credentials_file()
    )


@router.post(
    "/auth/credentials",
    response_model=AuthStartResponse,
    summary="Upload OAuth credentials and start auth flow"
)
async def upload_credentials_and_start_auth(
    request: AuthStartRequest
) -> AuthStartResponse:
    """
    Upload OAuth client credentials (credentials.json) and start auth flow.

    The credentials.json file should be downloaded from Google Cloud Console.
    """
    service = get_google_drive_service()

    if not service.is_available:
        return AuthStartResponse(
            success=False,
            message="Google API packages not installed"
        )

    # Save credentials
    if not service.save_credentials_file(request.credentials_json):
        return AuthStartResponse(
            success=False,
            message="Invalid credentials.json format"
        )

    # Start OAuth flow
    auth_url = service.get_auth_url()
    if not auth_url:
        return AuthStartResponse(
            success=False,
            message="Failed to generate authorization URL"
        )

    return AuthStartResponse(
        success=True,
        auth_url=auth_url,
        message="Open the URL in your browser and authorize access"
    )


@router.post(
    "/auth/start",
    response_model=AuthStartResponse,
    summary="Start OAuth flow (credentials already uploaded)"
)
async def start_auth() -> AuthStartResponse:
    """
    Start OAuth flow using previously uploaded credentials.
    """
    service = get_google_drive_service()

    if not service.is_available:
        return AuthStartResponse(
            success=False,
            message="Google API packages not installed"
        )

    if not service.has_credentials_file():
        return AuthStartResponse(
            success=False,
            message="No credentials.json file found. Upload credentials first."
        )

    auth_url = service.get_auth_url()
    if not auth_url:
        return AuthStartResponse(
            success=False,
            message="Failed to generate authorization URL"
        )

    return AuthStartResponse(
        success=True,
        auth_url=auth_url,
        message="Open the URL in your browser and authorize access"
    )


@router.post(
    "/auth/complete",
    response_model=AuthCompleteResponse,
    summary="Complete OAuth with authorization code"
)
async def complete_auth(request: AuthCompleteRequest) -> AuthCompleteResponse:
    """
    Complete OAuth flow by providing the authorization code from Google.
    """
    service = get_google_drive_service()

    if not service.is_available:
        return AuthCompleteResponse(
            success=False,
            message="Google API packages not installed"
        )

    if service.complete_auth(request.auth_code):
        return AuthCompleteResponse(
            success=True,
            email=service.user_email,
            message="Successfully connected to Google Drive"
        )
    else:
        return AuthCompleteResponse(
            success=False,
            message="Failed to complete authentication. Check the authorization code."
        )


@router.post(
    "/disconnect",
    summary="Disconnect Google Drive"
)
async def disconnect() -> dict:
    """
    Disconnect Google Drive and clear saved credentials.
    """
    service = get_google_drive_service()

    if service.disconnect():
        return {"success": True, "message": "Disconnected from Google Drive"}
    else:
        return {"success": False, "message": "Failed to disconnect"}


# =============================================================================
# File Browsing Endpoints
# =============================================================================

@router.get(
    "/files",
    response_model=FolderListResponse,
    summary="List files in a folder"
)
async def list_files(folder_id: str = "root") -> FolderListResponse:
    """
    List files and subfolders in a Google Drive folder.

    Args:
        folder_id: Folder ID ("root" for root folder).
    """
    service = get_google_drive_service()

    if not service.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated with Google Drive"
        )

    result = service.list_folder(folder_id)

    return FolderListResponse(
        files=[FileInfo(**f) for f in result.get("files", [])],
        path=[PathItem(**p) for p in result.get("path", [])],
        error=result.get("error")
    )


@router.get(
    "/files/{file_id}/info",
    summary="Get file information"
)
async def get_file_info(file_id: str) -> dict:
    """
    Get metadata for a specific file.
    """
    service = get_google_drive_service()

    if not service.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated with Google Drive"
        )

    info = service.get_file_info(file_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )

    return info


# =============================================================================
# File Import Endpoints
# =============================================================================

def _send_progress(stage: str, current: int, total: int, message: str = "") -> str:
    """Format SSE progress event."""
    data = {
        "stage": stage,
        "current": current,
        "total": total,
        "percent": round((current / total * 100) if total > 0 else 0, 1),
        "message": message
    }
    return f"data: {json.dumps(data)}\n\n"


@router.post(
    "/import/stream",
    summary="Import files from Google Drive with progress streaming"
)
async def import_files_stream(request: ImportRequest):
    """
    Import files from Google Drive with real-time progress updates.

    Downloads files, processes them through the RAG pipeline, and stores
    embeddings and graph data.

    Returns Server-Sent Events (SSE) stream with progress information.
    """
    # Validate library
    library_mgr = get_library_manager()
    library = library_mgr.get_library(request.library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {request.library_id}"
        )

    # Check authentication
    gdrive = get_google_drive_service()
    if not gdrive.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated with Google Drive"
        )

    if not request.file_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files selected for import"
        )

    async def generate():
        processor = get_document_processor()
        ai_client = get_ai_client()
        vector_db = get_vector_db_service()
        graph_db = get_graph_db_service()
        settings_mgr = get_settings_manager()
        graph_enabled = settings_mgr.ai_settings.graph.enable_graph_extraction

        total_files = len(request.file_ids)
        files_processed = 0
        total_chunks = 0
        total_nodes = 0
        total_relationships = 0

        for file_idx, file_id in enumerate(request.file_ids):
            try:
                # Phase 1: Download from Google Drive
                yield _send_progress(
                    "downloading",
                    file_idx,
                    total_files,
                    f"Downloading file {file_idx + 1}/{total_files}..."
                )

                result = gdrive.download_file(file_id)
                if not result:
                    yield _send_progress(
                        "error",
                        file_idx,
                        total_files,
                        f"Failed to download file {file_id}"
                    )
                    continue

                content, filename, mime_type = result
                logger.info(f"Downloaded from Google Drive: {filename}")

                # Phase 2: Parse document
                yield _send_progress(
                    "parsing",
                    file_idx,
                    total_files,
                    f"Parsing {filename}..."
                )

                try:
                    processed = processor.process_file(
                        file_content=content,
                        filename=filename
                    )
                except Exception as e:
                    yield _send_progress(
                        "error",
                        file_idx,
                        total_files,
                        f"Failed to parse {filename}: {e}"
                    )
                    continue

                if not processed.chunks:
                    yield _send_progress(
                        "warning",
                        file_idx,
                        total_files,
                        f"No content extracted from {filename}"
                    )
                    continue

                # Prepare chunk data
                chunk_data = []
                chunk_texts = []
                for chunk in processed.chunks:
                    chunk_id = str(uuid.uuid4())
                    chunk_data.append({
                        "chunk_id": chunk_id,
                        "text": chunk.text,
                        "page": chunk.page,
                        "chunk_index": chunk.chunk_index,
                        "source_file": filename,
                    })
                    chunk_texts.append(chunk.text)

                num_chunks = len(chunk_texts)

                # Phase 3: Generate embeddings
                yield _send_progress(
                    "embedding",
                    file_idx,
                    total_files,
                    f"Generating embeddings for {filename} ({num_chunks} chunks)..."
                )

                batch_size = 10
                all_embeddings = []
                total_batches = (num_chunks + batch_size - 1) // batch_size

                for batch_num in range(total_batches):
                    start_idx = batch_num * batch_size
                    end_idx = min(start_idx + batch_size, num_chunks)
                    batch_texts = chunk_texts[start_idx:end_idx]

                    response = ai_client._get_client().embeddings.create(
                        model=settings_mgr.ai_settings.embedding_model,
                        input=batch_texts
                    )
                    sorted_data = sorted(response.data, key=lambda x: x.index)
                    batch_embeddings = [item.embedding for item in sorted_data]
                    all_embeddings.extend(batch_embeddings)

                    await asyncio.sleep(0.01)

                # Phase 4: Store vectors
                yield _send_progress(
                    "storing",
                    file_idx,
                    total_files,
                    f"Storing vectors for {filename}..."
                )

                chunks_stored = 0
                for i, data in enumerate(chunk_data):
                    if i < len(all_embeddings):
                        metadata = {
                            "source_file": data["source_file"],
                            "page": str(data["page"]) if data["page"] else "",
                            "chunk_index": str(data["chunk_index"]),
                            "library_id": request.library_id,
                            "file_source": "google_drive",
                            "gdrive_file_id": file_id,
                        }
                        vector_db.add_chunk(
                            library_id=request.library_id,
                            chunk_id=data["chunk_id"],
                            embedding=all_embeddings[i],
                            metadata=metadata,
                            text=data["text"]
                        )
                        chunks_stored += 1

                total_chunks += chunks_stored

                # Phase 5: Graph extraction
                file_nodes = 0
                file_relationships = 0

                if graph_enabled:
                    yield _send_progress(
                        "graph",
                        file_idx,
                        total_files,
                        f"Extracting graph for {filename}..."
                    )

                    graph_chunks = [
                        {
                            "chunk_id": data["chunk_id"],
                            "page": data["page"],
                            "chunk_index": data["chunk_index"],
                            "text": data["text"],
                        }
                        for data in chunk_data
                    ]

                    file_nodes, file_relationships = graph_db.ingest_document(
                        library_id=request.library_id,
                        source_file=filename,
                        chunks=graph_chunks
                    )

                    total_nodes += file_nodes
                    total_relationships += file_relationships

                # Update library count
                library_mgr.increment_document_count(request.library_id)
                files_processed += 1

                yield _send_progress(
                    "file_complete",
                    file_idx + 1,
                    total_files,
                    f"Completed {filename}: {chunks_stored} chunks, {file_nodes} entities"
                )

            except Exception as e:
                logger.error(f"Error importing file {file_id}: {e}")
                yield _send_progress(
                    "error",
                    file_idx,
                    total_files,
                    f"Error importing file: {e}"
                )

        # Final result
        result = {
            "stage": "complete",
            "success": True,
            "files_processed": files_processed,
            "total_files": total_files,
            "chunks_processed": total_chunks,
            "graph_nodes": total_nodes,
            "graph_relationships": total_relationships,
            "message": f"Imported {files_processed}/{total_files} files"
        }
        yield f"data: {json.dumps(result)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
