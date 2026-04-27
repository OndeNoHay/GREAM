"""
Document ingestion API routes with progress tracking.

Handles file uploads, text paste, and document management.
Uses Server-Sent Events (SSE) for real-time progress updates.
"""

import asyncio
import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional, AsyncGenerator

import mimetypes

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.config import get_app_data_dir, get_settings_manager
from app.services.ai_client import get_ai_client
from app.services.document_processor import get_document_processor
from app.services.vector_db import get_vector_db_service
from app.services.graph_db import get_graph_db_service, RateLimitWait
from app.services.library_manager import get_library_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["Documents"])


class TextIngestRequest(BaseModel):
    """Request model for text ingestion."""
    text: str
    library_id: str
    source_name: Optional[str] = "pasted_text"


class IngestResponse(BaseModel):
    """Response model for document ingestion."""
    success: bool
    message: str
    source_file: str
    chunks_processed: int
    library_id: str
    graph_nodes: Optional[int] = None
    graph_relationships: Optional[int] = None


class DocumentInfo(BaseModel):
    """Document information model."""
    source_file: str
    chunk_count: int
    library_id: str


class ChunkDetail(BaseModel):
    """Chunk detail for source info."""
    chunk_id: str
    page: Optional[str] = None
    chunk_index: str
    embedding_dim: int = 0
    embedding_preview: list[float] = []


class EntityDetail(BaseModel):
    """Entity detail for source info."""
    name: str
    type: str


class SourceDetailsResponse(BaseModel):
    """Response model for source details."""
    source_file: str
    library_id: str
    chunk_count: int
    entity_count: int
    chunks: list[ChunkDetail]
    entities: list[EntityDetail]


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


async def _process_with_progress(
    chunks: list,
    library_id: str,
    source_file: str,
    file_source: str = "local",
) -> AsyncGenerator[str, None]:
    """
    Process chunks with progress updates via SSE.

    Yields SSE events for embedding and graph progress.
    """
    ai_client = get_ai_client()
    vector_db = get_vector_db_service()
    graph_db = get_graph_db_service()
    settings_mgr = get_settings_manager()

    # Check if graph processing is enabled
    graph_enabled = settings_mgr.ai_settings.graph.enable_graph_extraction

    # Prepare chunk data
    chunk_data = []
    chunk_texts = []

    for chunk in chunks:
        chunk_id = str(uuid.uuid4())
        chunk_data.append({
            "chunk_id": chunk_id,
            "text": chunk.text,
            "page": chunk.page,
            "chunk_index": chunk.chunk_index,
            "source_file": chunk.source_file,
        })
        chunk_texts.append(chunk.text)

    total_chunks = len(chunk_texts)
    logger.info(f"[{source_file}] Starting ingestion — {total_chunks} chunks")

    # Phase 1: Generate embeddings concurrently with progress
    # Up to MAX_CONCURRENT_EMBED_BATCHES batches run in parallel via asyncio.to_thread.
    # Each batch call is I/O-bound (network to Ollama/OpenAI) so the GIL is released
    # and true concurrency is achieved even without multiprocessing.
    batch_size = 10
    MAX_CONCURRENT_EMBED_BATCHES = 5
    all_embeddings = []
    total_batches = (total_chunks + batch_size - 1) // batch_size
    all_batches = [
        chunk_texts[i * batch_size: min((i + 1) * batch_size, total_chunks)]
        for i in range(total_batches)
    ]

    yield _send_progress("embedding", 0, total_chunks, "Starting embeddings...")
    logger.info(
        f"[{source_file}] Phase 1/3: Embedding ({total_batches} batches, "
        f"up to {MAX_CONCURRENT_EMBED_BATCHES} concurrent)"
    )

    embed_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EMBED_BATCHES)
    _embed_model = settings_mgr.ai_settings.embedding_model
    _embed_client = ai_client._get_client()

    async def _embed_one_batch(batch_texts: list) -> list:
        async with embed_semaphore:
            resp = await asyncio.to_thread(
                _embed_client.embeddings.create,
                model=_embed_model,
                input=batch_texts,
            )
            return sorted(resp.data, key=lambda x: x.index)

    try:
        # Process batches in concurrent groups; report progress after each group
        # so the UI receives updates while batches are in flight.
        group_size = MAX_CONCURRENT_EMBED_BATCHES
        for group_start in range(0, len(all_batches), group_size):
            group = all_batches[group_start:group_start + group_size]
            group_results = await asyncio.gather(*[_embed_one_batch(b) for b in group])
            for batch_data in group_results:
                all_embeddings.extend(item.embedding for item in batch_data)
            progress = min(len(all_embeddings), total_chunks)
            yield _send_progress("embedding", progress, total_chunks,
                                 f"Embedded {progress}/{total_chunks} chunks")
            logger.info(f"[{source_file}] Embedding: {progress}/{total_chunks} chunks")
            await asyncio.sleep(0.01)

    except Exception as e:
        logger.error(f"[{source_file}] Embedding failed: {e}")
        yield _send_progress("error", 0, 0, f"Embedding failed: {e}")
        return

    # Phase 2: Store in vector DB — emit progress every 5 chunks
    logger.info(f"[{source_file}] Phase 2/3: Storing {total_chunks} vectors")
    yield _send_progress("storing", 0, total_chunks, f"Storing vectors... 0/{total_chunks}")
    await asyncio.sleep(0)  # flush to client before blocking loop

    chunks_processed = 0
    try:
        for i, data in enumerate(chunk_data):
            if i < len(all_embeddings):
                metadata = {
                    "source_file": data["source_file"],
                    "page": str(data["page"]) if data["page"] else "",
                    "chunk_index": str(data["chunk_index"]),
                    "library_id": library_id,
                    "file_source": file_source,
                }
                vector_db.add_chunk(
                    library_id=library_id,
                    chunk_id=data["chunk_id"],
                    embedding=all_embeddings[i],
                    metadata=metadata,
                    text=data["text"]
                )
                chunks_processed += 1
                if chunks_processed % 5 == 0 or chunks_processed == total_chunks:
                    yield _send_progress(
                        "storing", chunks_processed, total_chunks,
                        f"Storing vectors... {chunks_processed}/{total_chunks}"
                    )
                    logger.info(f"[{source_file}] Storing: {chunks_processed}/{total_chunks} vectors")
                    await asyncio.sleep(0)  # flush to client
    except Exception as e:
        logger.error(f"[{source_file}] Vector store failed: {e}")
        yield _send_progress("error", 0, 0, str(e))
        return

    yield _send_progress("storing", chunks_processed, total_chunks,
                        f"Stored {chunks_processed} vectors")

    # Phase 3: Graph processing (if enabled)
    graph_nodes = 0
    graph_relationships = 0

    if graph_enabled:
        logger.info(f"[{source_file}] Phase 3/3: Graph extraction ({total_chunks} chunks)")
        yield _send_progress("graph", 0, total_chunks, "Extracting entities and building graph...")

        graph_chunks = [
            {
                "chunk_id": data["chunk_id"],
                "page": data["page"],
                "chunk_index": data["chunk_index"],
                "text": data["text"],
            }
            for data in chunk_data
        ]

        # Process ALL chunks in a single ingest_document call.
        # Previously this loop called ingest_document one chunk at a time, which caused
        # a full DB checkpoint + reconnect for every chunk (~500 reconnects on a 160-page PDF).
        # Passing all chunks at once means one reconnect, one batch write, and parallel
        # regex extraction inside ingest_document (via ThreadPoolExecutor).
        #
        # Because the call can take minutes (Kùzu I/O for thousands of writes), we run
        # it as an asyncio Task and poll every HEARTBEAT_S seconds to emit SSE keepalive
        # events — without these the browser drops the SSE connection after ~30-60s of
        # silence and the progress bar freezes at 0.
        #
        # RateLimitWait can still occur in LLM extraction mode; retry logic is preserved.
        MAX_RATE_LIMIT_RETRIES = 3
        HEARTBEAT_S = 15  # seconds between SSE keepalive events
        retries = 0
        logger.info(f"[{source_file}] Graph: processing {total_chunks} chunks in single batch")
        await asyncio.sleep(0)  # flush progress event to client before entering thread

        while True:
            graph_task = asyncio.create_task(
                asyncio.to_thread(
                    graph_db.ingest_document,
                    library_id=library_id,
                    source_file=source_file,
                    chunks=graph_chunks,
                )
            )
            elapsed_s = 0

            # Heartbeat loop: wait up to HEARTBEAT_S at a time; send a keepalive event
            # on each timeout so the SSE connection stays open.  asyncio.shield() prevents
            # the task from being cancelled when wait_for's timeout fires.
            # Non-timeout exceptions (e.g. RateLimitWait) are caught generically so they
            # fall through to graph_task.result() below for proper handling.
            while not graph_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(graph_task), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    elapsed_s += HEARTBEAT_S
                    yield _send_progress(
                        "graph", 0, total_chunks,
                        f"Graph extraction in progress... {elapsed_s}s — {total_chunks} chunks"
                    )
                    await asyncio.sleep(0)
                except Exception:
                    break  # task completed with an exception; handled by graph_task.result()

            # Retrieve result or exception from the completed task
            try:
                graph_nodes, graph_relationships = graph_task.result()
                break  # success
            except RateLimitWait as e:
                retries += 1
                if retries > MAX_RATE_LIMIT_RETRIES:
                    logger.error(
                        f"[{source_file}] Rate limit: max retries ({MAX_RATE_LIMIT_RETRIES}) exceeded"
                    )
                    yield _send_progress(
                        "error", 0, total_chunks,
                        f"Rate limit exceeded max retries for graph extraction"
                    )
                    return
                wait_secs = e.wait_seconds
                logger.warning(
                    f"[{source_file}] Rate limit on graph extraction, "
                    f"waiting {wait_secs}s (attempt {retries}/{MAX_RATE_LIMIT_RETRIES})"
                )
                yield _send_progress(
                    "graph", 0, total_chunks,
                    f"Rate limit hit — waiting {wait_secs}s before retry "
                    f"(attempt {retries}/{MAX_RATE_LIMIT_RETRIES})"
                )
                await asyncio.sleep(wait_secs)
                # Loop will create a new task for the retry

        logger.info(f"[{source_file}] Graph done: {graph_nodes} entities, {graph_relationships} relations")
        yield _send_progress("graph", total_chunks, total_chunks,
                           f"Done: {graph_nodes} entities, {graph_relationships} relations")
    else:
        logger.info(f"[{source_file}] Graph extraction disabled — skipping phase 3")
        yield _send_progress("graph", 0, 0, "Graph extraction disabled")

    logger.info(
        f"[{source_file}] Ingestion complete — "
        f"{chunks_processed} chunks, {graph_nodes} graph nodes, {graph_relationships} relations"
    )

    # Final result
    result = {
        "stage": "complete",
        "success": True,
        "chunks_processed": chunks_processed,
        "graph_nodes": graph_nodes,
        "graph_relationships": graph_relationships,
        "message": f"Processed {chunks_processed} chunks"
    }
    yield f"data: {json.dumps(result)}\n\n"


@router.post(
    "/upload/stream",
    summary="Upload and process a document with progress streaming"
)
async def upload_document_stream(
    file: UploadFile = File(...),
    library_id: str = Form(...)
):
    """
    Upload and process a document file with real-time progress updates.

    Returns Server-Sent Events (SSE) stream with progress information.
    """
    # Validate library
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    # Validate file
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required"
        )

    filename = file.filename
    ext = Path(filename).suffix.lower()

    processor = get_document_processor()
    if ext not in processor.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format: {ext}"
        )

    logger.info(f"Processing uploaded file with streaming: {filename}")

    # Read file content
    content = await file.read()

    # Save original file for later retrieval
    files_dir = get_app_data_dir() / "files" / library_id
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / Path(filename).name).write_bytes(content)

    # Process document
    processed = processor.process_file(
        file_content=content,
        filename=filename
    )

    if not processed.chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No content could be extracted from the file"
        )

    async def generate():
        yield _send_progress("parsing", len(processed.chunks), len(processed.chunks),
                           f"Parsed {len(processed.chunks)} chunks from document")

        completed = False
        async for event in _process_with_progress(
            chunks=processed.chunks,
            library_id=library_id,
            source_file=filename,
            file_source="local",
        ):
            yield event
            if event.startswith("data: "):
                try:
                    if json.loads(event[6:]).get("stage") == "complete":
                        completed = True
                except (json.JSONDecodeError, KeyError):
                    pass

        # Only increment count when ingestion fully completed (not on error)
        if completed:
            library_mgr.increment_document_count(library_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post(
    "/upload",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a document file"
)
async def upload_document(
    file: UploadFile = File(...),
    library_id: str = Form(...)
) -> IngestResponse:
    """
    Upload and process a document file (non-streaming version).
    """
    # Validate library
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required"
        )

    filename = file.filename
    ext = Path(filename).suffix.lower()

    processor = get_document_processor()
    if ext not in processor.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format: {ext}. "
                   f"Supported: {', '.join(processor.SUPPORTED_EXTENSIONS.keys())}"
        )

    logger.info(f"Processing uploaded file: {filename}")

    try:
        content = await file.read()

        # Save original file for later retrieval
        files_dir = get_app_data_dir() / "files" / library_id
        files_dir.mkdir(parents=True, exist_ok=True)
        (files_dir / Path(filename).name).write_bytes(content)

        processed = processor.process_file(
            file_content=content,
            filename=filename
        )

        if not processed.chunks:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No content could be extracted from the file"
            )

        # Process chunks
        ai_client = get_ai_client()
        vector_db = get_vector_db_service()
        graph_db = get_graph_db_service()
        settings_mgr = get_settings_manager()

        chunk_data = []
        chunk_texts = []

        for chunk in processed.chunks:
            chunk_id = str(uuid.uuid4())
            chunk_data.append({
                "chunk_id": chunk_id,
                "text": chunk.text,
                "page": chunk.page,
                "chunk_index": chunk.chunk_index,
                "source_file": chunk.source_file,
            })
            chunk_texts.append(chunk.text)

        # Generate embeddings
        embeddings = ai_client.generate_embeddings(chunk_texts)

        # Store vectors
        chunks_processed = 0
        for i, data in enumerate(chunk_data):
            if i < len(embeddings):
                metadata = {
                    "source_file": data["source_file"],
                    "page": str(data["page"]) if data["page"] else "",
                    "chunk_index": str(data["chunk_index"]),
                    "library_id": library_id,
                    "file_source": "local",
                }
                vector_db.add_chunk(
                    library_id=library_id,
                    chunk_id=data["chunk_id"],
                    embedding=embeddings[i],
                    metadata=metadata,
                    text=data["text"]  # Store text for RAG retrieval
                )
                chunks_processed += 1

        # Graph processing
        graph_nodes = 0
        graph_relationships = 0

        if settings_mgr.ai_settings.graph.enable_graph_extraction:
            graph_chunks = [
                {
                    "chunk_id": data["chunk_id"],
                    "page": data["page"],
                    "chunk_index": data["chunk_index"],
                    "text": data["text"],
                }
                for data in chunk_data
            ]

            graph_nodes, graph_relationships = graph_db.ingest_document(
                library_id=library_id,
                source_file=filename,
                chunks=graph_chunks
            )

        library_mgr.increment_document_count(library_id)

        logger.info(
            f"Ingested {chunks_processed} chunks from {filename} "
            f"(graph: {graph_nodes} nodes, {graph_relationships} relationships)"
        )

        return IngestResponse(
            success=True,
            message=f"Document processed successfully",
            source_file=filename,
            chunks_processed=chunks_processed,
            library_id=library_id,
            graph_nodes=graph_nodes,
            graph_relationships=graph_relationships
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document processing failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document processing failed: {e}"
        )


@router.post(
    "/text",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest pasted text content"
)
async def ingest_text(request: TextIngestRequest) -> IngestResponse:
    """Ingest text content (e.g., from clipboard paste)."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(request.library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {request.library_id}"
        )

    if not request.text or not request.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text content cannot be empty"
        )

    source_name = request.source_name or "pasted_text"
    logger.info(f"Processing pasted text: {len(request.text)} chars")

    try:
        processor = get_document_processor()
        processed = processor.process_text_content(
            text=request.text,
            source_name=source_name
        )

        if not processed.chunks:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No content could be extracted from the text"
            )

        # Process chunks (same as upload)
        ai_client = get_ai_client()
        vector_db = get_vector_db_service()
        graph_db = get_graph_db_service()
        settings_mgr = get_settings_manager()

        chunk_data = []
        chunk_texts = []

        for chunk in processed.chunks:
            chunk_id = str(uuid.uuid4())
            chunk_data.append({
                "chunk_id": chunk_id,
                "text": chunk.text,
                "page": chunk.page,
                "chunk_index": chunk.chunk_index,
                "source_file": chunk.source_file,
            })
            chunk_texts.append(chunk.text)

        embeddings = ai_client.generate_embeddings(chunk_texts)

        chunks_processed = 0
        for i, data in enumerate(chunk_data):
            if i < len(embeddings):
                metadata = {
                    "source_file": data["source_file"],
                    "page": str(data["page"]) if data["page"] else "",
                    "chunk_index": str(data["chunk_index"]),
                    "library_id": request.library_id,
                    "file_source": "pasted_text",
                }
                vector_db.add_chunk(
                    library_id=request.library_id,
                    chunk_id=data["chunk_id"],
                    embedding=embeddings[i],
                    metadata=metadata,
                    text=data["text"]  # Store text for RAG retrieval
                )
                chunks_processed += 1

        graph_nodes = 0
        graph_relationships = 0

        if settings_mgr.ai_settings.graph.enable_graph_extraction:
            graph_chunks = [
                {
                    "chunk_id": data["chunk_id"],
                    "page": data["page"],
                    "chunk_index": data["chunk_index"],
                    "text": data["text"],
                }
                for data in chunk_data
            ]

            graph_nodes, graph_relationships = graph_db.ingest_document(
                library_id=request.library_id,
                source_file=source_name,
                chunks=graph_chunks
            )

        library_mgr.increment_document_count(request.library_id)

        return IngestResponse(
            success=True,
            message="Text processed successfully",
            source_file=source_name,
            chunks_processed=chunks_processed,
            library_id=request.library_id,
            graph_nodes=graph_nodes,
            graph_relationships=graph_relationships
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Text processing failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Text processing failed: {e}"
        )


@router.post(
    "/text/stream",
    summary="Ingest pasted text content with progress streaming"
)
async def ingest_text_stream(request: TextIngestRequest):
    """Ingest text content with real-time SSE progress updates."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(request.library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {request.library_id}"
        )

    if not request.text or not request.text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text content cannot be empty"
        )

    source_name = request.source_name or "pasted_text"
    logger.info(f"Processing pasted text with streaming: {len(request.text)} chars")

    processor = get_document_processor()
    processed = processor.process_text_content(
        text=request.text,
        source_name=source_name
    )

    if not processed.chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No content could be extracted from the text"
        )

    async def generate():
        yield _send_progress("parsing", len(processed.chunks), len(processed.chunks),
                           f"Parsed {len(processed.chunks)} chunks from text")

        completed = False
        async for event in _process_with_progress(
            chunks=processed.chunks,
            library_id=request.library_id,
            source_file=source_name,
            file_source="pasted_text",
        ):
            yield event
            if event.startswith("data: "):
                try:
                    if json.loads(event[6:]).get("stage") == "complete":
                        completed = True
                except (json.JSONDecodeError, KeyError):
                    pass

        # Only increment count when ingestion fully completed (not on error)
        if completed:
            library_mgr.increment_document_count(request.library_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get(
    "/sources/{library_id}",
    response_model=list[str],
    summary="List all document sources in a library"
)
async def list_sources(library_id: str) -> list[str]:
    """Get list of all source files in a library."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    vector_db = get_vector_db_service()
    return vector_db.list_sources(library_id)


@router.delete(
    "/source/{library_id}/{source_file}",
    summary="Delete all chunks from a source file"
)
async def delete_source(library_id: str, source_file: str) -> dict:
    """Delete all chunks from a specific source file."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    vector_db = get_vector_db_service()
    graph_db = get_graph_db_service()

    vector_deleted = vector_db.delete_by_source(library_id, source_file)
    graph_deleted = graph_db.delete_by_source(library_id, source_file)

    if vector_deleted > 0 or graph_deleted > 0:
        library_mgr.decrement_document_count(library_id)

    # Delete stored original file if it exists
    stored_file = get_app_data_dir() / "files" / library_id / Path(source_file).name
    stored_file.unlink(missing_ok=True)

    return {
        "success": True,
        "message": f"Deleted source: {source_file}",
        "chunks_deleted": vector_deleted,
        "graph_nodes_deleted": graph_deleted
    }


@router.get(
    "/file/{library_id}/{source_file:path}",
    summary="Serve original document file"
)
async def serve_document_file(library_id: str, source_file: str):
    """Serve the original uploaded file so users can open it in the browser."""
    safe_name = Path(source_file).name  # prevent path traversal
    file_path = get_app_data_dir() / "files" / library_id / safe_name
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Original file not found"
        )
    media_type, _ = mimetypes.guess_type(safe_name)
    media_type = media_type or "application/octet-stream"
    return FileResponse(
        str(file_path),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.post(
    "/open/{library_id}/{source_file:path}",
    summary="Open original document file with system default application"
)
async def open_document_file(library_id: str, source_file: str):
    """Open the original file using the OS default application (e.g. Word for .docx, browser for .pdf)."""
    safe_name = Path(source_file).name  # prevent path traversal
    file_path = get_app_data_dir() / "files" / library_id / safe_name
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Original file not found"
        )
    if sys.platform == "win32":
        import os as _os
        _os.startfile(str(file_path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(file_path)], check=False)
    else:
        subprocess.run(["xdg-open", str(file_path)], check=False)
    return {"success": True}


@router.get(
    "/source/{library_id}/{source_file:path}/details",
    response_model=SourceDetailsResponse,
    summary="Get detailed information about a source file"
)
async def get_source_details(library_id: str, source_file: str) -> SourceDetailsResponse:
    """
    Get detailed information about a specific source file.

    Returns chunks (with embedding info) and related entities.
    """
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    vector_db = get_vector_db_service()
    graph_db = get_graph_db_service()

    # Get vector details
    vector_details = vector_db.get_source_details(library_id, source_file)

    # Get graph details
    graph_stats = graph_db.get_source_stats(library_id, source_file)

    # Build chunks list
    chunks = [
        ChunkDetail(
            chunk_id=c["chunk_id"],
            page=c.get("page"),
            chunk_index=c.get("chunk_index", "0"),
            embedding_dim=c.get("embedding_dim", 0),
            embedding_preview=c.get("embedding_preview", [])
        )
        for c in vector_details.get("chunks", [])
    ]

    # Build entities list
    entities = [
        EntityDetail(
            name=e["name"],
            type=e["type"]
        )
        for e in graph_stats.get("entities", [])
    ]

    return SourceDetailsResponse(
        source_file=source_file,
        library_id=library_id,
        chunk_count=vector_details.get("chunk_count", 0),
        entity_count=len(entities),
        chunks=chunks,
        entities=entities
    )


@router.delete(
    "/vectors/{library_id}",
    summary="Clear all vectors for a library"
)
async def clear_library_vectors(library_id: str) -> dict:
    """Delete all vector embeddings for a library."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    vector_db = get_vector_db_service()
    deleted = vector_db.clear_all(library_id)
    library_mgr.set_document_count(library_id, 0)

    return {
        "success": True,
        "message": f"Cleared {deleted} vectors",
        "vectors_deleted": deleted
    }


@router.delete(
    "/graphs/{library_id}",
    summary="Clear all graph data for a library"
)
async def clear_library_graphs(library_id: str) -> dict:
    """Delete all graph nodes and relationships for a library."""
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    graph_db = get_graph_db_service()
    graph_db.delete_library(library_id)

    return {
        "success": True,
        "message": "Cleared all graph data"
    }


@router.post(
    "/cleanup/{library_id}",
    summary="Clean up orphaned entities in a library"
)
async def cleanup_orphaned_entities(library_id: str) -> dict:
    """
    Remove orphaned entities that have no document connections.

    Call this to clean up entities from previously deleted documents.
    """
    library_mgr = get_library_manager()
    library = library_mgr.get_library(library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}"
        )

    graph_db = get_graph_db_service()
    deleted = graph_db.cleanup_orphaned_entities(library_id)

    return {
        "success": True,
        "message": f"Cleaned up {deleted} orphaned entities",
        "orphaned_entities_deleted": deleted
    }
