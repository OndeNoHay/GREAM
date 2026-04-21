"""
Configuration module for GraphRagExec.

Handles Windows 11 paths, %APPDATA% detection, persistent storage,
and API configuration management.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Application constants
APP_NAME: str = "GraphRagExec"
APP_VERSION: str = "1.0.2"
# updates on graph chips information and opening sources by page in browser when possible
# Configure logging — always write to a file so frozen exes have a log to inspect
# included testers feedback on history, files upload, configurable parameters, etc
def _setup_logging() -> None:
    log_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "app.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handlers: list[logging.Handler] = [file_handler]
    except Exception:
        handlers = []

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers or None,
    )


_setup_logging()
logger = logging.getLogger(__name__)


def is_frozen() -> bool:
    """Check if running as a PyInstaller frozen executable."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def get_app_data_dir() -> Path:
    """
    Get the application data directory for persistent storage.

    On Windows: %APPDATA%/GraphRagExec/
    On Linux/Mac: ~/.local/share/GraphRagExec/ (for development)
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            app_dir = Path(appdata) / APP_NAME
        else:
            app_dir = Path.home() / "AppData" / "Roaming" / APP_NAME
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            app_dir = Path(xdg_data) / APP_NAME
        else:
            app_dir = Path.home() / ".local" / "share" / APP_NAME

    return app_dir


def ensure_data_directories() -> dict[str, Path]:
    """
    Create and return all required data directories.

    Creates the following structure in %APPDATA%/GraphRagExec/:
    - /vector_db/  - ChromaDB persistent storage
    - /graph_db/   - Kùzu graph database storage
    - /config/     - Application configuration
    - /logs/       - Application logs
    """
    base_dir = get_app_data_dir()

    directories = {
        "base": base_dir,
        "vector_db": base_dir / "vector_db",
        "graph_db": base_dir / "graph_db",
        "config": base_dir / "config",
        "logs": base_dir / "logs",
        "files": base_dir / "files",
    }

    for name, path in directories.items():
        try:
            path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Directory ensured: {path}")
        except PermissionError as e:
            logger.error(f"Permission denied creating {name} directory: {path}")
            raise RuntimeError(
                f"Cannot create required directory {path}. "
                "Please check folder permissions."
            ) from e
        except OSError as e:
            logger.error(f"OS error creating {name} directory: {e}")
            raise

    return directories


# =============================================================================
# RELATIONSHIP TYPE DEFINITIONS
# =============================================================================

class RelationshipCategory(BaseModel):
    """Category of relationship types with toggles."""
    enabled: bool = True
    description: str = ""


class DocumentStructureRelations(BaseModel):
    """
    Document structure relationships - capture how content is organized.
    Useful for: All document types, especially structured documents.
    """
    enabled: bool = Field(default=True, description="Enable document structure relations")

    # Sequential relationships between chunks
    next_chunk: bool = Field(default=True, description="NEXT_CHUNK: Link sequential chunks")
    same_page: bool = Field(default=True, description="SAME_PAGE: Link chunks on same page")
    same_section: bool = Field(default=False, description="SAME_SECTION: Link chunks in same section")


class ComponentRelations(BaseModel):
    """
    Component/Part relationships - physical or logical composition.
    Useful for: Technical manuals, engineering docs, product specs.

    Detected via patterns like:
    - "X contains Y", "X consists of Y", "Y is part of X"
    - "X connects to Y", "X is connected to Y"
    - "X supplies Y", "X feeds Y", "gas flows from X to Y"
    - "X controls Y", "X regulates Y", "X activates Y"
    """
    enabled: bool = Field(default=True, description="Enable component relationships")

    part_of: bool = Field(default=True, description="PART_OF: Physical/logical containment")
    connects_to: bool = Field(default=True, description="CONNECTS_TO: Physical connections")
    supplies_to: bool = Field(default=True, description="SUPPLIES_TO: Flow of material/energy/data")
    controls: bool = Field(default=True, description="CONTROLS: Control/regulation relationships")


class ProcessRelations(BaseModel):
    """
    Process/Procedure relationships - sequences and dependencies.
    Useful for: Procedures, workflows, instructions, troubleshooting.

    Detected via patterns like:
    - "First X, then Y", "After X, do Y", "X before Y"
    - "X triggers Y", "X causes Y", "X leads to Y"
    - "X requires Y", "X depends on Y", "X needs Y"
    """
    enabled: bool = Field(default=True, description="Enable process relationships")

    precedes: bool = Field(default=True, description="PRECEDES: Sequential ordering")
    triggers: bool = Field(default=True, description="TRIGGERS: Cause-effect relationships")
    requires: bool = Field(default=True, description="REQUIRES: Dependencies")


class SemanticRelations(BaseModel):
    """
    Semantic/Co-occurrence relationships - entities appearing together.
    Useful for: All documents, especially unstructured text.

    Detected via:
    - Entities in same sentence
    - Entities in same paragraph/chunk
    - Proximity-based relationships
    """
    enabled: bool = Field(default=True, description="Enable semantic relationships")

    co_occurs_sentence: bool = Field(
        default=True,
        description="CO_OCCURS: Entities mentioned in same sentence (strong relationship)"
    )
    co_occurs_chunk: bool = Field(
        default=True,
        description="CO_OCCURS: Entities in same chunk (weaker relationship)"
    )
    related_to: bool = Field(
        default=False,
        description="RELATED_TO: Generic relationship for unclassified patterns"
    )


class HierarchyRelations(BaseModel):
    """
    Hierarchy/Classification relationships - taxonomies and types.
    Useful for: Catalogs, specifications, databases.

    Detected via patterns like:
    - "X is a Y", "X is a type of Y", "X is a kind of Y"
    - "X has property Y", "X features Y"
    """
    enabled: bool = Field(default=False, description="Enable hierarchy relationships")

    is_a: bool = Field(default=True, description="IS_A: Classification/type relationships")
    has_property: bool = Field(default=True, description="HAS_PROPERTY: Attribute relationships")


class ReferenceRelations(BaseModel):
    """
    Reference/Citation relationships - cross-references and citations.
    Useful for: Technical docs, legal docs, academic papers.

    Detected via patterns like:
    - "see X", "refer to X", "as described in X"
    - "according to X", "as per X"
    """
    enabled: bool = Field(default=False, description="Enable reference relationships")

    references: bool = Field(default=True, description="REFERENCES: Internal cross-references")
    cites: bool = Field(default=True, description="CITES: External citations")


class RelationshipSettings(BaseModel):
    """
    Complete relationship extraction settings.

    Controls which relationship types are extracted during document ingestion.
    Different relationship types are useful for different document types.
    """

    # Relationship categories
    document_structure: DocumentStructureRelations = Field(
        default_factory=DocumentStructureRelations,
        description="Document structure relationships (chunks, pages, sections)"
    )
    component: ComponentRelations = Field(
        default_factory=ComponentRelations,
        description="Component relationships (part-of, connects-to, supplies, controls)"
    )
    process: ProcessRelations = Field(
        default_factory=ProcessRelations,
        description="Process relationships (sequence, triggers, requires)"
    )
    semantic: SemanticRelations = Field(
        default_factory=SemanticRelations,
        description="Semantic relationships (co-occurrence, related-to)"
    )
    hierarchy: HierarchyRelations = Field(
        default_factory=HierarchyRelations,
        description="Hierarchy relationships (is-a, has-property)"
    )
    reference: ReferenceRelations = Field(
        default_factory=ReferenceRelations,
        description="Reference relationships (references, cites)"
    )

    def get_enabled_relations(self) -> list[str]:
        """Get list of all enabled relationship types."""
        enabled = []

        if self.document_structure.enabled:
            if self.document_structure.next_chunk:
                enabled.append("NEXT_CHUNK")
            if self.document_structure.same_page:
                enabled.append("SAME_PAGE")
            if self.document_structure.same_section:
                enabled.append("SAME_SECTION")

        if self.component.enabled:
            if self.component.part_of:
                enabled.append("PART_OF")
            if self.component.connects_to:
                enabled.append("CONNECTS_TO")
            if self.component.supplies_to:
                enabled.append("SUPPLIES_TO")
            if self.component.controls:
                enabled.append("CONTROLS")

        if self.process.enabled:
            if self.process.precedes:
                enabled.append("PRECEDES")
            if self.process.triggers:
                enabled.append("TRIGGERS")
            if self.process.requires:
                enabled.append("REQUIRES")

        if self.semantic.enabled:
            if self.semantic.co_occurs_sentence:
                enabled.append("CO_OCCURS_SENTENCE")
            if self.semantic.co_occurs_chunk:
                enabled.append("CO_OCCURS_CHUNK")
            if self.semantic.related_to:
                enabled.append("RELATED_TO")

        if self.hierarchy.enabled:
            if self.hierarchy.is_a:
                enabled.append("IS_A")
            if self.hierarchy.has_property:
                enabled.append("HAS_PROPERTY")

        if self.reference.enabled:
            if self.reference.references:
                enabled.append("REFERENCES")
            if self.reference.cites:
                enabled.append("CITES")

        return enabled


class GraphSettings(BaseModel):
    """Graph database relationship policy settings."""

    # Master toggle for graph extraction
    enable_graph_extraction: bool = Field(
        default=True,
        description="Enable/disable graph entity extraction during ingestion"
    )

    # Extraction method: 'regex' (fast, CPU) or 'llm' (accurate, GPU)
    extraction_method: str = Field(
        default="regex",
        description="Extraction method: 'regex' (fast) or 'llm' (accurate, uses GPU)"
    )

    # Entity extraction limits
    max_entities_per_chunk: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum entities to extract per chunk"
    )

    # Allowed entity types (controls what gets extracted)
    extract_proper_nouns: bool = Field(
        default=True,
        description="Extract proper nouns (names, organizations)"
    )
    extract_emails: bool = Field(
        default=False,
        description="Extract email addresses"
    )
    extract_urls: bool = Field(
        default=False,
        description="Extract URLs"
    )

    # NEW: Relationship extraction settings
    relationships: RelationshipSettings = Field(
        default_factory=RelationshipSettings,
        description="Relationship extraction settings"
    )

    # Legacy field for backwards compatibility
    enable_entity_relationships: bool = Field(
        default=True,
        description="Enable entity-to-entity relationships (now controlled by relationships settings)"
    )


class GoogleDriveSettings(BaseModel):
    """
    Google Drive integration settings.

    Allows importing documents from Google Drive into the RAG pipeline.
    """
    enabled: bool = Field(
        default=False,
        description="Enable Google Drive integration"
    )
    export_format: str = Field(
        default="pdf",
        description="Export format for Google Docs/Sheets: 'pdf', 'txt', 'docx'"
    )


class AISettings(BaseModel):
    """AI API configuration settings."""

    # API Configuration
    api_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="Base URL for OpenAI-compatible API (e.g., Ollama)"
    )
    api_key: str = Field(
        default="ollama",
        description="API key (use 'ollama' for local Ollama)"
    )

    # Proxy Configuration
    proxy_url: Optional[str] = Field(
        default=None,
        description="HTTP/HTTPS proxy URL (e.g., http://proxy.example.com:8080)"
    )
    proxy_username: Optional[str] = Field(
        default=None,
        description="Proxy authentication username"
    )
    proxy_password: Optional[str] = Field(
        default=None,
        description="Proxy authentication password"
    )
    ssl_certificate_path: Optional[str] = Field(
        default=None,
        description="Path to custom SSL certificate or CA bundle file"
    )

    # Model Configuration
    embedding_model: str = Field(
        default="nomic-embed-text",
        description="Model name for embeddings"
    )
    chat_model: str = Field(
        default="llama3.2",
        description="Model name for chat/completions"
    )

    # Processing Configuration
    chunk_size: int = Field(
        default=512,
        ge=100,
        le=4000,
        description="Size of text chunks for processing"
    )
    chunk_overlap: int = Field(
        default=50,
        ge=0,
        le=500,
        description="Overlap between consecutive chunks"
    )

    # Conversation history sent to LLM per chat turn (0 = disabled)
    max_conversation_history: int = Field(
        default=6,
        ge=0,
        le=20,
        description="Number of previous chat messages (user+assistant pairs) sent as context to the LLM. 0 disables conversation memory."
    )

    # Graph settings (embedded)
    graph: GraphSettings = Field(
        default_factory=GraphSettings,
        description="Graph database relationship policy"
    )

    # Google Drive settings (embedded)
    google_drive: GoogleDriveSettings = Field(
        default_factory=GoogleDriveSettings,
        description="Google Drive integration settings"
    )


class AppSettings(BaseSettings):
    """
    Application settings with environment variable support.

    Settings can be overridden via environment variables prefixed with GRAPHRAGEXEC_.
    """

    # Server configuration
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    # Search configuration
    top_k_results: int = 10
    similarity_threshold: float = 0.5

    # Kùzu configuration
    # Buffer pool size for Kùzu graph database
    # Increased from 256MB to 1GB to handle large graph operations
    # Without sufficient buffer, "Failed to claim a frame" errors occur
    kuzu_buffer_pool_size: int = 1024 * 1024 * 1024  # 1 GB

    class Config:
        env_prefix = "GRAPHRAGEXEC_"
        case_sensitive = False


class SettingsManager:
    """
    Manages persistent storage of application settings.

    Settings are stored in %APPDATA%/GraphRagExec/config/settings.json
    """

    _instance: Optional["SettingsManager"] = None
    _settings_file: Path
    _ai_settings: AISettings

    def __new__(cls) -> "SettingsManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize the settings manager."""
        dirs = ensure_data_directories()
        self._settings_file = dirs["config"] / "settings.json"
        self._ai_settings = self._load_settings()

    def _load_settings(self) -> AISettings:
        """Load settings from file or create defaults."""
        if self._settings_file.exists():
            try:
                with open(self._settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"Settings loaded from {self._settings_file}")
                # Debug: show extraction_method from file
                graph_data = data.get("graph", {})
                logger.info(f"Loaded extraction_method from file: '{graph_data.get('extraction_method', 'NOT SET')}'")
                return AISettings(**data)
            except Exception as e:
                # Log the error but do NOT overwrite the existing file with defaults
                logger.error(f"Failed to load settings: {e}. Using defaults (file preserved for inspection).")
                return AISettings()

        # File doesn't exist yet — create defaults and save
        settings = AISettings()
        self._save_settings(settings)
        return settings

    def _save_settings(self, settings: AISettings) -> None:
        """Save settings to file."""
        try:
            data = settings.model_dump()
            # Debug: show what extraction_method is being saved
            logger.info(f"Saving extraction_method: '{data.get('graph', {}).get('extraction_method', 'NOT SET')}'")
            with open(self._settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Settings saved to {self._settings_file}")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            raise

    @property
    def ai_settings(self) -> AISettings:
        """Get current AI settings."""
        return self._ai_settings

    def update_ai_settings(self, **kwargs) -> AISettings:
        """
        Update AI settings with new values.

        Args:
            **kwargs: Setting fields to update.

        Returns:
            Updated AISettings instance.
        """
        current_data = self._ai_settings.model_dump()
        current_data.update(kwargs)
        self._ai_settings = AISettings(**current_data)
        self._save_settings(self._ai_settings)
        return self._ai_settings

    def reset_to_defaults(self) -> AISettings:
        """Reset settings to defaults."""
        self._ai_settings = AISettings()
        self._save_settings(self._ai_settings)
        return self._ai_settings


# Global instances
app_settings = AppSettings()


def get_app_settings() -> AppSettings:
    """Get the global app settings instance."""
    return app_settings


def get_settings_manager() -> SettingsManager:
    """Get the singleton settings manager instance."""
    return SettingsManager()


def log_startup_info() -> None:
    """Log startup information for debugging."""
    settings_mgr = get_settings_manager()
    ai = settings_mgr.ai_settings

    logger.info(f"{'=' * 50}")
    logger.info(f"{APP_NAME} v{APP_VERSION}")
    logger.info(f"{'=' * 50}")
    logger.info(f"Running as frozen executable: {is_frozen()}")
    logger.info(f"Persistent data directory: {get_app_data_dir()}")
    logger.info(f"Server binding: {app_settings.host}:{app_settings.port}")
    logger.info(f"AI API Base URL: {ai.api_base_url}")
    logger.info(f"Embedding model: {ai.embedding_model}")
    logger.info(f"Chat model: {ai.chat_model}")
    logger.info(f"{'=' * 50}")
