"""
Graph database service using Kùzu.

Provides Cypher-compatible graph storage in %APPDATA%/GraphRagExec/graph_db/.
Supports multiple libraries through library_id property on nodes.

Redesigned with:
- Separated phases: entity extraction, normalization, relationship creation
- Document-level aggregation (avoids per-chunk writes)
- Batch inserts for Kùzu optimization
- Configurable relationship policy with multiple relationship types
- Linear entity resolution with canonical string keys
- Kùzu-specific Cypher syntax handling
- Pattern-based relationship extraction for semantic relationships
"""

import concurrent.futures
import gc
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

import kuzu
from openai import RateLimitError as OpenAIRateLimitError, APITimeoutError as OpenAITimeoutError


class RateLimitWait(Exception):
    """Raised when the LLM API returns a rate-limit or timeout error.

    Propagates through asyncio.to_thread() back to the SSE generator in
    documents.py, which awaits the cooldown and retries the chunk.
    """
    def __init__(self, wait_seconds: int = 120):
        self.wait_seconds = wait_seconds
        super().__init__(f"Rate limit hit — wait {wait_seconds}s")

from app.config import get_app_settings, ensure_data_directories, get_settings_manager
from app.services.ai_client import get_ai_client

logger = logging.getLogger(__name__)


# =============================================================================
# RELATIONSHIP PATTERNS
# =============================================================================

# Patterns for detecting component relationships
COMPONENT_PATTERNS = {
    "PART_OF": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:is\s+)?(?:part\s+of|contained\s+in|included\s+in|inside)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:contains|includes|consists\s+of|has)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
    ],
    "CONNECTS_TO": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:connects?\s+to|connected\s+to|attached\s+to|linked\s+to|joined\s+to)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:and|&)\s+(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:are\s+)?connected",
    ],
    "SUPPLIES_TO": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:supplies|feeds|provides|delivers|sends)\s+(?:\w+\s+)?(?:to\s+)?(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(?:gas|water|power|signal|data|air|fuel)\s+(?:flows?|goes?|passes?)\s+from\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+to\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
    ],
    "CONTROLS": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:controls?|regulates?|manages?|operates?|activates?|triggers?)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:is\s+)?controlled\s+by\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
    ],
}

# Patterns for process relationships
PROCESS_PATTERNS = {
    "PRECEDES": [
        r"(?:first|before)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)[,\s]+(?:then|after\s+that)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:comes?\s+before|precedes?|is\s+followed\s+by)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
    ],
    "TRIGGERS": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:triggers?|causes?|initiates?|starts?|begins?|activates?)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(?:when|if)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:\w+\s+){0,3}(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:will\s+)?(?:activate|start|begin)",
    ],
    "REQUIRES": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:requires?|needs?|depends?\s+on|must\s+have)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:cannot|can't|won't)\s+(?:\w+\s+)?without\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
    ],
}

# Patterns for hierarchy relationships
HIERARCHY_PATTERNS = {
    "IS_A": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:is\s+a|is\s+an|is\s+a\s+type\s+of|is\s+a\s+kind\s+of)\s+(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
    ],
    "HAS_PROPERTY": [
        r"(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:has|features?|includes?)\s+(?:a\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)\s+(?:feature|property|attribute|capability)",
    ],
}

# Patterns for reference relationships
REFERENCE_PATTERNS = {
    "REFERENCES": [
        r"(?:see|refer\s+to|as\s+(?:described|shown|mentioned)\s+in)\s+(?:the\s+)?(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b)",
        r"(?:section|chapter|figure|table|appendix)\s+(\b[A-Z]?[a-z0-9]+(?:\.[0-9]+)*\b)",
    ],
}


class GraphDBService:
    """
    Service for graph storage and traversal using Kùzu.

    Supports multiple libraries by storing library_id on nodes.
    Uses batch operations for efficient document ingestion.
    """

    _instance: Optional["GraphDBService"] = None
    _database: Optional[kuzu.Database] = None
    _connection: Optional[kuzu.Connection] = None
    _initialized: bool = False

    # Entity resolution cache per library (canonical key -> entity_id)
    _entity_cache: dict[str, dict[str, str]] = {}

    # Human-readable labels for relationship types used in entity labels
    _RELATIONSHIP_LABELS: dict[str, str] = {
        "PART_OF":     "is part-of",
        "CO_OCCURS":   "co-occurs with",
        "CONTROLS":    "controls",
        "SUPPLIES_TO": "supplies to",
        "CONNECTS_TO": "connects to",
        "REQUIRES":    "requires",
        "TRIGGERS":    "triggers",
        "PRECEDES":    "precedes",
    }

    def _humanize_rel(self, rel_type: str) -> str:
        """Convert a raw relationship type key to a readable verb phrase."""
        base = rel_type.removeprefix("inverse_")
        return self._RELATIONSHIP_LABELS.get(base, base.lower().replace("_", " "))

    def _build_entity_label(
        self, entity_name: str, rel_type: str, source_entity_name: str
    ) -> str:
        """
        Build a human-readable relationship label for a search result.

        Direct matches:  entity_name
        Outgoing A→B:    "source is-rel entity"   (source -[REL]-> entity)
        Incoming B→A:    "entity is-rel source"   (entity -[REL]-> source, stored as inverse_REL)

        Example: entity="engine rotor", rel="inverse_PART_OF", source="engine"
                 → "engine rotor is part-of engine"
        """
        if not rel_type:
            return entity_name
        human_rel = self._humanize_rel(rel_type)
        if not source_entity_name:
            return f"{entity_name} ({human_rel})"
        if rel_type.startswith("inverse_"):
            # entity -[REL]-> source  →  "entity is-rel source"
            return f"{entity_name} {human_rel} {source_entity_name}"
        else:
            # source -[REL]-> entity  →  "source is-rel entity"
            return f"{source_entity_name} {human_rel} {entity_name}"

    def __new__(cls) -> "GraphDBService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._entity_cache = {}
        return cls._instance

    def _get_db_path(self) -> Path:
        """Get the graph database storage path."""
        dirs = ensure_data_directories()
        return dirs["graph_db"]

    def initialize(self) -> None:
        """Initialize Kùzu with persistent storage."""
        if self._initialized:
            logger.debug("Graph database already initialized")
            return

        db_path = self._get_db_path()
        logger.info(f"Initializing Kùzu at: {db_path}")

        try:
            settings = get_app_settings()
            self._database = kuzu.Database(
                str(db_path),
                buffer_pool_size=settings.kuzu_buffer_pool_size
            )
            self._connection = kuzu.Connection(self._database)
            self._create_schema()
            self._initialized = True
            logger.info("Kùzu graph database initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Kùzu: {e}")
            raise RuntimeError(f"Could not initialize graph database: {e}") from e

    def _create_schema(self) -> None:
        """Create the graph schema if tables don't exist."""
        if self._connection is None:
            raise RuntimeError("Database connection not established")

        logger.info("Checking graph schema...")

        # Check and create each table individually
        # Document node table
        if not self._table_exists("Document"):
            self._safe_execute(
                "CREATE NODE TABLE Document("
                "id STRING, "
                "library_id STRING, "
                "source_file STRING, "
                "page STRING, "
                "chunk_index STRING, "
                "created_at STRING, "
                "PRIMARY KEY (id))"
            )
            logger.info("Created Document node table")

        # Entity node table
        if not self._table_exists("Entity"):
            self._safe_execute(
                "CREATE NODE TABLE Entity("
                "id STRING, "
                "library_id STRING, "
                "name STRING, "
                "entity_type STRING, "
                "PRIMARY KEY (id))"
            )
            logger.info("Created Entity node table")

        # HAS_ENTITY relationship (Document -> Entity)
        # Note: "CONTAINS" is a reserved keyword in Kùzu, so we use "HAS_ENTITY"
        if not self._table_exists("HAS_ENTITY"):
            self._safe_execute(
                "CREATE REL TABLE HAS_ENTITY(FROM Document TO Entity)"
            )
            logger.info("Created HAS_ENTITY relationship table")

        # =====================================================================
        # Document Structure Relationships (Document -> Document)
        # =====================================================================
        if not self._table_exists("NEXT_CHUNK"):
            self._safe_execute(
                "CREATE REL TABLE NEXT_CHUNK(FROM Document TO Document)"
            )
            logger.info("Created NEXT_CHUNK relationship table")

        if not self._table_exists("SAME_PAGE"):
            self._safe_execute(
                "CREATE REL TABLE SAME_PAGE(FROM Document TO Document)"
            )
            logger.info("Created SAME_PAGE relationship table")

        # =====================================================================
        # Entity-to-Entity Relationships (with relation_type property)
        # =====================================================================

        # Semantic co-occurrence
        if not self._table_exists("CO_OCCURS"):
            self._safe_execute(
                "CREATE REL TABLE CO_OCCURS(FROM Entity TO Entity, strength STRING)"
            )
            logger.info("Created CO_OCCURS relationship table")

        # Component relationships
        if not self._table_exists("PART_OF"):
            self._safe_execute(
                "CREATE REL TABLE PART_OF(FROM Entity TO Entity)"
            )
            logger.info("Created PART_OF relationship table")

        if not self._table_exists("CONNECTS_TO"):
            self._safe_execute(
                "CREATE REL TABLE CONNECTS_TO(FROM Entity TO Entity)"
            )
            logger.info("Created CONNECTS_TO relationship table")

        if not self._table_exists("SUPPLIES_TO"):
            self._safe_execute(
                "CREATE REL TABLE SUPPLIES_TO(FROM Entity TO Entity)"
            )
            logger.info("Created SUPPLIES_TO relationship table")

        if not self._table_exists("CONTROLS"):
            self._safe_execute(
                "CREATE REL TABLE CONTROLS(FROM Entity TO Entity)"
            )
            logger.info("Created CONTROLS relationship table")

        # Process relationships
        if not self._table_exists("PRECEDES"):
            self._safe_execute(
                "CREATE REL TABLE PRECEDES(FROM Entity TO Entity)"
            )
            logger.info("Created PRECEDES relationship table")

        if not self._table_exists("TRIGGERS"):
            self._safe_execute(
                "CREATE REL TABLE TRIGGERS(FROM Entity TO Entity)"
            )
            logger.info("Created TRIGGERS relationship table")

        if not self._table_exists("REQUIRES"):
            self._safe_execute(
                "CREATE REL TABLE REQUIRES(FROM Entity TO Entity)"
            )
            logger.info("Created REQUIRES relationship table")

        # Hierarchy relationships
        if not self._table_exists("IS_A"):
            self._safe_execute(
                "CREATE REL TABLE IS_A(FROM Entity TO Entity)"
            )
            logger.info("Created IS_A relationship table")

        if not self._table_exists("HAS_PROPERTY"):
            self._safe_execute(
                "CREATE REL TABLE HAS_PROPERTY(FROM Entity TO Entity)"
            )
            logger.info("Created HAS_PROPERTY relationship table")

        # Generic relationship (legacy + fallback)
        if not self._table_exists("RELATED_TO"):
            self._safe_execute(
                "CREATE REL TABLE RELATED_TO(FROM Entity TO Entity, relation_type STRING)"
            )
            logger.info("Created RELATED_TO relationship table")

    def _table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database."""
        if self._connection is None:
            return False
        try:
            # Try a minimal query against the table
            if table_name in ("Document", "Entity"):
                self._connection.execute(f"MATCH (n:{table_name}) RETURN n LIMIT 1")
            else:
                # For relationship tables, try a pattern match
                if table_name == "HAS_ENTITY":
                    self._connection.execute(
                        "MATCH (d:Document)-[r:HAS_ENTITY]->(e:Entity) RETURN r LIMIT 1"
                    )
                elif table_name == "RELATED_TO":
                    self._connection.execute(
                        "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) RETURN r LIMIT 1"
                    )
            return True
        except Exception:
            return False

    def _reconnect_connection(self) -> None:
        """Close and reopen the Kùzu connection to free exhausted buffer pool memory.

        After long-running LLM extraction phases, the buffer pool can be fully
        occupied, causing 'Failed to claim a frame' errors on every write.
        Checkpointing flushes dirty pages to disk; reopening the connection
        resets the in-memory buffer manager state.
        """
        try:
            if self._connection is not None:
                try:
                    self._connection.execute("ROLLBACK")
                except Exception:
                    pass  # No active transaction — expected
                try:
                    self._connection.execute("CHECKPOINT")
                    logger.info("Database checkpoint completed before reconnect")
                except Exception as e:
                    logger.debug(f"Checkpoint skipped (non-critical): {e}")
            # Explicitly destroy old connection before creating new one.
            # Kùzu allows only one write transaction at a time across all connections.
            # gc.collect() forces immediate GC of C extension reference cycles so the
            # write lock is released before the new connection attempts any writes.
            self._connection = None
            gc.collect()
            self._connection = kuzu.Connection(self._database)
            logger.info("Database connection refreshed — buffer pool reset")
        except Exception as e:
            logger.error(f"Failed to refresh database connection: {e}")
            raise RuntimeError(f"Could not reconnect to graph database: {e}") from e

    def _safe_execute(self, query: str) -> Optional[Any]:
        """Execute a Cypher query with error handling."""
        if self._connection is None:
            return None
        try:
            return self._connection.execute(query)
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.warning(f"Query execution warning: {e}")
            return None

    def _get_canonical_key(self, name: str, library_id: str) -> str:
        """Generate canonical key for entity resolution (O(1) lookup)."""
        normalized = name.lower().strip()
        return f"{library_id}:{normalized}"

    def _escape_string(self, value: str) -> str:
        """Escape string for Cypher query."""
        if value is None:
            return ""
        return str(value).replace("\\", "\\\\").replace("'", "\\'")

    # =========================================================================
    # PHASE 1: Entity Extraction (per chunk)
    # =========================================================================

    def extract_entities(self, text: str) -> list[dict[str, str]]:
        """
        Extract entities from text using regex patterns.

        Phase 1 of the pipeline - pure extraction, no DB writes.
        Respects settings for which entity types to extract.

        Args:
            text: Text to extract entities from.

        Returns:
            List of entity dicts with 'name' and 'type' keys.
        """
        entities = []
        seen = set()

        settings_mgr = get_settings_manager()
        graph_settings = settings_mgr.ai_settings.graph
        max_entities = graph_settings.max_entities_per_chunk

        # Capitalized phrases (proper nouns, names, organizations)
        if graph_settings.extract_proper_nouns:
            caps_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b'
            for match in re.finditer(caps_pattern, text):
                name = match.group(1)
                if name not in seen and len(name) > 2:
                    entities.append({"name": name, "type": "proper_noun"})
                    seen.add(name)
                    if len(entities) >= max_entities:
                        break

        # Email addresses
        if graph_settings.extract_emails and len(entities) < max_entities:
            email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            for match in re.finditer(email_pattern, text):
                email = match.group(0)
                if email not in seen:
                    entities.append({"name": email, "type": "email"})
                    seen.add(email)
                    if len(entities) >= max_entities:
                        break

        # URLs (limited)
        if graph_settings.extract_urls and len(entities) < max_entities:
            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
            for match in re.finditer(url_pattern, text[:2000]):
                url = match.group(0)[:100]  # Truncate long URLs
                if url not in seen:
                    entities.append({"name": url, "type": "url"})
                    seen.add(url)
                    if len(entities) >= max_entities:
                        break

        return entities[:max_entities]

    # =========================================================================
    # PHASE 1b: Relationship Extraction from Text
    # =========================================================================

    def extract_relationships(
        self,
        text: str,
        entities: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """
        Extract relationships between entities from text.

        Uses pattern matching to detect semantic relationships.

        Args:
            text: Text to analyze for relationships.
            entities: List of entities already extracted from this text.

        Returns:
            List of relationship dicts with 'source', 'target', 'type' keys.
        """
        relationships = []
        settings_mgr = get_settings_manager()
        rel_settings = settings_mgr.ai_settings.graph.relationships

        # Get entity names for quick lookup
        entity_names = {e["name"].lower(): e["name"] for e in entities}

        # Extract component relationships
        if rel_settings.component.enabled:
            for rel_type, patterns in COMPONENT_PATTERNS.items():
                if not self._is_relation_enabled(rel_settings, rel_type):
                    continue
                for pattern in patterns:
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        source, target = match.groups()[:2]
                        if source.lower() in entity_names and target.lower() in entity_names:
                            relationships.append({
                                "source": entity_names[source.lower()],
                                "target": entity_names[target.lower()],
                                "type": rel_type
                            })

        # Extract process relationships
        if rel_settings.process.enabled:
            for rel_type, patterns in PROCESS_PATTERNS.items():
                if not self._is_relation_enabled(rel_settings, rel_type):
                    continue
                for pattern in patterns:
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        source, target = match.groups()[:2]
                        if source.lower() in entity_names and target.lower() in entity_names:
                            relationships.append({
                                "source": entity_names[source.lower()],
                                "target": entity_names[target.lower()],
                                "type": rel_type
                            })

        # Extract hierarchy relationships
        if rel_settings.hierarchy.enabled:
            for rel_type, patterns in HIERARCHY_PATTERNS.items():
                if not self._is_relation_enabled(rel_settings, rel_type):
                    continue
                for pattern in patterns:
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        groups = match.groups()
                        if len(groups) >= 2:
                            source, target = groups[:2]
                            if source.lower() in entity_names and target.lower() in entity_names:
                                relationships.append({
                                    "source": entity_names[source.lower()],
                                    "target": entity_names[target.lower()],
                                    "type": rel_type
                                })

        return relationships

    def extract_cooccurrences(
        self,
        text: str,
        entities: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """
        Extract co-occurrence relationships from entities in same context.

        Args:
            text: Text to analyze.
            entities: Entities found in this text.

        Returns:
            List of co-occurrence relationships.
        """
        relationships = []
        settings_mgr = get_settings_manager()
        rel_settings = settings_mgr.ai_settings.graph.relationships

        if not rel_settings.semantic.enabled:
            return relationships

        # Split into sentences for sentence-level co-occurrence
        sentences = re.split(r'[.!?]+', text)

        if rel_settings.semantic.co_occurs_sentence:
            # Find entities that appear in same sentence
            for sentence in sentences:
                sentence_lower = sentence.lower()
                entities_in_sentence = [
                    e for e in entities
                    if e["name"].lower() in sentence_lower
                ]
                # Create pairwise relationships
                for i, e1 in enumerate(entities_in_sentence):
                    for e2 in entities_in_sentence[i+1:]:
                        if e1["name"] != e2["name"]:
                            relationships.append({
                                "source": e1["name"],
                                "target": e2["name"],
                                "type": "CO_OCCURS",
                                "strength": "sentence"
                            })

        if rel_settings.semantic.co_occurs_chunk and len(entities) >= 2:
            # Create chunk-level co-occurrence (weaker)
            for i, e1 in enumerate(entities):
                for e2 in entities[i+1:]:
                    if e1["name"] != e2["name"]:
                        # Check if we already have sentence-level
                        existing = any(
                            r["source"] == e1["name"] and r["target"] == e2["name"]
                            for r in relationships
                        )
                        if not existing:
                            relationships.append({
                                "source": e1["name"],
                                "target": e2["name"],
                                "type": "CO_OCCURS",
                                "strength": "chunk"
                            })

        return relationships

    def _is_relation_enabled(self, rel_settings, rel_type: str) -> bool:
        """Check if a specific relationship type is enabled."""
        mapping = {
            "PART_OF": rel_settings.component.part_of,
            "CONNECTS_TO": rel_settings.component.connects_to,
            "SUPPLIES_TO": rel_settings.component.supplies_to,
            "CONTROLS": rel_settings.component.controls,
            "PRECEDES": rel_settings.process.precedes,
            "TRIGGERS": rel_settings.process.triggers,
            "REQUIRES": rel_settings.process.requires,
            "IS_A": rel_settings.hierarchy.is_a,
            "HAS_PROPERTY": rel_settings.hierarchy.has_property,
            "CO_OCCURS": rel_settings.semantic.co_occurs_sentence or rel_settings.semantic.co_occurs_chunk,
            "RELATED_TO": rel_settings.semantic.related_to,
        }
        return mapping.get(rel_type, False)

    # =========================================================================
    # LLM-BASED EXTRACTION (GPU-accelerated via Ollama)
    # Using structured text format for reliable parsing
    # =========================================================================

    def _get_entity_types(self) -> list[str]:
        """Get list of entity types to extract based on settings."""
        settings_mgr = get_settings_manager()
        graph_settings = settings_mgr.ai_settings.graph

        entity_types = []
        if graph_settings.extract_proper_nouns:
            entity_types.extend([
                "Component", "System", "Part", "Tool", "Material",
                "Person", "Organization", "Location", "Product"
            ])
        if graph_settings.extract_emails:
            entity_types.append("Email")
        if graph_settings.extract_urls:
            entity_types.append("URL")

        # Add generic types
        entity_types.extend(["Procedure", "Requirement", "Concept", "Document"])
        return entity_types

    def _get_relationship_types(self) -> list[str]:
        """Get list of enabled relationship types based on settings."""
        settings_mgr = get_settings_manager()
        rel_settings = settings_mgr.ai_settings.graph.relationships

        relationship_types = []

        # Component relationships
        if rel_settings.component.enabled:
            if rel_settings.component.part_of:
                relationship_types.append("PART_OF")
            if rel_settings.component.connects_to:
                relationship_types.append("CONNECTS_TO")
            if rel_settings.component.supplies_to:
                relationship_types.append("SUPPLIES_TO")
            if rel_settings.component.controls:
                relationship_types.append("CONTROLS")

        # Process relationships
        if rel_settings.process.enabled:
            if rel_settings.process.precedes:
                relationship_types.append("PRECEDES")
            if rel_settings.process.triggers:
                relationship_types.append("TRIGGERS")
            if rel_settings.process.requires:
                relationship_types.append("REQUIRES")

        # Hierarchy relationships
        if rel_settings.hierarchy.enabled:
            if rel_settings.hierarchy.is_a:
                relationship_types.append("IS_A")
            if rel_settings.hierarchy.has_property:
                relationship_types.append("HAS_PROPERTY")

        # Add common relationship types
        relationship_types.extend(["REFERENCES", "AFFECTS", "RELATED_TO"])

        return list(set(relationship_types))  # Remove duplicates

    def extract_graph_llm(self, text: str) -> tuple[list[dict], list[dict]]:
        """
        Extract entities AND relationships from text using LLM in a single call.

        Uses structured text format (not JSON) for reliable parsing:
        ## ENTITIES
        ENTITY_NAME | ENTITY_TYPE | DESCRIPTION
        ## RELATIONSHIPS
        SOURCE -> RELATIONSHIP_TYPE -> TARGET

        Args:
            text: Text to extract from.

        Returns:
            Tuple of (entities, relationships) lists.
        """
        entity_types = self._get_entity_types()
        relationship_types = self._get_relationship_types()

        settings_mgr = get_settings_manager()
        max_entities = settings_mgr.ai_settings.graph.max_entities_per_chunk

        # Build combined prompt for both entities and relationships
        prompt = f"""Extract key entities and their relationships from the text below.

Allowed entity types: {', '.join(entity_types)}
Allowed relationship types: {', '.join(relationship_types)}

Extract at most {max_entities} entities.

Text:
{text[:2500]}

Return the results in this EXACT format:

## ENTITIES
ENTITY_NAME | ENTITY_TYPE | DESCRIPTION
ENTITY_NAME | ENTITY_TYPE | DESCRIPTION

## RELATIONSHIPS
SOURCE_ENTITY -> RELATIONSHIP_TYPE -> TARGET_ENTITY
SOURCE_ENTITY -> RELATIONSHIP_TYPE -> TARGET_ENTITY

Important:
- Use ONLY the allowed entity types and relationship types listed above
- Each entity on its own line with | separating fields
- Each relationship on its own line with -> separating parts
- Do not include any other text or explanations"""

        try:
            ai_client = get_ai_client()
            response = ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a knowledge graph extraction assistant. Extract entities and relationships from text in the exact format requested."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1000
            )

            return self._parse_extraction_response(response, entity_types, relationship_types)

        except (OpenAIRateLimitError, OpenAITimeoutError) as e:
            wait = 120
            logger.warning(f"LLM rate limit / timeout during graph extraction: {e}. Raising RateLimitWait({wait}s)")
            raise RateLimitWait(wait_seconds=wait) from e
        except Exception as e:
            logger.error(f"LLM graph extraction failed: {e}")
            return [], []

    def _parse_extraction_response(
        self,
        response: str,
        allowed_entity_types: list[str],
        allowed_relationship_types: list[str]
    ) -> tuple[list[dict], list[dict]]:
        """
        Parse the structured text response from LLM.

        Args:
            response: Raw LLM response text.
            allowed_entity_types: List of valid entity types.
            allowed_relationship_types: List of valid relationship types.

        Returns:
            Tuple of (entities, relationships) lists.
        """
        entities = []
        relationships = []

        # Normalize allowed types for case-insensitive matching
        allowed_entity_types_lower = {t.lower(): t for t in allowed_entity_types}
        allowed_rel_types_upper = {t.upper() for t in allowed_relationship_types}

        # Split response into entities and relationships sections
        entities_part = ""
        relationships_part = ""

        response_upper = response.upper()
        if "## RELATIONSHIPS" in response_upper:
            idx = response_upper.index("## RELATIONSHIPS")
            entities_part = response[:idx]
            relationships_part = response[idx:]
        elif "## ENTITIES" in response_upper:
            entities_part = response

        # Remove section header from entities part
        if "## ENTITIES" in entities_part.upper():
            idx = entities_part.upper().index("## ENTITIES")
            entities_part = entities_part[idx + len("## ENTITIES"):]

        # Remove section header from relationships part
        if "## RELATIONSHIPS" in relationships_part.upper():
            idx = relationships_part.upper().index("## RELATIONSHIPS")
            relationships_part = relationships_part[idx + len("## RELATIONSHIPS"):]

        # Parse entities
        seen_entities = set()
        for line in entities_part.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line.upper().startswith('ENTITY'):
                continue

            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2:
                    name = parts[0]
                    entity_type = parts[1]
                    description = parts[2] if len(parts) >= 3 else ""

                    # Skip empty names or too short
                    if not name or len(name) < 2:
                        continue

                    # Normalize entity type
                    entity_type_lower = entity_type.lower()
                    if entity_type_lower in allowed_entity_types_lower:
                        entity_type = allowed_entity_types_lower[entity_type_lower]
                    else:
                        entity_type = "Concept"  # Default type

                    # Deduplicate
                    key = name.lower()
                    if key not in seen_entities:
                        seen_entities.add(key)
                        entities.append({
                            "name": name,
                            "type": entity_type,
                            "description": description
                        })

        # Build entity name set for relationship validation
        entity_names = {e["name"].lower() for e in entities}

        # Parse relationships
        for line in relationships_part.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line.upper().startswith('SOURCE'):
                continue

            if '->' in line:
                parts = [p.strip() for p in line.split('->')]
                if len(parts) >= 3:
                    source = parts[0]
                    rel_type = parts[1].upper().replace(' ', '_').replace('-', '_')
                    target = parts[2]

                    # Validate entities exist
                    if source.lower() not in entity_names or target.lower() not in entity_names:
                        continue

                    # Validate relationship type (or map to RELATED_TO)
                    if rel_type not in allowed_rel_types_upper:
                        rel_type = "RELATED_TO"

                    # Skip self-references
                    if source.lower() == target.lower():
                        continue

                    relationships.append({
                        "source": source,
                        "type": rel_type,
                        "target": target
                    })

        logger.info(f"LLM extracted {len(entities)} entities, {len(relationships)} relationships")
        return entities, relationships

    def _extract_with_llm_tracking(self, text: str) -> tuple[list[dict], list[dict], bool]:
        """
        Extract graph using LLM with tracking of success.

        Returns:
            Tuple of (entities, relationships, llm_used).
            llm_used is False if extraction failed completely.
        """
        try:
            entities, relationships = self.extract_graph_llm(text)
            if entities or relationships:
                return entities, relationships, True
            else:
                # LLM returned empty - fall back to regex
                logger.warning("LLM returned empty results, using regex fallback")
                entities = self.extract_entities(text)
                relationships = self.extract_relationships(text, entities)
                return entities, relationships, False
        except RateLimitWait:
            raise  # propagate to asyncio.to_thread caller for SSE wait-and-retry
        except Exception as e:
            logger.error(f"LLM extraction error: {e}, using regex fallback")
            entities = self.extract_entities(text)
            relationships = self.extract_relationships(text, entities)
            return entities, relationships, False

    # =========================================================================
    # PHASE 2: Document-level Aggregation and Batch Ingestion
    # =========================================================================

    def ingest_document(
        self,
        library_id: str,
        source_file: str,
        chunks: list[dict]
    ) -> tuple[int, int]:
        """
        Ingest an entire document with batch operations.

        Document-level aggregation - collects all entities first,
        then performs batch inserts.

        Args:
            library_id: The library this document belongs to.
            source_file: Source filename.
            chunks: List of chunk dicts with keys:
                    - chunk_id: Unique identifier
                    - page: Page number (or None)
                    - chunk_index: Index in document
                    - text: Text content for entity extraction

        Returns:
            Tuple of (nodes_created, relationships_created).
        """
        if not self._initialized or self._connection is None:
            raise RuntimeError("Graph database not initialized")

        nodes_created = 0
        relationships_created = 0
        created_at = datetime.utcnow().isoformat()

        # Initialize entity cache for this library if needed
        if library_id not in self._entity_cache:
            self._entity_cache[library_id] = {}
            self._load_entity_cache(library_id)

        # Get relationship settings
        settings_mgr = get_settings_manager()
        rel_settings = settings_mgr.ai_settings.graph.relationships

        # Collect all document-chunk data, entities, and relationships
        document_nodes = []
        all_chunk_entities: dict[str, list[dict]] = {}  # chunk_id -> entities
        all_chunk_relationships: dict[str, list[dict]] = {}  # chunk_id -> relationships
        total_entities_found = 0
        total_relationships_found = 0

        # Check extraction method setting
        graph_settings = settings_mgr.ai_settings.graph
        use_llm = graph_settings.extraction_method == "llm"

        # Debug log to show actual setting value
        logger.info(f"Graph extraction_method setting: '{graph_settings.extraction_method}' (use_llm={use_llm})")

        if use_llm:
            logger.info(f"Using LLM-based extraction (GPU) for '{source_file}' ({len(chunks)} chunks)")
        else:
            logger.info(f"Using regex-based extraction for '{source_file}'")

        # Track LLM usage for diagnostics
        llm_success_count = 0
        llm_fallback_count = 0

        if use_llm:
            # LLM extraction remains sequential — rate-limited API calls cannot be parallelised
            for i, chunk in enumerate(chunks):
                chunk_id = chunk.get("chunk_id", f"chunk_{uuid.uuid4().hex[:8]}")
                page = chunk.get("page")
                chunk_index = chunk.get("chunk_index", 0)
                text = chunk.get("text", "")

                document_nodes.append({
                    "chunk_id": chunk_id,
                    "page": str(page) if page else "",
                    "chunk_index": str(chunk_index),
                    "text": text,
                })

                entities, relationships, llm_used = self._extract_with_llm_tracking(text)
                if llm_used:
                    llm_success_count += 1
                else:
                    llm_fallback_count += 1

                cooccurrences = self.extract_cooccurrences(text, entities)
                relationships.extend(cooccurrences)

                all_chunk_entities[chunk_id] = entities
                all_chunk_relationships[chunk_id] = relationships
                total_entities_found += len(entities)
                total_relationships_found += len(relationships)

                if (i + 1) % 5 == 0:
                    logger.info(f"LLM extraction progress: {i + 1}/{len(chunks)} chunks processed")
        else:
            # Regex extraction is stateless and thread-safe — run all chunks in parallel.
            # ThreadPoolExecutor releases the GIL between regex operations so multiple
            # chunks are processed simultaneously on multi-core machines.
            _num_workers = min(8, max(1, os.cpu_count() or 4))

            def _extract_regex_chunk(chunk: dict) -> tuple:
                cid = chunk.get("chunk_id", f"chunk_{uuid.uuid4().hex[:8]}")
                page = chunk.get("page")
                idx = chunk.get("chunk_index", 0)
                text = chunk.get("text", "")
                entities = self.extract_entities(text)
                relationships = self.extract_relationships(text, entities)
                cooccurrences = self.extract_cooccurrences(text, entities)
                relationships.extend(cooccurrences)
                return cid, page, idx, text, entities, relationships

            with concurrent.futures.ThreadPoolExecutor(max_workers=_num_workers) as _pool:
                _extracted = list(_pool.map(_extract_regex_chunk, chunks))

            for cid, page, idx, text, entities, relationships in _extracted:
                document_nodes.append({
                    "chunk_id": cid,
                    "page": str(page) if page else "",
                    "chunk_index": str(idx),
                    "text": text,
                })
                all_chunk_entities[cid] = entities
                all_chunk_relationships[cid] = relationships
                total_entities_found += len(entities)
                total_relationships_found += len(relationships)

        # Log LLM usage summary
        if use_llm:
            logger.info(
                f"LLM extraction summary: {llm_success_count} chunks via LLM, {llm_fallback_count} via regex fallback"
            )

        logger.info(
            f"Graph extraction for '{source_file}': "
            f"{len(chunks)} chunks, {total_entities_found} entity mentions, "
            f"{total_relationships_found} relationships detected"
        )

        # Refresh the connection before any writes.
        # LLM extraction can run for hours, exhausting the Kùzu buffer pool and
        # causing 'Failed to claim a frame' on every subsequent CREATE.
        # Checkpoint + reconnect flushes dirty pages and resets the buffer manager.
        logger.info("Refreshing database connection before batch write (frees buffer pool)...")
        self._reconnect_connection()

        safe_library = self._escape_string(library_id)
        safe_source = self._escape_string(source_file)

        # ── Phase 2a: Document nodes — one transaction ────────────────────────
        # Kùzu 0.3.2 COPY FROM is restricted to the initial load of a pristine
        # table and is unsupported for relationship tables entirely, so we use
        # explicit BEGIN/COMMIT transactions throughout.
        try:
            self._connection.execute("BEGIN TRANSACTION")
            for doc in document_nodes:
                safe_cid = self._escape_string(doc["chunk_id"])
                self._connection.execute(
                    f"CREATE (:Document {{id: '{safe_cid}', library_id: '{safe_library}', "
                    f"source_file: '{safe_source}', page: '{doc['page']}', "
                    f"chunk_index: '{doc['chunk_index']}', created_at: '{created_at}'}})"
                )
                nodes_created += 1
            self._connection.execute("COMMIT")
            logger.info(f"[tx] {len(document_nodes)} Document nodes committed")
        except Exception as e:
            logger.error(f"Document nodes transaction failed: {e}")
            try:
                self._connection.execute("ROLLBACK")
            except Exception:
                pass

        # ── Phase 2b: Normalize entities (in memory) ─────────────────────────
        unique_entities: dict[str, dict] = {}  # canonical_key -> entity info
        for chunk_id, entities in all_chunk_entities.items():
            for entity in entities:
                canonical_key = self._get_canonical_key(entity["name"], library_id)
                if canonical_key not in unique_entities:
                    unique_entities[canonical_key] = {
                        "name": entity["name"],
                        "type": entity["type"],
                        "chunk_ids": [],
                    }
                unique_entities[canonical_key]["chunk_ids"].append(chunk_id)

        # Pre-assign IDs for new entities (cache miss = never seen before)
        new_entities: list[tuple[str, str, dict]] = []
        for canonical_key, entity_info in unique_entities.items():
            if canonical_key not in self._entity_cache[library_id]:
                new_entities.append(
                    (canonical_key, f"entity_{uuid.uuid4().hex[:8]}", entity_info)
                )

        # ── Phase 2c: Entity nodes — one transaction (new only) ──────────────
        if new_entities:
            try:
                self._connection.execute("BEGIN TRANSACTION")
                for canonical_key, eid, entity_info in new_entities:
                    safe_name = self._escape_string(entity_info["name"])
                    safe_type = self._escape_string(entity_info["type"])
                    self._connection.execute(
                        f"CREATE (:Entity {{id: '{eid}', library_id: '{safe_library}', "
                        f"name: '{safe_name}', entity_type: '{safe_type}'}})"
                    )
                self._connection.execute("COMMIT")
                # Update in-memory cache only after successful commit
                for canonical_key, new_entity_id, _ in new_entities:
                    self._entity_cache[library_id][canonical_key] = new_entity_id
                nodes_created += len(new_entities)
                logger.info(f"[tx] {len(new_entities)} Entity nodes committed")
            except Exception as e:
                logger.error(f"Entity nodes transaction failed: {e}")
                try:
                    self._connection.execute("ROLLBACK")
                except Exception:
                    pass

        # ── Phase 3: HAS_ENTITY relationships — one transaction ───────────────
        has_entity_pairs = [
            (chunk_id, self._entity_cache[library_id][canonical_key])
            for canonical_key, entity_info in unique_entities.items()
            if canonical_key in self._entity_cache[library_id]
            for chunk_id in entity_info["chunk_ids"]
        ]
        if has_entity_pairs:
            try:
                self._connection.execute("BEGIN TRANSACTION")
                for doc_id, entity_id in has_entity_pairs:
                    safe_did = self._escape_string(doc_id)
                    safe_eid = self._escape_string(entity_id)
                    self._connection.execute(
                        f"MATCH (d:Document {{id: '{safe_did}'}}), (e:Entity {{id: '{safe_eid}'}}) "
                        f"CREATE (d)-[:HAS_ENTITY]->(e)"
                    )
                    relationships_created += 1
                self._connection.execute("COMMIT")
                logger.info(f"[tx] {len(has_entity_pairs)} HAS_ENTITY relationships committed")
            except Exception as e:
                logger.error(f"HAS_ENTITY transaction failed: {e}")
                try:
                    self._connection.execute("ROLLBACK")
                except Exception:
                    pass

        # ── Phase 4: Document structure relationships (NEXT_CHUNK, SAME_PAGE) ─
        if rel_settings.document_structure.enabled:
            relationships_created += self._create_document_structure_relationships(
                document_nodes, rel_settings
            )

        # ── Phase 5: Entity-to-entity relationships ───────────────────────────
        relationships_created += self._create_entity_relationships(
            library_id, all_chunk_relationships, rel_settings
        )

        logger.info(
            f"Graph batch for '{source_file}': "
            f"{nodes_created} nodes ({len(unique_entities)} unique entities), "
            f"{relationships_created} relationships"
        )
        return nodes_created, relationships_created

    def _create_document_structure_relationships(
        self,
        document_nodes: list[dict],
        rel_settings
    ) -> int:
        """Create NEXT_CHUNK and SAME_PAGE relationships in single transactions."""
        if self._connection is None:
            return 0

        relationships_created = 0
        sorted_nodes = sorted(document_nodes, key=lambda x: int(x.get("chunk_index", 0) or 0))

        # ── NEXT_CHUNK — one transaction ──────────────────────────────────────
        if rel_settings.document_structure.next_chunk and len(sorted_nodes) > 1:
            next_chunk_queries = [
                (
                    f"MATCH (a:Document {{id: '{self._escape_string(sorted_nodes[i]['chunk_id'])}'}}), "
                    f"(b:Document {{id: '{self._escape_string(sorted_nodes[i+1]['chunk_id'])}'}}) "
                    f"CREATE (a)-[:NEXT_CHUNK]->(b)"
                )
                for i in range(len(sorted_nodes) - 1)
            ]
            try:
                self._connection.execute("BEGIN TRANSACTION")
                for q in next_chunk_queries:
                    self._connection.execute(q)
                    relationships_created += 1
                self._connection.execute("COMMIT")
                logger.info(f"[tx] {len(next_chunk_queries)} NEXT_CHUNK relationships committed")
            except Exception as e:
                logger.error(f"NEXT_CHUNK transaction failed: {e}")
                try:
                    self._connection.execute("ROLLBACK")
                except Exception:
                    pass

        # ── SAME_PAGE — one transaction ───────────────────────────────────────
        if rel_settings.document_structure.same_page:
            page_groups: dict[str, list[dict]] = {}
            for node in document_nodes:
                page = node.get("page", "")
                if page:
                    page_groups.setdefault(page, []).append(node)

            same_page_queries = [
                (
                    f"MATCH (a:Document {{id: '{self._escape_string(n1['chunk_id'])}'}}), "
                    f"(b:Document {{id: '{self._escape_string(n2['chunk_id'])}'}}) "
                    f"CREATE (a)-[:SAME_PAGE]->(b)"
                )
                for nodes in page_groups.values()
                if len(nodes) > 1
                for i, n1 in enumerate(nodes)
                for n2 in nodes[i + 1:]
            ]
            if same_page_queries:
                try:
                    self._connection.execute("BEGIN TRANSACTION")
                    for q in same_page_queries:
                        self._connection.execute(q)
                        relationships_created += 1
                    self._connection.execute("COMMIT")
                    logger.info(f"[tx] {len(same_page_queries)} SAME_PAGE relationships committed")
                except Exception as e:
                    logger.error(f"SAME_PAGE transaction failed: {e}")
                    try:
                        self._connection.execute("ROLLBACK")
                    except Exception:
                        pass

        return relationships_created

    def _create_entity_relationships(
        self,
        library_id: str,
        all_chunk_relationships: dict[str, list[dict]],
        rel_settings
    ) -> int:
        """Create entity-to-entity relationships in transactions grouped by type."""
        if self._connection is None:
            return 0

        relationships_created = 0
        seen_rels: set[str] = set()

        # Collect and deduplicate all relationships in memory first
        all_rels: list[dict] = []
        for relationships in all_chunk_relationships.values():
            for rel in relationships:
                source_name = rel["source"]
                target_name = rel["target"]
                rel_type = rel["type"]

                rel_key = f"{source_name.lower()}|{target_name.lower()}|{rel_type}"
                if rel_key in seen_rels:
                    continue
                seen_rels.add(rel_key)

                source_id = self._entity_cache.get(library_id, {}).get(
                    self._get_canonical_key(source_name, library_id)
                )
                target_id = self._entity_cache.get(library_id, {}).get(
                    self._get_canonical_key(target_name, library_id)
                )
                if not source_id or not target_id:
                    continue

                all_rels.append({
                    "source_id": source_id,
                    "target_id": target_id,
                    "type": rel_type,
                    "strength": rel.get("strength", "chunk") if rel_type == "CO_OCCURS" else None,
                })

        if not all_rels:
            return 0

        logger.info(f"Writing {len(all_rels)} entity-to-entity relationships...")

        # Group by relationship type — each type uses its own transaction so a
        # failure in one type doesn't roll back the others.
        by_type: dict[str, list[dict]] = {}
        for rel in all_rels:
            by_type.setdefault(rel["type"], []).append(rel)

        for rel_type, rels in by_type.items():
            try:
                self._connection.execute("BEGIN TRANSACTION")
                for rel in rels:
                    safe_src = self._escape_string(rel["source_id"])
                    safe_tgt = self._escape_string(rel["target_id"])
                    if rel_type == "CO_OCCURS":
                        safe_str = self._escape_string(rel["strength"] or "chunk")
                        query = (
                            f"MATCH (a:Entity {{id: '{safe_src}'}}), "
                            f"(b:Entity {{id: '{safe_tgt}'}}) "
                            f"CREATE (a)-[:CO_OCCURS {{strength: '{safe_str}'}}]->(b)"
                        )
                    else:
                        query = (
                            f"MATCH (a:Entity {{id: '{safe_src}'}}), "
                            f"(b:Entity {{id: '{safe_tgt}'}}) "
                            f"CREATE (a)-[:{rel_type}]->(b)"
                        )
                    self._connection.execute(query)
                    relationships_created += 1
                self._connection.execute("COMMIT")
                logger.info(f"[tx] {len(rels)} {rel_type} relationships committed")
            except Exception as e:
                logger.error(f"{rel_type} relationship transaction failed: {e}")
                try:
                    self._connection.execute("ROLLBACK")
                except Exception:
                    pass

        return relationships_created

    def _load_entity_cache(self, library_id: str) -> None:
        """Load existing entities into cache for O(1) resolution."""
        if self._connection is None:
            return

        try:
            safe_library = self._escape_string(library_id)
            query = f"MATCH (e:Entity) WHERE e.library_id = '{safe_library}' RETURN e.id, e.name"
            result = self._connection.execute(query)
            while result.has_next():
                row = result.get_next()
                entity_id, name = row[0], row[1]
                canonical_key = self._get_canonical_key(name, library_id)
                self._entity_cache[library_id][canonical_key] = entity_id

            logger.debug(
                f"Loaded {len(self._entity_cache[library_id])} entities "
                f"into cache for library {library_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to load entity cache: {e}")

    # =========================================================================
    # Legacy single-chunk method (for backwards compatibility)
    # =========================================================================

    def add_chunk(
        self,
        library_id: str,
        chunk_id: str,
        source_file: str,
        page: Optional[int],
        chunk_index: int,
        text_for_entities: str
    ) -> tuple[int, int]:
        """Add a single document chunk (legacy method)."""
        chunks = [{
            "chunk_id": chunk_id,
            "page": page,
            "chunk_index": chunk_index,
            "text": text_for_entities
        }]
        return self.ingest_document(library_id, source_file, chunks)

    # =========================================================================
    # Search Operations - Two-step approach for Kùzu compatibility
    # =========================================================================

    def search_by_entity(
        self,
        library_id: str,
        query: str,
        top_k: int = 10
    ) -> list[dict[str, Any]]:
        """
        Search chunks by entity name match using multi-step query approach.

        Step 1: Find entities matching the search keywords
        Step 2: Expand to related entities via typed relationships
        Step 3: Find documents connected to all relevant entities

        Args:
            library_id: The library to search in.
            query: Search query.
            top_k: Maximum results.

        Returns:
            List of matching chunks with metadata and relationship info.
        """
        if not self._initialized or self._connection is None:
            raise RuntimeError("Graph database not initialized")

        results = []
        safe_library = self._escape_string(library_id)

        # Extract keywords from query for entity matching
        keywords = self._extract_search_keywords(query)
        if not keywords:
            return results

        try:
            # Step 1: Find entities matching any keyword
            matching_entities = []
            for keyword in keywords[:8]:  # Limit to top 8 keywords (technical IDs take priority)
                safe_keyword = self._escape_string(keyword.lower())
                entity_query = (
                    f"MATCH (e:Entity) "
                    f"WHERE e.library_id = '{safe_library}' "
                    f"AND lower(e.name) CONTAINS '{safe_keyword}' "
                    f"RETURN e.id, e.name LIMIT 10"
                )
                result = self._connection.execute(entity_query)
                while result.has_next():
                    row = result.get_next()
                    matching_entities.append({
                        "id": row[0],
                        "name": row[1],
                        "direct_match": True
                    })

            # Step 2: Expand to related entities via typed relationships
            expanded_entities = list(matching_entities)
            if matching_entities:
                id_to_name = {e["id"]: e["name"] for e in matching_entities}
                related = self._find_related_entities(
                    [e["id"] for e in matching_entities],
                    safe_library,
                    id_to_name,
                )
                for rel_entity in related:
                    if not any(e["id"] == rel_entity["id"] for e in expanded_entities):
                        expanded_entities.append(rel_entity)
                logger.debug(
                    f"Entity expansion: {len(matching_entities)} direct -> "
                    f"{len(expanded_entities)} total"
                )

            if not expanded_entities:
                logger.debug(f"No entities found for keywords: {keywords}")
                return results

            # Step 3: For each entity, find connected documents
            seen_chunks = set()
            for entity in expanded_entities[:30]:  # Limit entities to check
                entity_id = entity["id"]
                entity_name = entity["name"]
                is_direct = entity.get("direct_match", False)
                rel_type = entity.get("relationship_type", "")

                # Query the HAS_ENTITY relationship table
                doc_query = (
                    f"MATCH (d:Document)-[:HAS_ENTITY]->(e:Entity) "
                    f"WHERE d.library_id = '{safe_library}' "
                    f"AND e.id = '{entity_id}' "
                    f"RETURN DISTINCT d.id, d.source_file, d.page, d.chunk_index "
                    f"LIMIT {top_k}"
                )

                try:
                    result = self._connection.execute(doc_query)
                    while result.has_next():
                        row = result.get_next()
                        chunk_id = row[0]

                        # Score based on match type
                        base_score = 0.9 if is_direct else 0.75

                        if chunk_id in seen_chunks:
                            # Add entity to existing result and boost score
                            for r in results:
                                if r["chunk_id"] == chunk_id:
                                    entity_label = self._build_entity_label(
                                        entity_name, rel_type,
                                        entity.get("source_entity_name", "")
                                    )
                                    if entity_label not in r["related_entities"]:
                                        r["related_entities"].append(entity_label)
                                    r["score"] = min(1.0, r["score"] + 0.05)
                                    break
                        else:
                            seen_chunks.add(chunk_id)
                            entity_label = self._build_entity_label(
                                entity_name, rel_type,
                                entity.get("source_entity_name", "")
                            )
                            results.append({
                                "chunk_id": chunk_id,
                                "source_file": row[1],
                                "page": row[2] if row[2] else None,
                                "chunk_index": row[3],
                                "related_entities": [entity_label],
                                "score": base_score,
                                "source": "graph"
                            })

                        if len(results) >= top_k:
                            break
                except Exception as e:
                    # If EXISTS subquery doesn't work, try alternative approach
                    logger.debug(f"EXISTS query failed, trying alternative: {e}")
                    self._search_docs_for_entity_fallback(
                        entity_id, entity_name, safe_library,
                        results, seen_chunks, top_k
                    )

                if len(results) >= top_k:
                    break

            # Sort by score
            results.sort(key=lambda x: x["score"], reverse=True)

        except Exception as e:
            logger.error(f"Graph search failed: {e}")

        return results[:top_k]

    def _find_related_entities(
        self,
        entity_ids: list[str],
        safe_library: str,
        entity_id_to_name: dict[str, str] | None = None,
    ) -> list[dict]:
        """
        Find entities related to the given entities via typed relationships.

        This expands the search to include entities connected through:
        - CO_OCCURS, CONTROLS, SUPPLIES_TO, CONNECTS_TO, etc.
        """
        if not self._connection:
            return []

        related = []
        relationship_types = [
            "CO_OCCURS", "CONTROLS", "SUPPLIES_TO", "CONNECTS_TO",
            "PART_OF", "REQUIRES", "TRIGGERS", "PRECEDES"
        ]

        for entity_id in entity_ids[:10]:  # Limit expansion
            for rel_type in relationship_types:
                try:
                    # Outgoing relationships
                    query = (
                        f"MATCH (a:Entity {{id: '{entity_id}'}})-[:{rel_type}]->(b:Entity) "
                        f"WHERE b.library_id = '{safe_library}' "
                        f"RETURN b.id, b.name LIMIT 5"
                    )
                    result = self._connection.execute(query)
                    while result.has_next():
                        row = result.get_next()
                        related.append({
                            "id": row[0],
                            "name": row[1],
                            "direct_match": False,
                            "relationship_type": rel_type,
                            "source_entity_name": (entity_id_to_name or {}).get(entity_id, ""),
                        })

                    # Incoming relationships
                    query = (
                        f"MATCH (a:Entity)-[:{rel_type}]->(b:Entity {{id: '{entity_id}'}}) "
                        f"WHERE a.library_id = '{safe_library}' "
                        f"RETURN a.id, a.name LIMIT 5"
                    )
                    result = self._connection.execute(query)
                    while result.has_next():
                        row = result.get_next()
                        related.append({
                            "id": row[0],
                            "name": row[1],
                            "direct_match": False,
                            "relationship_type": f"inverse_{rel_type}",
                            "source_entity_name": (entity_id_to_name or {}).get(entity_id, ""),
                        })
                except Exception:
                    pass  # Skip failed relationship queries

        return related

    def _search_docs_for_entity_fallback(
        self,
        entity_id: str,
        entity_name: str,
        safe_library: str,
        results: list,
        seen_chunks: set,
        top_k: int
    ) -> None:
        """Fallback search using simple queries when complex patterns fail."""
        if self._connection is None:
            return

        try:
            # Get all documents in this library
            doc_query = (
                f"MATCH (d:Document) "
                f"WHERE d.library_id = '{safe_library}' "
                f"RETURN d.id, d.source_file, d.page, d.chunk_index "
                f"LIMIT 100"
            )
            doc_result = self._connection.execute(doc_query)
            doc_list = []
            while doc_result.has_next():
                doc_list.append(doc_result.get_next())

            # For each document, check if entity exists in cache
            # This is a workaround when relationship queries fail
            for doc in doc_list:
                chunk_id = doc[0]
                if chunk_id not in seen_chunks and len(results) < top_k:
                    # Check relationship via simpler query
                    try:
                        # Try to get entities for this document
                        rel_check = (
                            f"MATCH (d:Document {{id: '{chunk_id}'}})-[:HAS_ENTITY]->(e:Entity {{id: '{entity_id}'}}) "
                            f"RETURN count(*)"
                        )
                        check_result = self._connection.execute(rel_check)
                        if check_result.has_next():
                            count = check_result.get_next()[0]
                            if count > 0:
                                seen_chunks.add(chunk_id)
                                results.append({
                                    "chunk_id": chunk_id,
                                    "source_file": doc[1],
                                    "page": doc[2] if doc[2] else None,
                                    "chunk_index": doc[3],
                                    "related_entities": [entity_name],
                                    "score": 0.8,
                                    "source": "graph"
                                })
                    except Exception:
                        pass  # Skip this document if query fails

        except Exception as e:
            logger.debug(f"Fallback search also failed: {e}")

    def _extract_search_keywords(self, query: str) -> list[str]:
        """Extract meaningful keywords from search query."""
        stop_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'must', 'shall',
            'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'in',
            'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
            'through', 'during', 'before', 'after', 'above', 'below',
            'between', 'under', 'again', 'further', 'then', 'once',
            'here', 'there', 'when', 'where', 'why', 'how', 'all',
            'each', 'few', 'more', 'most', 'other', 'some', 'such',
            'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
            'too', 'very', 'just', 'and', 'but', 'if', 'or', 'because',
            'until', 'while', 'although', 'this', 'that', 'these',
            'those', 'what', 'which', 'who', 'whom', 'whose', 'i',
            'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her',
            'us', 'them', 'my', 'your', 'his', 'its', 'our', 'their',
            'procedure', 'process', 'method', 'way', 'step', 'steps',
            'about', 'work', 'working', 'person', 'thing', 'things'
        }

        # Capture technical identifiers FIRST (before lowercasing strips structure).
        # The pure-alpha regex below misses model codes and part numbers which are
        # the most specific and valuable search terms for technical documents:
        #   D-260   → letter(s) + hyphen + alphanumeric
        #   S3A     → letters mixed with digits
        #   719-73-08 → digit groups separated by hyphens
        technical = re.findall(
            r'\b[A-Za-z]{1,4}[-–][0-9A-Za-z][-0-9A-Za-z]*\b'  # D-260, S3A-EN
            r'|\b[0-9]+(?:[-–][0-9]+)+\b'                       # 719-73-08
            r'|\b[A-Za-z]+[0-9]+[A-Za-z0-9]*\b'                # S3A, D260S
            r'|\b[0-9]+[A-Za-z][A-Za-z0-9]*\b',                # 260S, 3A
            query
        )
        keywords = [t.lower() for t in technical]

        # Tokenize and filter plain words
        words = re.findall(r'\b[a-zA-Z]{3,}\b', query.lower())
        keywords += [w for w in words if w not in stop_words]

        # Also look for capitalized words (potential proper nouns)
        caps = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', query)
        keywords.extend([c.lower() for c in caps])

        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for k in keywords:
            if k not in seen:
                seen.add(k)
                unique_keywords.append(k)

        return unique_keywords

    def get_entity_info(self, library_id: str, entity_name: str) -> dict[str, str] | None:
        """Return {name, entity_type} for the first entity matching entity_name (case-insensitive). Returns None if not found."""
        if not self._initialized or self._connection is None:
            return None
        safe_library = self._escape_string(library_id)
        safe_name = self._escape_string(entity_name.lower())
        query = (
            f"MATCH (e:Entity) "
            f"WHERE e.library_id = '{safe_library}' "
            f"AND lower(e.name) CONTAINS '{safe_name}' "
            f"RETURN e.name, e.entity_type LIMIT 1"
        )
        try:
            result = self._connection.execute(query)
            if result.has_next():
                row = result.get_next()
                return {"name": row[0], "entity_type": row[1]}
        except Exception as e:
            logger.warning(f"get_entity_info failed: {e}")
        return None

    def get_entities_for_query(self, library_id: str, query: str, top_k: int = 20) -> list[dict[str, str]]:
        """Return deduplicated [{name, entity_type}] for entities matching keywords in query."""
        if not self._initialized or self._connection is None:
            return []
        safe_library = self._escape_string(library_id)
        keywords = self._extract_search_keywords(query)
        seen: set[str] = set()
        results: list[dict[str, str]] = []
        for kw in keywords[:8]:
            if len(results) >= top_k:
                break
            safe_kw = self._escape_string(kw.lower())
            entity_query = (
                f"MATCH (e:Entity) "
                f"WHERE e.library_id = '{safe_library}' "
                f"AND lower(e.name) CONTAINS '{safe_kw}' "
                f"RETURN e.name, e.entity_type LIMIT 10"
            )
            try:
                result = self._connection.execute(entity_query)
                while result.has_next() and len(results) < top_k:
                    row = result.get_next()
                    lower_name = row[0].lower()
                    if lower_name not in seen:
                        seen.add(lower_name)
                        results.append({"name": row[0], "entity_type": row[1]})
            except Exception as e:
                logger.warning(f"get_entities_for_query keyword '{kw}' failed: {e}")
        return results

    def get_related_entities(
        self,
        library_id: str,
        entity_name: str,
        max_depth: int = 2
    ) -> list[dict[str, Any]]:
        """Find entities directly related to entity_name via typed relationships."""
        if not self._initialized or self._connection is None:
            return []

        safe_name = self._escape_string(entity_name.lower())
        safe_library = self._escape_string(library_id)

        try:
            # Find entity id
            entity_query = (
                f"MATCH (e:Entity) "
                f"WHERE e.library_id = '{safe_library}' "
                f"AND lower(e.name) CONTAINS '{safe_name}' "
                f"RETURN e.id LIMIT 1"
            )
            entity_result = self._connection.execute(entity_query)
            if not entity_result.has_next():
                return []

            entity_id = entity_result.get_next()[0]
            id_to_name = {entity_id: entity_name}

            # Step 1: Traverse typed relationship edges (PART_OF, CONTROLS, CO_OCCURS…)
            related_raw = self._find_related_entities([entity_id], safe_library, id_to_name)

            results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for item in related_raw:
                target_id = item["id"]
                if target_id in seen_ids:
                    continue
                seen_ids.add(target_id)
                target_name = item["name"]
                rel_type = item["relationship_type"]
                target_type = "Unknown"
                try:
                    safe_target_id = self._escape_string(target_id)
                    type_result = self._connection.execute(
                        f"MATCH (e:Entity) WHERE e.id = '{safe_target_id}' RETURN e.entity_type LIMIT 1"
                    )
                    if type_result.has_next():
                        target_type = type_result.get_next()[0]
                except Exception:
                    pass
                results.append({
                    "target_name": target_name,
                    "target_type": target_type,
                    "relationship_type": rel_type,
                })

            # Step 2: Chunk co-occurrence fallback — entities sharing the same document
            # chunks via HAS_ENTITY. This works even when no explicit typed edges exist,
            # which is typical for regex-extracted documents in technical/structured text.
            TYPED_EDGE_THRESHOLD = 5  # supplement whenever typed edges are sparse
            if len(results) < TYPED_EDGE_THRESHOLD:
                safe_entity_id = self._escape_string(entity_id)
                fallback_query = (
                    f"MATCH (d:Document)-[:HAS_ENTITY]->(focal:Entity) "
                    f"WHERE focal.id = '{safe_entity_id}' "
                    f"MATCH (d)-[:HAS_ENTITY]->(other:Entity) "
                    f"WHERE other.id <> '{safe_entity_id}' "
                    f"AND other.library_id = '{safe_library}' "
                    f"RETURN DISTINCT other.id, other.name, other.entity_type "
                    f"LIMIT 20"
                )
                try:
                    fallback_result = self._connection.execute(fallback_query)
                    while fallback_result.has_next():
                        row = fallback_result.get_next()
                        fb_id, fb_name, fb_type = row[0], row[1], row[2]
                        if fb_id not in seen_ids:
                            seen_ids.add(fb_id)
                            results.append({
                                "target_name": fb_name,
                                "target_type": fb_type,
                                "relationship_type": "CO_OCCURS",
                            })
                except Exception as e:
                    logger.debug(f"Chunk co-occurrence fallback failed: {e}")

            return results

        except Exception as e:
            logger.warning(f"Related entities search failed: {e}")
            return []

    # =========================================================================
    # Source Details Operations
    # =========================================================================

    def get_source_stats(
        self,
        library_id: str,
        source_file: str
    ) -> dict[str, Any]:
        """
        Get statistics for a specific source file.

        Returns:
            Dict with chunk_count, entity_count, entities list
        """
        if not self._initialized or self._connection is None:
            return {"error": "Graph database not initialized"}

        safe_library = self._escape_string(library_id)
        safe_source = self._escape_string(source_file)

        stats = {
            "source_file": source_file,
            "chunk_count": 0,
            "entity_count": 0,
            "entities": [],
            "chunks": []
        }

        try:
            # Get document chunks for this source
            chunk_query = (
                f"MATCH (d:Document) "
                f"WHERE d.library_id = '{safe_library}' "
                f"AND d.source_file = '{safe_source}' "
                f"RETURN d.id, d.page, d.chunk_index "
                f"ORDER BY d.chunk_index"
            )
            result = self._connection.execute(chunk_query)
            chunk_ids = []
            while result.has_next():
                row = result.get_next()
                chunk_ids.append(row[0])
                stats["chunks"].append({
                    "id": row[0],
                    "page": row[1] if row[1] else None,
                    "chunk_index": row[2]
                })
            stats["chunk_count"] = len(chunk_ids)

            # Get entities connected to these specific document chunks
            if chunk_ids:
                entity_set = set()
                for chunk_id in chunk_ids:
                    safe_chunk_id = self._escape_string(chunk_id)
                    entity_query = (
                        f"MATCH (d:Document {{id: '{safe_chunk_id}'}})-[:HAS_ENTITY]->(e:Entity) "
                        f"RETURN e.name, e.entity_type"
                    )
                    try:
                        result = self._connection.execute(entity_query)
                        while result.has_next():
                            row = result.get_next()
                            entity_key = f"{row[0]}|{row[1]}"
                            if entity_key not in entity_set:
                                entity_set.add(entity_key)
                                stats["entities"].append({
                                    "name": row[0],
                                    "type": row[1]
                                })
                    except Exception as e:
                        logger.debug(f"Entity query failed for chunk {chunk_id}: {e}")

            stats["entity_count"] = len(stats["entities"])

        except Exception as e:
            logger.error(f"Failed to get source stats: {e}")
            stats["error"] = str(e)

        return stats

    # =========================================================================
    # Delete Operations
    # =========================================================================

    def delete_by_source(self, library_id: str, source_file: str) -> int:
        """Delete all chunks and orphaned entities from a source file."""
        if not self._initialized or self._connection is None:
            return 0

        safe_source = self._escape_string(source_file)
        safe_library = self._escape_string(library_id)
        deleted = 0

        try:
            # First, get the document IDs we're about to delete
            doc_query = (
                f"MATCH (d:Document) "
                f"WHERE d.library_id = '{safe_library}' "
                f"AND d.source_file = '{safe_source}' "
                f"RETURN d.id"
            )
            result = self._connection.execute(doc_query)
            doc_ids = []
            while result.has_next():
                doc_ids.append(result.get_next()[0])

            # Get entities connected to these documents before deletion
            entity_ids_to_check = set()
            for doc_id in doc_ids:
                safe_doc_id = self._escape_string(doc_id)
                try:
                    entity_query = (
                        f"MATCH (d:Document {{id: '{safe_doc_id}'}})-[:HAS_ENTITY]->(e:Entity) "
                        f"RETURN e.id"
                    )
                    result = self._connection.execute(entity_query)
                    while result.has_next():
                        entity_ids_to_check.add(result.get_next()[0])
                except Exception:
                    pass

            # Delete document nodes (relationships deleted automatically by Kùzu)
            delete_query = (
                f"MATCH (d:Document) "
                f"WHERE d.library_id = '{safe_library}' "
                f"AND d.source_file = '{safe_source}' "
                f"DELETE d "
                f"RETURN count(d)"
            )
            result = self._connection.execute(delete_query)
            if result.has_next():
                deleted = result.get_next()[0]

            # Now check each entity - if it has no more document connections, delete it
            orphaned_entities = 0
            for entity_id in entity_ids_to_check:
                safe_entity_id = self._escape_string(entity_id)
                try:
                    # Check if entity still has any document connections
                    check_query = (
                        f"MATCH (d:Document)-[:HAS_ENTITY]->(e:Entity {{id: '{safe_entity_id}'}}) "
                        f"RETURN count(d)"
                    )
                    result = self._connection.execute(check_query)
                    if result.has_next():
                        count = result.get_next()[0]
                        if count == 0:
                            # Entity is orphaned, delete it
                            del_query = f"MATCH (e:Entity {{id: '{safe_entity_id}'}}) DELETE e"
                            self._connection.execute(del_query)
                            orphaned_entities += 1
                except Exception as e:
                    logger.debug(f"Failed to check/delete entity {entity_id}: {e}")

            # Clear entity cache for this library to force reload
            if library_id in self._entity_cache:
                del self._entity_cache[library_id]

            logger.info(
                f"Deleted {deleted} document nodes and {orphaned_entities} "
                f"orphaned entities from {source_file}"
            )

        except Exception as e:
            logger.error(f"Failed to delete by source: {e}")

        return deleted

    def delete_library(self, library_id: str) -> bool:
        """Delete all nodes belonging to a library."""
        if not self._initialized or self._connection is None:
            return False

        safe_library = self._escape_string(library_id)

        try:
            # Delete documents
            self._connection.execute(
                f"MATCH (d:Document) WHERE d.library_id = '{safe_library}' DELETE d"
            )

            # Delete entities
            self._connection.execute(
                f"MATCH (e:Entity) WHERE e.library_id = '{safe_library}' DELETE e"
            )

            # Clear entity cache
            if library_id in self._entity_cache:
                del self._entity_cache[library_id]

            logger.info(f"Deleted all graph nodes for library: {library_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete library from graph: {e}")
            return False

    def cleanup_orphaned_entities(self, library_id: str) -> int:
        """
        Remove orphaned entities that have no document connections.

        Call this to clean up entities from previously deleted documents.

        Returns:
            Number of orphaned entities deleted.
        """
        if not self._initialized or self._connection is None:
            return 0

        safe_library = self._escape_string(library_id)
        deleted = 0

        try:
            # Get all entity IDs in this library
            entity_query = (
                f"MATCH (e:Entity) "
                f"WHERE e.library_id = '{safe_library}' "
                f"RETURN e.id"
            )
            result = self._connection.execute(entity_query)
            entity_ids = []
            while result.has_next():
                entity_ids.append(result.get_next()[0])

            # Check each entity for document connections
            for entity_id in entity_ids:
                safe_entity_id = self._escape_string(entity_id)
                try:
                    check_query = (
                        f"MATCH (d:Document)-[:HAS_ENTITY]->(e:Entity {{id: '{safe_entity_id}'}}) "
                        f"RETURN count(d)"
                    )
                    result = self._connection.execute(check_query)
                    if result.has_next():
                        count = result.get_next()[0]
                        if count == 0:
                            # Entity is orphaned
                            del_query = f"MATCH (e:Entity {{id: '{safe_entity_id}'}}) DELETE e"
                            self._connection.execute(del_query)
                            deleted += 1
                except Exception as e:
                    logger.debug(f"Failed to check entity {entity_id}: {e}")

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} orphaned entities in library {library_id}")

                # Clear entity cache
                if library_id in self._entity_cache:
                    del self._entity_cache[library_id]

        except Exception as e:
            logger.error(f"Failed to cleanup orphaned entities: {e}")

        return deleted

    # =========================================================================
    # Stats and Status
    # =========================================================================

    def count_nodes(self, library_id: str, node_type: str = "Document") -> int:
        """Get the count of nodes for a library."""
        if not self._initialized or self._connection is None:
            return 0

        safe_library = self._escape_string(library_id)

        try:
            query = f"MATCH (n:{node_type}) WHERE n.library_id = '{safe_library}' RETURN count(n)"
            result = self._connection.execute(query)
            if result.has_next():
                return result.get_next()[0]
        except Exception as e:
            logger.warning(f"Count nodes failed: {e}")

        return 0

    def get_library_stats(self, library_id: str) -> dict[str, int]:
        """Get statistics for a library."""
        return {
            "document_nodes": self.count_nodes(library_id, "Document"),
            "entity_nodes": self.count_nodes(library_id, "Entity"),
        }

    @property
    def is_initialized(self) -> bool:
        """Check if the database is initialized."""
        return self._initialized

    def get_status(self) -> str:
        """Get the database status string."""
        if not self._initialized:
            return "not_initialized"
        return "healthy"


def get_graph_db_service() -> GraphDBService:
    """Get the singleton graph database service instance."""
    return GraphDBService()


def initialize_graph_db_service() -> GraphDBService:
    """Initialize the graph database service."""
    service = get_graph_db_service()
    service.initialize()
    return service
