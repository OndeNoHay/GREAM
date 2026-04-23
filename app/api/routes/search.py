"""
Search API routes.

Handles hybrid search (vector + graph) queries with optional LLM response generation.
"""

import asyncio
import json
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_app_settings, get_settings_manager
from app.services.ai_client import get_ai_client
from app.services.vector_db import get_vector_db_service
from app.services.graph_db import get_graph_db_service
from app.services.library_manager import get_library_manager
from app.services.agent_manager import get_agent_manager
from app.services.agent_executor import get_agent_executor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["Search"])


class SearchRequest(BaseModel):
    """Request model for search queries."""
    query: str = Field(..., min_length=1, max_length=10000)
    library_id: str
    top_k: int = Field(default=10, ge=1, le=100)
    use_vector_search: bool = True
    use_graph_search: bool = True
    generate_response: bool = Field(
        default=False,
        description="Generate LLM response based on search results"
    )


class SearchResult(BaseModel):
    """Individual search result."""
    source_file: str
    page: Optional[str]
    chunk_index: Optional[str]
    score: float
    source: str  # 'vector', 'graph', or 'both'
    related_entities: Optional[list[str]] = None
    text: Optional[str] = None  # Actual text content for RAG
    file_link: Optional[str] = None  # URL to open original file


class SearchResponse(BaseModel):
    """Response model for search queries."""
    success: bool
    query: str
    library_id: str
    results: list[SearchResult]
    total_results: int
    message: str
    llm_response: Optional[str] = Field(
        default=None,
        description="LLM-generated answer based on search results"
    )


class ConversationMessage(BaseModel):
    """A single turn in the conversation history."""
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    """Request model for chat/RAG queries."""
    query: str = Field(..., min_length=1, max_length=10000)
    library_id: str
    top_k: int = Field(default=10, ge=1, le=50)
    use_vector_search: bool = True
    use_graph_search: bool = True
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(default=1024, ge=100, le=4096)
    conversation_history: list[ConversationMessage] = Field(
        default_factory=list,
        description="Previous turns sent as context to the LLM"
    )


class EntityInfo(BaseModel):
    """A single graph entity extracted from retrieved chunks."""
    name: str
    entity_type: str


class EntityNeighbor(BaseModel):
    """One relationship edge returned by the neighbors endpoint."""
    target_name: str
    target_type: str
    relationship_type: str  # e.g. "CONTROLS" or "inverse_PART_OF"


class EntityNeighborsResponse(BaseModel):
    """Response for the entity neighbors endpoint."""
    entity_name: str
    entity_type: str
    relationships: list[EntityNeighbor]


class ChatResponse(BaseModel):
    """Response model for chat queries."""
    success: bool
    query: str
    answer: str
    sources: list[SearchResult]
    library_id: str
    vector_results_count: int = Field(default=0, description="Number of vector search results used")
    graph_results_count: int = Field(default=0, description="Number of graph search results used")
    graph_entities: list[EntityInfo] = Field(
        default_factory=list,
        description="Distinct graph entities from retrieved chunks"
    )


def _build_file_link(
    metadata: dict,
    source_file: str,
    library_id: str,
    page: Optional[str] = None,
) -> Optional[str]:
    """Build a URL to open the original file, based on its source type.

    For local PDFs, appends a #page=N fragment so the browser's inline PDF
    viewer jumps directly to the referenced page.
    """
    file_source = metadata.get("file_source", "")
    if file_source == "google_drive":
        gdrive_id = metadata.get("gdrive_file_id", "")
        if gdrive_id:
            return f"https://drive.google.com/file/d/{gdrive_id}/view"
    elif file_source == "local":
        base = f"/api/documents/file/{library_id}/{quote(source_file)}"
        if page and source_file.lower().endswith(".pdf"):
            return f"{base}#page={page}"
        return base
    return None


def _enrich_graph_results_with_text(
    graph_results: list[dict],
    library_id: str,
) -> list[dict]:
    """
    Fetch and inject text content into graph results, returning only those
    for which text could be retrieved.

    Graph search (Kùzu) returns chunk IDs but no text — text is stored only
    in ChromaDB. Graph results whose chunk IDs cannot be resolved (e.g. due
    to ID mismatch from prior processing) are dropped so they never fill LLM
    context with '[Text content not available]' entries.

    Returns:
        Filtered list containing only graph results that have text.
    """
    missing = [r["chunk_id"] for r in graph_results if not r.get("text")]
    if not missing:
        return graph_results

    vector_db = get_vector_db_service()
    id_to_text = vector_db.get_chunks_by_ids(library_id, missing)

    enriched = []
    for r in graph_results:
        if r.get("text"):
            enriched.append(r)
        elif r["chunk_id"] in id_to_text and id_to_text[r["chunk_id"]].strip():
            r["text"] = id_to_text[r["chunk_id"]]
            enriched.append(r)
        else:
            logger.warning(
                f"Graph result dropped — chunk ID not found in vector DB "
                f"(chunk_id={r['chunk_id']}, page={r.get('page')}). "
                f"Re-process the document to sync IDs."
            )

    logger.debug(
        f"Graph enrichment: {len(enriched)}/{len(graph_results)} results have text "
        f"({len(graph_results) - len(enriched)} dropped)"
    )
    return enriched


def merge_results(
    vector_results: list[dict],
    graph_results: list[dict],
    top_k: int,
    library_id: str = "",
) -> list[SearchResult]:
    """
    Merge and deduplicate results from vector and graph searches.

    Args:
        vector_results: Results from vector similarity search.
        graph_results: Results from graph traversal search.
        top_k: Maximum results to return.

    Returns:
        Merged and ranked list of SearchResult objects.
    """
    # Track by source_file + page + chunk_index
    merged: dict[str, dict] = {}

    # Process vector results (these have text content)
    for result in vector_results:
        key = f"{result.get('source_file', '')}|{result.get('page', '')}|{result.get('chunk_index', '')}"
        src_file = result.get("source_file", "")
        metadata = result.get("metadata", {})
        merged[key] = {
            "source_file": src_file,
            "page": result.get("page"),
            "chunk_index": result.get("chunk_index"),
            "score": result.get("score", 0.0),
            "source": "vector",
            "related_entities": None,
            "text": result.get("text", ""),  # Include text content
            "file_link": _build_file_link(metadata, src_file, library_id, page=result.get("page")),
        }

    # Process graph results and merge
    for result in graph_results:
        key = f"{result.get('source_file', '')}|{result.get('page', '')}|{result.get('chunk_index', '')}"
        graph_score = result.get("score", 0.8)

        if key in merged:
            existing = merged[key]
            # Boost score for appearing in both
            existing["score"] = min(1.0, (existing["score"] + graph_score) / 2 + 0.1)
            existing["source"] = "both"
            existing["related_entities"] = result.get("related_entities")
        else:
            src_file = result.get("source_file", "")
            g_metadata = result.get("metadata", {})
            merged[key] = {
                "source_file": src_file,
                "page": result.get("page"),
                "chunk_index": result.get("chunk_index"),
                "score": graph_score,
                "source": "graph",
                "related_entities": result.get("related_entities"),
                "text": result.get("text", ""),  # Graph results may not have text
                "file_link": _build_file_link(g_metadata, src_file, library_id, page=result.get("page")),
            }

    # Sort all merged results by score descending
    sorted_results = sorted(
        merged.values(),
        key=lambda x: x["score"],
        reverse=True
    )

    # When both sources contributed results, guarantee proportional representation.
    # Graph scores (0.75–0.9+) are structurally higher than vector cosine-similarity
    # scores (0.5–0.75 for complex queries), so a pure score ranking would silently
    # crowd out all vector results even when they contain the actual answer text.
    has_vector_input = bool(vector_results)
    has_graph_input = bool(graph_results)
    if has_vector_input and has_graph_input:
        both_pool   = [r for r in sorted_results if r["source"] == "both"]
        vector_pool = [r for r in sorted_results if r["source"] == "vector"]
        graph_pool  = [r for r in sorted_results if r["source"] == "graph"]

        # "both" chunks count toward both quotas; fill each quota with single-source chunks
        min_each = max(1, top_k // 2)
        v_quota = min_each - len(both_pool)  # remaining vector slots needed
        g_quota = min_each - len(both_pool)  # remaining graph slots needed

        selected = list(both_pool)
        selected += vector_pool[:max(0, v_quota)]
        selected += graph_pool[:max(0, g_quota)]

        # Fill any remaining slots with the best unchosen results from either pool
        chosen_ids = {id(r) for r in selected}
        remaining = [r for r in sorted_results if id(r) not in chosen_ids]
        selected += remaining[:top_k - len(selected)]

        # Final sort by score so the best results appear first
        selected.sort(key=lambda x: x["score"], reverse=True)
        final = selected[:top_k]
    else:
        final = sorted_results[:top_k]

    # Convert to SearchResult objects
    return [
        SearchResult(
            source_file=item["source_file"],
            page=item["page"],
            chunk_index=item["chunk_index"],
            score=round(item["score"], 4),
            source=item["source"],
            related_entities=item["related_entities"],
            text=item.get("text"),
            file_link=item.get("file_link"),
        )
        for item in final
    ]


def _build_rag_context(results: list[SearchResult]) -> str:
    """Build context string from search results for LLM including actual text content."""
    if not results:
        return "No relevant documents found."

    context_parts = []
    for i, result in enumerate(results, 1):
        # Build source header
        source_info = f"[Source {i}: {result.source_file}"
        if result.page:
            source_info += f", Page {result.page}"
        source_info += f" (Relevance: {result.score:.0%})]"

        # Add related entity relationships if present (graph results only)
        entities_info = ""
        if result.related_entities:
            entities_info = f"\nGraph relationships: {'; '.join(result.related_entities[:5])}"

        # Add actual text content
        text_content = ""
        if result.text and result.text.strip():
            text_content = f"\n{result.text}"
        else:
            text_content = "\n[Text content not available]"

        context_parts.append(f"{source_info}{entities_info}{text_content}")

    return "\n\n---\n\n".join(context_parts)


def _generate_llm_response(
    query: str,
    results: list[SearchResult],
    temperature: float = 0.7,
    max_tokens: Optional[int] = 1024,
    conversation_history: Optional[list] = None,
) -> str:
    """
    Generate LLM response based on search results.

    Args:
        query: User's question.
        results: Search results to use as context.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in response.
        conversation_history: Previous turns [{role, content}] to include as context.

    Returns:
        Generated answer string.
    """
    ai_client = get_ai_client()
    context = _build_rag_context(results)

    system_prompt = """You are a helpful assistant that answers questions based on the provided document references.

IMPORTANT RULES:
1. Only use information from the provided document references to answer questions.
2. Always cite your sources by mentioning the document name and page number when available.
3. If the documents don't contain enough information to answer the question, say so clearly.
4. Be concise but thorough in your responses.
5. If entities are mentioned in the context, consider how they relate to the question.

The user has searched their document library and found the following relevant references:"""

    rag_prompt = f"""Context from document search:
{context}

User's question: {query}

Please provide a helpful answer based on the document references above. Remember to cite your sources."""

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Inject prior conversation turns so the LLM can reference previous exchanges
    if conversation_history:
        for turn in conversation_history:
            messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({"role": "user", "content": rag_prompt})

    try:
        response = ai_client.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response
    except Exception as e:
        logger.error(f"LLM response generation failed: {e}")
        return f"Unable to generate response: {e}"


@router.post(
    "",
    response_model=SearchResponse,
    summary="Hybrid search query"
)
async def hybrid_search(request: SearchRequest) -> SearchResponse:
    """
    Perform hybrid search combining vector similarity and graph traversal.

    Returns references to documents (filename, page) without the actual text content.
    Optionally generates an LLM response based on the search results.
    """
    # Validate library
    library_mgr = get_library_manager()
    library = library_mgr.get_library(request.library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {request.library_id}"
        )

    if not request.query or not request.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query cannot be empty"
        )

    logger.info(f"Processing search query: {request.query[:100]}...")

    try:
        settings = get_app_settings()
        vector_results: list[dict] = []
        graph_results: list[dict] = []

        # Vector similarity search
        if request.use_vector_search:
            ai_client = get_ai_client()
            vector_db = get_vector_db_service()

            try:
                query_embedding = ai_client.generate_embedding(request.query)
                vector_results = vector_db.search(
                    library_id=request.library_id,
                    query_embedding=query_embedding,
                    top_k=request.top_k
                )
                logger.info(f"Vector search found {len(vector_results)} results")
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        # Graph traversal search
        if request.use_graph_search:
            graph_db = get_graph_db_service()
            try:
                graph_results = graph_db.search_by_entity(
                    library_id=request.library_id,
                    query=request.query,
                    top_k=request.top_k
                )
                logger.info(f"Graph search found {len(graph_results)} results")
            except Exception as e:
                logger.warning(f"Graph search failed: {e}")

        # Enrich graph results with text from vector DB; drop any that can't be enriched
        if graph_results:
            graph_results = _enrich_graph_results_with_text(graph_results, request.library_id)

        # Merge results
        merged_results = merge_results(
            vector_results,
            graph_results,
            request.top_k,
            library_id=request.library_id,
        )

        # Filter by threshold
        threshold = settings.similarity_threshold
        filtered_results = [r for r in merged_results if r.score >= threshold]

        message = f"Found {len(filtered_results)} results"
        if len(merged_results) != len(filtered_results):
            message += f" (filtered from {len(merged_results)})"

        # Generate LLM response if requested
        llm_response = None
        if request.generate_response and filtered_results:
            llm_response = _generate_llm_response(
                query=request.query,
                results=filtered_results[:5]  # Use top 5 for context
            )
            message += " with LLM response"

        return SearchResponse(
            success=True,
            query=request.query,
            library_id=request.library_id,
            results=filtered_results,
            total_results=len(filtered_results),
            message=message,
            llm_response=llm_response
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {e}"
        )


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Chat with your documents (RAG)"
)
async def chat_with_documents(request: ChatRequest) -> ChatResponse:
    """
    Ask a question and get an LLM-generated answer based on your documents.

    This endpoint performs hybrid search and automatically generates a response
    using the configured chat model. It's designed for conversational interaction
    with your document library.
    """
    # Validate library
    library_mgr = get_library_manager()
    library = library_mgr.get_library(request.library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {request.library_id}"
        )

    if not request.query or not request.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query cannot be empty"
        )

    logger.info(f"Processing chat query: {request.query[:100]}...")

    try:
        settings = get_app_settings()
        vector_results: list[dict] = []
        graph_results: list[dict] = []

        # Vector similarity search
        if request.use_vector_search:
            ai_client = get_ai_client()
            vector_db = get_vector_db_service()

            try:
                query_embedding = ai_client.generate_embedding(request.query)
                vector_results = vector_db.search(
                    library_id=request.library_id,
                    query_embedding=query_embedding,
                    top_k=request.top_k
                )
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        # Graph traversal search
        if request.use_graph_search:
            graph_db = get_graph_db_service()
            try:
                graph_results = graph_db.search_by_entity(
                    library_id=request.library_id,
                    query=request.query,
                    top_k=request.top_k
                )
            except Exception as e:
                logger.warning(f"Graph search failed: {e}")

        # Enrich graph results with text from vector DB; drop any that can't be enriched
        if graph_results:
            graph_results = _enrich_graph_results_with_text(graph_results, request.library_id)

        # Merge results
        merged_results = merge_results(
            vector_results,
            graph_results,
            request.top_k,
            library_id=request.library_id,
        )

        # Filter by threshold
        threshold = settings.similarity_threshold
        filtered_results = [r for r in merged_results if r.score >= threshold]

        # Count sources by type
        vector_count = sum(1 for r in filtered_results if r.source in ("vector", "both"))
        graph_count = sum(1 for r in filtered_results if r.source in ("graph", "both"))

        # Generate LLM response
        if filtered_results:
            # Enforce server-side cap from settings
            settings_mgr = get_settings_manager()
            max_hist = settings_mgr.ai_settings.max_conversation_history
            history = [h.model_dump() for h in request.conversation_history]
            if max_hist > 0 and history:
                history = history[-max_hist:]
            elif max_hist == 0:
                history = []
            answer = _generate_llm_response(
                query=request.query,
                results=filtered_results,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                conversation_history=history if history else None,
            )
        else:
            answer = (
                "I couldn't find any relevant documents in your library to answer "
                "this question. Please try rephrasing your question or ensure that "
                "relevant documents have been imported into the library."
            )

        logger.info(f"Chat response: {len(filtered_results)} sources ({vector_count} vector, {graph_count} graph)")

        # Collect graph entities from the query (non-fatal)
        graph_entities: list[EntityInfo] = []
        if graph_count > 0:
            try:
                graph_db = get_graph_db_service()
                raw_entities = graph_db.get_entities_for_query(request.library_id, request.query)
                graph_entities = [EntityInfo(name=e["name"], entity_type=e["entity_type"]) for e in raw_entities]
            except Exception as e:
                logger.warning(f"Graph entity extraction failed (non-fatal): {e}")

        return ChatResponse(
            success=True,
            vector_results_count=vector_count,
            graph_results_count=graph_count,
            query=request.query,
            answer=answer,
            sources=filtered_results,
            library_id=request.library_id,
            graph_entities=graph_entities,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat failed: {e}"
        )


@router.post(
    "/vector",
    response_model=SearchResponse,
    summary="Vector-only search"
)
async def vector_search(request: SearchRequest) -> SearchResponse:
    """Perform vector similarity search only."""
    request.use_vector_search = True
    request.use_graph_search = False
    return await hybrid_search(request)


@router.post(
    "/graph",
    response_model=SearchResponse,
    summary="Graph-only search"
)
async def graph_search(request: SearchRequest) -> SearchResponse:
    """Perform graph traversal search only."""
    request.use_vector_search = False
    request.use_graph_search = True
    return await hybrid_search(request)


# Agent chat endpoint

class AgentChatRequest(BaseModel):
    """Request model for agent-powered chat."""
    query: str = Field(..., min_length=1, max_length=10000)
    library_id: str
    agent_id: str


@router.post("/chat/agent", summary="Chat with an agent (SSE)")
async def chat_with_agent(request: AgentChatRequest):
    """
    Run an agent in the chat context with streaming SSE output.

    The agent uses its configured tools to research the library
    and respond to the user's query.
    """
    manager = get_agent_manager()
    executor = get_agent_executor()

    # Validate agent
    agent = manager.get_agent(request.agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {request.agent_id}"
        )

    # Validate library
    library_mgr = get_library_manager()
    library = library_mgr.get_library(request.library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {request.library_id}"
        )

    # Create task
    task = manager.create_task(
        agent_id=request.agent_id,
        library_id=request.library_id,
        prompt=request.query,
    )

    async def event_stream():
        """Generate SSE events from agent execution."""
        try:
            async for event in executor.run_agent(task, agent):
                data = json.dumps(event)
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            raise  # client disconnect — let uvicorn clean up
        except Exception as e:
            logger.error(f"Agent chat execution error: {e}")
            error_event = json.dumps({"type": "error", "message": str(e)})
            yield f"data: {error_event}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Task-ID": task.id,
        },
    )


# ---------------------------------------------------------------------------
# Graph entity neighborhood router
# ---------------------------------------------------------------------------
graph_router = APIRouter(prefix="/api/graph", tags=["Graph"])


@graph_router.get(
    "/entity/{library_id}/{entity_name}/neighbors",
    response_model=EntityNeighborsResponse,
    summary="Get related entities and relationship types for a graph entity",
)
async def get_entity_neighbors(
    library_id: str,
    entity_name: str,
) -> EntityNeighborsResponse:
    """Return direct neighbors of a named entity within a library, with relationship types."""
    library_mgr = get_library_manager()
    if not library_mgr.get_library(library_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {library_id}",
        )

    graph_db = get_graph_db_service()

    entity_info = graph_db.get_entity_info(library_id, entity_name)
    if not entity_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_name}",
        )

    raw_neighbors = graph_db.get_related_entities(library_id, entity_name)
    relationships = [
        EntityNeighbor(
            target_name=n["target_name"],
            target_type=n["target_type"],
            relationship_type=n["relationship_type"],
        )
        for n in raw_neighbors
    ]

    return EntityNeighborsResponse(
        entity_name=entity_info["name"],
        entity_type=entity_info["entity_type"],
        relationships=relationships,
    )
