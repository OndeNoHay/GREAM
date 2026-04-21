"""
Agent tools for GraphRagExec.

Defines tools that agents can use to interact with the document library.
Each tool wraps existing GraphRagExec services.
"""

import logging
from typing import Any, Optional
from pydantic import BaseModel, Field

from app.services.ai_client import get_ai_client
from app.services.vector_db import get_vector_db_service
from app.services.graph_db import get_graph_db_service
from app.models.agents import ToolPermission

# Track delegation depth to prevent recursive orchestration
_current_delegation_depth: int = 0
MAX_DELEGATION_DEPTH: int = 1

logger = logging.getLogger(__name__)


# Tool argument and result models

class SearchDocumentsArgs(BaseModel):
    """Arguments for document search."""
    query: str = Field(description="Search query text")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of results to return")


class SearchDocumentsResult(BaseModel):
    """Results from document search."""
    results: list[dict[str, Any]]
    total: int
    query: str


class SearchGraphArgs(BaseModel):
    """Arguments for graph search."""
    query: str = Field(description="Search query for entity matching")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of results to return")


class SearchGraphResult(BaseModel):
    """Results from graph search."""
    results: list[dict[str, Any]]
    total: int
    query: str


class GetEntitiesArgs(BaseModel):
    """Arguments for getting entities."""
    source_file: str = Field(description="Source file name to get entities from")
    entity_type: str | None = Field(default=None, description="Filter by entity type")


class GetEntitiesResult(BaseModel):
    """Results from getting entities."""
    entities: list[dict[str, Any]]
    total: int
    source_file: str


class GetRelationshipsArgs(BaseModel):
    """Arguments for getting relationships."""
    entity_name: str = Field(description="Entity name to find relationships for")
    relationship_type: str | None = Field(default=None, description="Filter by relationship type")
    max_depth: int = Field(default=2, ge=1, le=5, description="Maximum traversal depth")


class GetRelationshipsResult(BaseModel):
    """Results from getting relationships."""
    relationships: list[dict[str, Any]]
    total: int
    entity_name: str


class CompareDocumentsArgs(BaseModel):
    """Arguments for comparing documents."""
    source_file_1: str = Field(description="First document to compare")
    source_file_2: str = Field(description="Second document to compare")


class CompareDocumentsResult(BaseModel):
    """Results from document comparison."""
    shared_entities: list[str]
    unique_to_first: list[str]
    unique_to_second: list[str]
    relationships_between: list[dict[str, Any]]
    similarity_score: float


class GetDocumentChunksArgs(BaseModel):
    """Arguments for getting document chunks."""
    source_file: str = Field(description="Source file name")
    page: str | None = Field(default=None, description="Filter by page number")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum chunks to return")


class GetDocumentChunksResult(BaseModel):
    """Results from getting document chunks."""
    chunks: list[dict[str, Any]]
    total: int
    source_file: str


class SummarizeTextArgs(BaseModel):
    """Arguments for text summarization."""
    text: str = Field(description="Text to summarize")
    max_length: int = Field(default=200, ge=50, le=500, description="Maximum summary length in words")
    style: str = Field(default="concise", description="Summary style: 'concise', 'detailed', or 'bullet'")


class SummarizeTextResult(BaseModel):
    """Results from text summarization."""
    summary: str
    original_length: int
    summary_length: int


# Tool implementations

async def search_documents(
    args: SearchDocumentsArgs,
    library_id: str
) -> SearchDocumentsResult:
    """
    Search documents using vector similarity.

    Finds documents semantically similar to the query text.
    Returns document references with relevance scores.
    """
    ai_client = get_ai_client()
    vector_db = get_vector_db_service()

    try:
        query_embedding = ai_client.generate_embedding(args.query)
        results = vector_db.search(
            library_id=library_id,
            query_embedding=query_embedding,
            top_k=args.top_k
        )

        return SearchDocumentsResult(
            results=results,
            total=len(results),
            query=args.query
        )
    except Exception as e:
        logger.error(f"search_documents failed: {e}")
        raise


async def search_graph(
    args: SearchGraphArgs,
    library_id: str
) -> SearchGraphResult:
    """
    Search using the knowledge graph.

    Finds documents through entity relationships and graph traversal.
    Returns documents connected to entities matching the query.
    """
    graph_db = get_graph_db_service()

    try:
        results = graph_db.search_by_entity(
            library_id=library_id,
            query=args.query,
            top_k=args.top_k
        )

        return SearchGraphResult(
            results=results,
            total=len(results),
            query=args.query
        )
    except Exception as e:
        logger.error(f"search_graph failed: {e}")
        raise


async def get_entities(
    args: GetEntitiesArgs,
    library_id: str
) -> GetEntitiesResult:
    """
    Get entities extracted from a document.

    Returns all entities (names, concepts, etc.) found in the specified document.
    """
    graph_db = get_graph_db_service()

    try:
        stats = graph_db.get_source_stats(
            library_id=library_id,
            source_file=args.source_file
        )
        entities = stats.get("entities", [])

        # Filter by type if specified
        if args.entity_type:
            entities = [e for e in entities if e.get("type") == args.entity_type]

        return GetEntitiesResult(
            entities=entities,
            total=len(entities),
            source_file=args.source_file
        )
    except Exception as e:
        logger.error(f"get_entities failed: {e}")
        raise


async def get_relationships(
    args: GetRelationshipsArgs,
    library_id: str
) -> GetRelationshipsResult:
    """
    Get relationships for an entity.

    Finds how an entity is connected to other entities in the knowledge graph.
    """
    graph_db = get_graph_db_service()

    try:
        related = graph_db.get_related_entities(
            library_id=library_id,
            entity_name=args.entity_name,
            max_depth=args.max_depth
        )

        # Filter by relationship type if specified
        if args.relationship_type:
            related = [
                r for r in related
                if r.get("type", "").lower() == args.relationship_type.lower()
            ]

        return GetRelationshipsResult(
            relationships=related,
            total=len(related),
            entity_name=args.entity_name
        )
    except Exception as e:
        logger.error(f"get_relationships failed: {e}")
        raise


async def compare_documents(
    args: CompareDocumentsArgs,
    library_id: str
) -> CompareDocumentsResult:
    """
    Compare two documents.

    Analyzes shared and unique entities between documents,
    and finds relationships connecting them.
    """
    graph_db = get_graph_db_service()

    try:
        # Get entities for both documents
        entities_1 = set()
        entities_2 = set()

        stats_1 = graph_db.get_source_stats(
            library_id=library_id,
            source_file=args.source_file_1
        )
        for e in stats_1.get("entities", []):
            entities_1.add(e.get("name", ""))

        stats_2 = graph_db.get_source_stats(
            library_id=library_id,
            source_file=args.source_file_2
        )
        for e in stats_2.get("entities", []):
            entities_2.add(e.get("name", ""))

        shared = entities_1 & entities_2
        unique_1 = entities_1 - entities_2
        unique_2 = entities_2 - entities_1

        # Find relationships between documents
        relationships = []
        for entity in shared:
            rels = graph_db.get_related_entities(
                library_id=library_id,
                entity_name=entity,
                max_depth=1
            )
            relationships.extend(rels)

        # Calculate similarity score (Jaccard index)
        if entities_1 or entities_2:
            similarity = len(shared) / len(entities_1 | entities_2)
        else:
            similarity = 0.0

        return CompareDocumentsResult(
            shared_entities=list(shared),
            unique_to_first=list(unique_1),
            unique_to_second=list(unique_2),
            relationships_between=relationships[:20],  # Limit relationships
            similarity_score=round(similarity, 3)
        )
    except Exception as e:
        logger.error(f"compare_documents failed: {e}")
        raise


async def get_document_chunks(
    args: GetDocumentChunksArgs,
    library_id: str
) -> GetDocumentChunksResult:
    """
    Get text chunks from a document.

    Returns the actual text content of document chunks.
    """
    vector_db = get_vector_db_service()

    try:
        chunks = vector_db.get_chunks_for_source(
            library_id=library_id,
            source_file=args.source_file,
            page=args.page,
            limit=args.limit
        )

        return GetDocumentChunksResult(
            chunks=chunks,
            total=len(chunks),
            source_file=args.source_file
        )
    except Exception as e:
        logger.error(f"get_document_chunks failed: {e}")
        raise


async def summarize_text(
    args: SummarizeTextArgs,
    library_id: str
) -> SummarizeTextResult:
    """
    Generate a summary of text.

    Uses the LLM to create a concise summary.
    """
    ai_client = get_ai_client()

    style_prompts = {
        "concise": "Provide a brief, focused summary in 2-3 sentences.",
        "detailed": "Provide a comprehensive summary covering all main points.",
        "bullet": "Provide a summary as a bulleted list of key points.",
    }

    style_instruction = style_prompts.get(args.style, style_prompts["concise"])

    try:
        messages = [
            {
                "role": "system",
                "content": f"You are a summarization assistant. {style_instruction} "
                           f"Keep the summary under {args.max_length} words."
            },
            {
                "role": "user",
                "content": f"Please summarize the following text:\n\n{args.text}"
            }
        ]

        summary = ai_client.chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=args.max_length * 2  # Approximate
        )

        return SummarizeTextResult(
            summary=summary,
            original_length=len(args.text.split()),
            summary_length=len(summary.split())
        )
    except Exception as e:
        logger.error(f"summarize_text failed: {e}")
        raise


# Delegation tool

class DelegateToAgentArgs(BaseModel):
    """Arguments for delegating a task to another agent."""
    agent_id: str = Field(description="ID of the agent to delegate to (e.g. 'template-doc-reviewer')")
    task: str = Field(description="Task description for the delegated agent")


class DelegateToAgentResult(BaseModel):
    """Result from a delegated agent execution."""
    agent_name: str
    result: str
    iterations_used: int
    status: str
    error: Optional[str] = None


async def delegate_to_agent(
    args: DelegateToAgentArgs,
    library_id: str,
) -> DelegateToAgentResult:
    """
    Delegate a task to another agent and return its result.

    The delegated agent runs autonomously (no approval prompts) within the
    same library context. Delegation depth is limited to 1 level — a
    delegated agent cannot delegate further.
    """
    global _current_delegation_depth

    # Guard: prevent nested delegation
    if _current_delegation_depth >= MAX_DELEGATION_DEPTH:
        return DelegateToAgentResult(
            agent_name="unknown",
            result="",
            iterations_used=0,
            status="failed",
            error="Maximum delegation depth reached. Delegated agents cannot delegate further.",
        )

    # Lazy imports to avoid circular dependency
    from app.services.agent_executor import get_agent_executor
    from app.services.agent_manager import get_agent_manager
    from app.models.agents import ApprovalMode

    manager = get_agent_manager()
    executor = get_agent_executor()

    # Look up target agent
    agent = manager.get_agent(args.agent_id)
    if not agent:
        # Try matching by name (case-insensitive) as a fallback
        for a in manager.list_agents(include_templates=True):
            if a.name.lower() == args.agent_id.lower():
                agent = a
                break
    if not agent:
        return DelegateToAgentResult(
            agent_name="unknown",
            result="",
            iterations_used=0,
            status="failed",
            error=f"Agent not found: {args.agent_id}",
        )

    # Guard: prevent self-delegation (caller can't delegate to itself)
    # The orchestrator's ID is not available here, but we block the
    # delegate_to_agent tool from appearing in sub-agent's tool list by
    # stripping it below.

    # Create a copy of the agent with forced autonomous mode and no
    # delegation capability
    from app.models.agents import AgentDefinition
    sub_tools = [t for t in agent.tools if t != ToolPermission.DELEGATE_TO_AGENT]
    sub_agent = AgentDefinition(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        tools=sub_tools,
        approval_mode=ApprovalMode.NEVER,
        max_iterations=agent.max_iterations,
        temperature=agent.temperature,
        is_template=agent.is_template,
    )

    # Create a subtask
    subtask = manager.create_task(
        agent_id=sub_agent.id,
        library_id=library_id,
        prompt=args.task,
    )

    # Run with depth guard
    _current_delegation_depth += 1
    final_result = None
    final_error = None
    iteration_count = 0

    try:
        async for event in executor.run_agent(subtask, sub_agent):
            if event["type"] == "complete":
                final_result = event.get("result")
                iteration_count = event.get("iterations", 0)
            elif event["type"] == "error":
                final_error = event.get("message")
    except Exception as e:
        logger.error(f"delegate_to_agent failed for {agent.name}: {e}")
        final_error = str(e)
    finally:
        _current_delegation_depth -= 1

    return DelegateToAgentResult(
        agent_name=agent.name,
        result=final_result or "",
        iterations_used=iteration_count,
        status="completed" if final_result and not final_error else "failed",
        error=final_error,
    )


# Tool registry mapping ToolPermission to implementations
TOOL_REGISTRY: dict[ToolPermission, dict] = {
    ToolPermission.SEARCH_DOCUMENTS: {
        "function": search_documents,
        "args_model": SearchDocumentsArgs,
        "result_model": SearchDocumentsResult,
        "description": "Search documents using vector similarity",
    },
    ToolPermission.SEARCH_GRAPH: {
        "function": search_graph,
        "args_model": SearchGraphArgs,
        "result_model": SearchGraphResult,
        "description": "Search using the knowledge graph",
    },
    ToolPermission.GET_ENTITIES: {
        "function": get_entities,
        "args_model": GetEntitiesArgs,
        "result_model": GetEntitiesResult,
        "description": "Get entities extracted from a document",
    },
    ToolPermission.GET_RELATIONSHIPS: {
        "function": get_relationships,
        "args_model": GetRelationshipsArgs,
        "result_model": GetRelationshipsResult,
        "description": "Get relationships for an entity",
    },
    ToolPermission.COMPARE_DOCUMENTS: {
        "function": compare_documents,
        "args_model": CompareDocumentsArgs,
        "result_model": CompareDocumentsResult,
        "description": "Compare two documents",
    },
    ToolPermission.GET_DOCUMENT_CHUNKS: {
        "function": get_document_chunks,
        "args_model": GetDocumentChunksArgs,
        "result_model": GetDocumentChunksResult,
        "description": "Get text chunks from a document",
    },
    ToolPermission.SUMMARIZE_TEXT: {
        "function": summarize_text,
        "args_model": SummarizeTextArgs,
        "result_model": SummarizeTextResult,
        "description": "Generate a summary of text",
    },
    ToolPermission.DELEGATE_TO_AGENT: {
        "function": delegate_to_agent,
        "args_model": DelegateToAgentArgs,
        "result_model": DelegateToAgentResult,
        "description": "Delegate a task to another specialized agent and get its result",
    },
}


def get_tool_info(permission: ToolPermission) -> dict | None:
    """Get information about a tool by its permission."""
    return TOOL_REGISTRY.get(permission)


def get_all_tool_descriptions() -> list[dict]:
    """Get descriptions of all available tools."""
    return [
        {
            "name": perm.value,
            "description": info["description"],
            "args_schema": info["args_model"].model_json_schema(),
        }
        for perm, info in TOOL_REGISTRY.items()
    ]
