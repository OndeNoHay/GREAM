"""
Settings API routes.

Handles AI API configuration management including graph extraction settings.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import (
    get_settings_manager, AISettings, GraphSettings, GoogleDriveSettings,
    RelationshipSettings, DocumentStructureRelations, ComponentRelations,
    ProcessRelations, SemanticRelations, HierarchyRelations, ReferenceRelations
)
from app.services.ai_client import get_ai_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class GraphSettingsModel(BaseModel):
    """Graph settings model for API."""
    enable_graph_extraction: Optional[bool] = None
    extraction_method: Optional[str] = Field(
        default=None,
        description="Extraction method: 'regex' (fast, CPU) or 'llm' (accurate, GPU)"
    )
    max_entities_per_chunk: Optional[int] = Field(default=None, ge=1, le=50)
    extract_proper_nouns: Optional[bool] = None
    extract_emails: Optional[bool] = None
    extract_urls: Optional[bool] = None
    enable_entity_relationships: Optional[bool] = None


class GoogleDriveSettingsModel(BaseModel):
    """Google Drive settings model for API."""
    enabled: Optional[bool] = None
    export_format: Optional[str] = Field(
        default=None,
        description="Export format for Google Docs/Sheets: 'pdf', 'txt', 'docx'"
    )


class AISettingsUpdate(BaseModel):
    """Request model for updating AI settings."""
    api_base_url: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(default=None)
    embedding_model: Optional[str] = Field(default=None)
    chat_model: Optional[str] = Field(default=None)
    chunk_size: Optional[int] = Field(default=None, ge=100, le=4000)
    chunk_overlap: Optional[int] = Field(default=None, ge=0, le=500)
    max_conversation_history: Optional[int] = Field(default=None, ge=0, le=20)
    proxy_url: Optional[str] = Field(default=None)
    proxy_username: Optional[str] = Field(default=None)
    proxy_password: Optional[str] = Field(default=None)
    ssl_certificate_path: Optional[str] = Field(default=None)
    graph: Optional[GraphSettingsModel] = None
    google_drive: Optional[GoogleDriveSettingsModel] = None


class GraphSettingsResponse(BaseModel):
    """Response model for graph settings."""
    enable_graph_extraction: bool
    extraction_method: str
    max_entities_per_chunk: int
    extract_proper_nouns: bool
    extract_emails: bool
    extract_urls: bool
    enable_entity_relationships: bool


class GoogleDriveSettingsResponse(BaseModel):
    """Response model for Google Drive settings."""
    enabled: bool
    export_format: str


class AISettingsResponse(BaseModel):
    """Response model for AI settings."""
    api_base_url: str
    api_key_masked: str  # Show only last 4 chars
    embedding_model: str
    chat_model: str
    chunk_size: int
    chunk_overlap: int
    max_conversation_history: int
    proxy_url: Optional[str]
    proxy_username: Optional[str]
    proxy_password_masked: Optional[str]  # masked like api_key
    ssl_certificate_path: Optional[str]
    graph: GraphSettingsResponse
    google_drive: GoogleDriveSettingsResponse


class ConnectionTestResponse(BaseModel):
    """Response model for connection test."""
    success: bool
    embeddings_ok: bool
    chat_ok: bool
    message: str


def mask_api_key(key: str) -> str:
    """Mask a secret string, showing only last 4 characters."""
    if not key or len(key) <= 4:
        return "****"
    return "*" * (len(key) - 4) + key[-4:]


def _build_settings_response(ai) -> AISettingsResponse:
    """Build AISettingsResponse from an AISettings instance."""
    return AISettingsResponse(
        api_base_url=ai.api_base_url,
        api_key_masked=mask_api_key(ai.api_key),
        embedding_model=ai.embedding_model,
        chat_model=ai.chat_model,
        chunk_size=ai.chunk_size,
        chunk_overlap=ai.chunk_overlap,
        max_conversation_history=ai.max_conversation_history,
        proxy_url=ai.proxy_url,
        proxy_username=ai.proxy_username,
        proxy_password_masked=mask_api_key(ai.proxy_password) if ai.proxy_password else None,
        ssl_certificate_path=ai.ssl_certificate_path,
        graph=GraphSettingsResponse(
            enable_graph_extraction=ai.graph.enable_graph_extraction,
            extraction_method=ai.graph.extraction_method,
            max_entities_per_chunk=ai.graph.max_entities_per_chunk,
            extract_proper_nouns=ai.graph.extract_proper_nouns,
            extract_emails=ai.graph.extract_emails,
            extract_urls=ai.graph.extract_urls,
            enable_entity_relationships=ai.graph.enable_entity_relationships,
        ),
        google_drive=GoogleDriveSettingsResponse(
            enabled=ai.google_drive.enabled,
            export_format=ai.google_drive.export_format,
        ),
    )


@router.get(
    "",
    response_model=AISettingsResponse,
    summary="Get current AI settings"
)
async def get_settings() -> AISettingsResponse:
    """Get current AI API configuration (API key masked)."""
    settings_mgr = get_settings_manager()
    ai = settings_mgr.ai_settings

    return _build_settings_response(ai)


@router.put(
    "",
    response_model=AISettingsResponse,
    summary="Update AI settings"
)
async def update_settings(request: AISettingsUpdate) -> AISettingsResponse:
    """
    Update AI API configuration.

    All fields are optional - only provided fields will be updated.
    Settings are persisted to disk.
    """
    settings_mgr = get_settings_manager()

    # Build update dict with only provided values
    updates = {}
    if request.api_base_url is not None:
        updates["api_base_url"] = request.api_base_url
    if request.api_key is not None:
        updates["api_key"] = request.api_key
    if request.embedding_model is not None:
        updates["embedding_model"] = request.embedding_model
    if request.chat_model is not None:
        updates["chat_model"] = request.chat_model
    if request.chunk_size is not None:
        updates["chunk_size"] = request.chunk_size
    if request.chunk_overlap is not None:
        updates["chunk_overlap"] = request.chunk_overlap
    if request.max_conversation_history is not None:
        updates["max_conversation_history"] = request.max_conversation_history
    if request.proxy_url is not None:
        updates["proxy_url"] = request.proxy_url or None  # empty string → None
    if request.proxy_username is not None:
        updates["proxy_username"] = request.proxy_username or None
    if request.proxy_password is not None:
        updates["proxy_password"] = request.proxy_password or None
    if request.ssl_certificate_path is not None:
        updates["ssl_certificate_path"] = request.ssl_certificate_path or None

    # Handle graph settings
    if request.graph is not None:
        current_graph = settings_mgr.ai_settings.graph
        graph_updates = {}

        if request.graph.enable_graph_extraction is not None:
            graph_updates["enable_graph_extraction"] = request.graph.enable_graph_extraction
        else:
            graph_updates["enable_graph_extraction"] = current_graph.enable_graph_extraction

        if request.graph.extraction_method is not None:
            # Validate extraction method
            if request.graph.extraction_method not in ("regex", "llm"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="extraction_method must be 'regex' or 'llm'"
                )
            graph_updates["extraction_method"] = request.graph.extraction_method
        else:
            graph_updates["extraction_method"] = current_graph.extraction_method

        if request.graph.max_entities_per_chunk is not None:
            graph_updates["max_entities_per_chunk"] = request.graph.max_entities_per_chunk
        else:
            graph_updates["max_entities_per_chunk"] = current_graph.max_entities_per_chunk

        if request.graph.extract_proper_nouns is not None:
            graph_updates["extract_proper_nouns"] = request.graph.extract_proper_nouns
        else:
            graph_updates["extract_proper_nouns"] = current_graph.extract_proper_nouns

        if request.graph.extract_emails is not None:
            graph_updates["extract_emails"] = request.graph.extract_emails
        else:
            graph_updates["extract_emails"] = current_graph.extract_emails

        if request.graph.extract_urls is not None:
            graph_updates["extract_urls"] = request.graph.extract_urls
        else:
            graph_updates["extract_urls"] = current_graph.extract_urls

        if request.graph.enable_entity_relationships is not None:
            graph_updates["enable_entity_relationships"] = request.graph.enable_entity_relationships
        else:
            graph_updates["enable_entity_relationships"] = current_graph.enable_entity_relationships

        # Preserve relationships settings
        graph_updates["relationships"] = current_graph.relationships

        updates["graph"] = GraphSettings(**graph_updates)
        logger.info(f"Saving graph settings with extraction_method='{graph_updates.get('extraction_method')}'")

    # Handle Google Drive settings
    if request.google_drive is not None:
        current_gdrive = settings_mgr.ai_settings.google_drive
        gdrive_updates = {}

        if request.google_drive.enabled is not None:
            gdrive_updates["enabled"] = request.google_drive.enabled
        else:
            gdrive_updates["enabled"] = current_gdrive.enabled

        if request.google_drive.export_format is not None:
            # Validate export format
            if request.google_drive.export_format not in ("pdf", "txt", "docx"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="export_format must be 'pdf', 'txt', or 'docx'"
                )
            gdrive_updates["export_format"] = request.google_drive.export_format
        else:
            gdrive_updates["export_format"] = current_gdrive.export_format

        updates["google_drive"] = GoogleDriveSettings(**gdrive_updates)
        logger.info(f"Saving Google Drive settings with export_format='{gdrive_updates.get('export_format')}'")

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No settings provided to update"
        )

    try:
        ai = settings_mgr.update_ai_settings(**updates)
        logger.info(f"AI settings updated: {list(updates.keys())}")

        return _build_settings_response(ai)

    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update settings: {e}"
        )


@router.post(
    "/reset",
    response_model=AISettingsResponse,
    summary="Reset settings to defaults"
)
async def reset_settings() -> AISettingsResponse:
    """Reset AI settings to default values."""
    settings_mgr = get_settings_manager()
    ai = settings_mgr.reset_to_defaults()

    logger.info("AI settings reset to defaults")

    return AISettingsResponse(
        api_base_url=ai.api_base_url,
        api_key_masked=mask_api_key(ai.api_key),
        embedding_model=ai.embedding_model,
        chat_model=ai.chat_model,
        chunk_size=ai.chunk_size,
        chunk_overlap=ai.chunk_overlap,
        graph=GraphSettingsResponse(
            enable_graph_extraction=ai.graph.enable_graph_extraction,
            extraction_method=ai.graph.extraction_method,
            max_entities_per_chunk=ai.graph.max_entities_per_chunk,
            extract_proper_nouns=ai.graph.extract_proper_nouns,
            extract_emails=ai.graph.extract_emails,
            extract_urls=ai.graph.extract_urls,
            enable_entity_relationships=ai.graph.enable_entity_relationships
        ),
        google_drive=GoogleDriveSettingsResponse(
            enabled=ai.google_drive.enabled,
            export_format=ai.google_drive.export_format
        )
    )


@router.post(
    "/test",
    response_model=ConnectionTestResponse,
    summary="Test AI API connection"
)
async def test_connection() -> ConnectionTestResponse:
    """
    Test connection to the AI API.

    Tests both embedding and chat endpoints.
    """
    ai_client = get_ai_client()
    results = ai_client.test_connection()

    embeddings_ok = results.get("embeddings", False)
    chat_ok = results.get("chat", False)

    if embeddings_ok and chat_ok:
        message = "All API endpoints are working"
    elif embeddings_ok:
        message = "Embeddings API working, Chat API failed"
    elif chat_ok:
        message = "Chat API working, Embeddings API failed"
    else:
        message = "Both API endpoints failed. Check your settings."

    return ConnectionTestResponse(
        success=embeddings_ok and chat_ok,
        embeddings_ok=embeddings_ok,
        chat_ok=chat_ok,
        message=message
    )


class ModelsResponse(BaseModel):
    """Response model for available models list."""
    models: list[str]


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="List available models from the configured API"
)
async def list_models() -> ModelsResponse:
    """
    Query the configured AI API for available models.

    Returns all model IDs reported by the /models endpoint.
    Works with any OpenAI-compatible API (Ollama, Mistral, OpenAI, etc.).
    """
    try:
        ai_client = get_ai_client()
        model_ids = ai_client.list_models()
        return ModelsResponse(models=model_ids)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve models from API: {e}"
        )


# =============================================================================
# Relationship Settings Endpoints
# =============================================================================

class RelationshipSettingsUpdate(BaseModel):
    """Request model for updating relationship settings."""

    # Document structure
    document_structure_enabled: Optional[bool] = None
    next_chunk: Optional[bool] = None
    same_page: Optional[bool] = None
    same_section: Optional[bool] = None

    # Component relations
    component_enabled: Optional[bool] = None
    part_of: Optional[bool] = None
    connects_to: Optional[bool] = None
    supplies_to: Optional[bool] = None
    controls: Optional[bool] = None

    # Process relations
    process_enabled: Optional[bool] = None
    precedes: Optional[bool] = None
    triggers: Optional[bool] = None
    requires: Optional[bool] = None

    # Semantic relations
    semantic_enabled: Optional[bool] = None
    co_occurs_sentence: Optional[bool] = None
    co_occurs_chunk: Optional[bool] = None
    related_to: Optional[bool] = None

    # Hierarchy relations
    hierarchy_enabled: Optional[bool] = None
    is_a: Optional[bool] = None
    has_property: Optional[bool] = None

    # Reference relations
    reference_enabled: Optional[bool] = None
    references: Optional[bool] = None
    cites: Optional[bool] = None


class RelationshipSettingsResponse(BaseModel):
    """Response model for relationship settings."""

    document_structure: dict
    component: dict
    process: dict
    semantic: dict
    hierarchy: dict
    reference: dict
    enabled_relations: list[str]


@router.get(
    "/relationships",
    response_model=RelationshipSettingsResponse,
    summary="Get relationship extraction settings"
)
async def get_relationship_settings() -> RelationshipSettingsResponse:
    """Get current relationship extraction settings."""
    settings_mgr = get_settings_manager()
    rel = settings_mgr.ai_settings.graph.relationships

    return RelationshipSettingsResponse(
        document_structure={
            "enabled": rel.document_structure.enabled,
            "next_chunk": rel.document_structure.next_chunk,
            "same_page": rel.document_structure.same_page,
            "same_section": rel.document_structure.same_section,
        },
        component={
            "enabled": rel.component.enabled,
            "part_of": rel.component.part_of,
            "connects_to": rel.component.connects_to,
            "supplies_to": rel.component.supplies_to,
            "controls": rel.component.controls,
        },
        process={
            "enabled": rel.process.enabled,
            "precedes": rel.process.precedes,
            "triggers": rel.process.triggers,
            "requires": rel.process.requires,
        },
        semantic={
            "enabled": rel.semantic.enabled,
            "co_occurs_sentence": rel.semantic.co_occurs_sentence,
            "co_occurs_chunk": rel.semantic.co_occurs_chunk,
            "related_to": rel.semantic.related_to,
        },
        hierarchy={
            "enabled": rel.hierarchy.enabled,
            "is_a": rel.hierarchy.is_a,
            "has_property": rel.hierarchy.has_property,
        },
        reference={
            "enabled": rel.reference.enabled,
            "references": rel.reference.references,
            "cites": rel.reference.cites,
        },
        enabled_relations=rel.get_enabled_relations()
    )


@router.put(
    "/relationships",
    response_model=RelationshipSettingsResponse,
    summary="Update relationship extraction settings"
)
async def update_relationship_settings(
    request: RelationshipSettingsUpdate
) -> RelationshipSettingsResponse:
    """
    Update relationship extraction settings.

    All fields are optional - only provided fields will be updated.
    Note: Documents need to be re-ingested for changes to take effect.
    """
    settings_mgr = get_settings_manager()
    current = settings_mgr.ai_settings.graph.relationships

    # Build updated relationship settings
    doc_struct = DocumentStructureRelations(
        enabled=request.document_structure_enabled if request.document_structure_enabled is not None else current.document_structure.enabled,
        next_chunk=request.next_chunk if request.next_chunk is not None else current.document_structure.next_chunk,
        same_page=request.same_page if request.same_page is not None else current.document_structure.same_page,
        same_section=request.same_section if request.same_section is not None else current.document_structure.same_section,
    )

    component = ComponentRelations(
        enabled=request.component_enabled if request.component_enabled is not None else current.component.enabled,
        part_of=request.part_of if request.part_of is not None else current.component.part_of,
        connects_to=request.connects_to if request.connects_to is not None else current.component.connects_to,
        supplies_to=request.supplies_to if request.supplies_to is not None else current.component.supplies_to,
        controls=request.controls if request.controls is not None else current.component.controls,
    )

    process = ProcessRelations(
        enabled=request.process_enabled if request.process_enabled is not None else current.process.enabled,
        precedes=request.precedes if request.precedes is not None else current.process.precedes,
        triggers=request.triggers if request.triggers is not None else current.process.triggers,
        requires=request.requires if request.requires is not None else current.process.requires,
    )

    semantic = SemanticRelations(
        enabled=request.semantic_enabled if request.semantic_enabled is not None else current.semantic.enabled,
        co_occurs_sentence=request.co_occurs_sentence if request.co_occurs_sentence is not None else current.semantic.co_occurs_sentence,
        co_occurs_chunk=request.co_occurs_chunk if request.co_occurs_chunk is not None else current.semantic.co_occurs_chunk,
        related_to=request.related_to if request.related_to is not None else current.semantic.related_to,
    )

    hierarchy = HierarchyRelations(
        enabled=request.hierarchy_enabled if request.hierarchy_enabled is not None else current.hierarchy.enabled,
        is_a=request.is_a if request.is_a is not None else current.hierarchy.is_a,
        has_property=request.has_property if request.has_property is not None else current.hierarchy.has_property,
    )

    reference = ReferenceRelations(
        enabled=request.reference_enabled if request.reference_enabled is not None else current.reference.enabled,
        references=request.references if request.references is not None else current.reference.references,
        cites=request.cites if request.cites is not None else current.reference.cites,
    )

    new_rel_settings = RelationshipSettings(
        document_structure=doc_struct,
        component=component,
        process=process,
        semantic=semantic,
        hierarchy=hierarchy,
        reference=reference,
    )

    # Update graph settings with new relationships
    current_graph = settings_mgr.ai_settings.graph
    new_graph = GraphSettings(
        enable_graph_extraction=current_graph.enable_graph_extraction,
        extraction_method=current_graph.extraction_method,
        max_entities_per_chunk=current_graph.max_entities_per_chunk,
        extract_proper_nouns=current_graph.extract_proper_nouns,
        extract_emails=current_graph.extract_emails,
        extract_urls=current_graph.extract_urls,
        relationships=new_rel_settings,
        enable_entity_relationships=current_graph.enable_entity_relationships,
    )

    try:
        settings_mgr.update_ai_settings(graph=new_graph)
        logger.info("Relationship settings updated")

        return RelationshipSettingsResponse(
            document_structure={
                "enabled": new_rel_settings.document_structure.enabled,
                "next_chunk": new_rel_settings.document_structure.next_chunk,
                "same_page": new_rel_settings.document_structure.same_page,
                "same_section": new_rel_settings.document_structure.same_section,
            },
            component={
                "enabled": new_rel_settings.component.enabled,
                "part_of": new_rel_settings.component.part_of,
                "connects_to": new_rel_settings.component.connects_to,
                "supplies_to": new_rel_settings.component.supplies_to,
                "controls": new_rel_settings.component.controls,
            },
            process={
                "enabled": new_rel_settings.process.enabled,
                "precedes": new_rel_settings.process.precedes,
                "triggers": new_rel_settings.process.triggers,
                "requires": new_rel_settings.process.requires,
            },
            semantic={
                "enabled": new_rel_settings.semantic.enabled,
                "co_occurs_sentence": new_rel_settings.semantic.co_occurs_sentence,
                "co_occurs_chunk": new_rel_settings.semantic.co_occurs_chunk,
                "related_to": new_rel_settings.semantic.related_to,
            },
            hierarchy={
                "enabled": new_rel_settings.hierarchy.enabled,
                "is_a": new_rel_settings.hierarchy.is_a,
                "has_property": new_rel_settings.hierarchy.has_property,
            },
            reference={
                "enabled": new_rel_settings.reference.enabled,
                "references": new_rel_settings.reference.references,
                "cites": new_rel_settings.reference.cites,
            },
            enabled_relations=new_rel_settings.get_enabled_relations()
        )
    except Exception as e:
        logger.error(f"Failed to update relationship settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update settings: {e}"
        )
