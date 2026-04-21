"""
Vector database service using ChromaDB.

Provides persistent vector storage in %APPDATA%/GraphRagExec/vector_db/.
Supports multiple libraries as separate collections.
"""

import logging
import uuid
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import ensure_data_directories, get_settings_manager

logger = logging.getLogger(__name__)


class VectorDBService:
    """
    Service for vector storage and similarity search using ChromaDB.

    Supports multiple libraries (collections) for document organization.
    Data is persisted in %APPDATA% to survive .exe updates.
    """

    _instance: Optional["VectorDBService"] = None
    _client: Optional[chromadb.PersistentClient] = None
    _collections: dict[str, chromadb.Collection]
    _initialized: bool = False

    def __new__(cls) -> "VectorDBService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._collections = {}
            cls._instance._initialized = False
        return cls._instance

    def _get_db_path(self) -> Path:
        """Get the vector database storage path."""
        dirs = ensure_data_directories()
        return dirs["vector_db"]

    def initialize(self) -> None:
        """Initialize ChromaDB with persistent storage."""
        if self._initialized:
            logger.debug("Vector database already initialized")
            return

        db_path = self._get_db_path()
        logger.info(f"Initializing ChromaDB at: {db_path}")

        try:
            self._client = chromadb.PersistentClient(
                path=str(db_path),
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=False
                )
            )
            self._initialized = True
            logger.info("ChromaDB initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            raise RuntimeError(f"Could not initialize vector database: {e}") from e

    def _get_collection(self, library_id: str) -> chromadb.Collection:
        """
        Get or create a ChromaDB collection for a library.

        Stores the embedding model name in the collection metadata at creation
        time so that a later model change is detected before any write is
        attempted — avoiding a confusing mid-ingestion dimension-mismatch error.
        """
        if not self._initialized or self._client is None:
            raise RuntimeError("Vector database not initialized")

        # Sanitize collection name (ChromaDB has restrictions)
        collection_name = f"lib_{library_id.replace('-', '_')}"

        if collection_name not in self._collections:
            try:
                current_model = get_settings_manager().ai_settings.embedding_model
            except Exception:
                current_model = ""

            try:
                # get_collection does NOT update metadata — safe for mismatch detection
                collection = self._client.get_collection(name=collection_name)
                stored_model = collection.metadata.get("embedding_model", "")
                if stored_model and stored_model != current_model and collection.count() > 0:
                    raise RuntimeError(
                        f"Embedding model mismatch for library '{library_id}': "
                        f"existing data was created with '{stored_model}' but the "
                        f"current model is '{current_model}'. These models produce "
                        f"vectors of different sizes and cannot share the same collection. "
                        f"To fix: open the Documents tab, delete ALL vector embeddings "
                        f"for this library, then re-ingest all documents."
                    )
            except RuntimeError:
                raise
            except ValueError:
                # Collection does not exist yet — create it and record the model
                collection = self._client.create_collection(
                    name=collection_name,
                    metadata={"library_id": library_id, "embedding_model": current_model}
                )

            self._collections[collection_name] = collection

        return self._collections[collection_name]

    def add_chunk(
        self,
        library_id: str,
        chunk_id: str,
        embedding: list[float],
        metadata: dict[str, str],
        text: str = ""
    ) -> str:
        """
        Add a document chunk with its embedding and text content.

        Args:
            library_id: The library to add to.
            chunk_id: Unique identifier for the chunk.
            embedding: The embedding vector.
            metadata: Metadata including source_file, page, chunk_index.
            text: The actual text content of the chunk for RAG retrieval.

        Returns:
            The chunk ID.
        """
        collection = self._get_collection(library_id)

        if not chunk_id:
            chunk_id = str(uuid.uuid4())

        # Ensure all metadata values are strings
        safe_metadata = {str(k): str(v) for k, v in metadata.items()}

        try:
            collection.add(
                ids=[chunk_id],
                embeddings=[embedding],
                metadatas=[safe_metadata],
                documents=[text]  # Store actual text for RAG retrieval
            )
            logger.debug(f"Added chunk to vector DB: {chunk_id}")
            return chunk_id

        except Exception as e:
            err_str = str(e).lower()
            if "dimension" in err_str or "dimensionality" in err_str:
                try:
                    current_model = get_settings_manager().ai_settings.embedding_model
                except Exception:
                    current_model = "current model"
                raise RuntimeError(
                    f"Embedding dimension mismatch — the embedding model was changed "
                    f"after this library's collection was created. Current model: "
                    f"'{current_model}'. To fix: delete all vector embeddings for this "
                    f"library in the Documents tab, then re-ingest all documents."
                ) from e
            logger.error(f"Failed to add chunk: {e}")
            raise

    def search(
        self,
        library_id: str,
        query_embedding: list[float],
        top_k: int = 10,
        where: Optional[dict] = None
    ) -> list[dict]:
        """
        Search for similar chunks using vector similarity.

        Args:
            library_id: The library to search in.
            query_embedding: The query embedding vector.
            top_k: Maximum number of results.
            where: Optional filter conditions.

        Returns:
            List of matching chunks with metadata, scores, and text content.
        """
        collection = self._get_collection(library_id)

        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, 100),
                where=where,
                include=["metadatas", "distances", "documents"]
            )

            formatted_results = []
            if results and results["ids"] and results["ids"][0]:
                ids = results["ids"][0]
                distances = results["distances"][0] if results["distances"] else []
                metadatas = results["metadatas"][0] if results["metadatas"] else []
                documents = results["documents"][0] if results.get("documents") else []

                for i, chunk_id in enumerate(ids):
                    distance = distances[i] if i < len(distances) else 1.0
                    # Convert L2 distance to similarity score
                    similarity = max(0.0, 1.0 - (distance / 2.0))

                    metadata = metadatas[i] if i < len(metadatas) else {}
                    text = documents[i] if i < len(documents) else ""
                    formatted_results.append({
                        "chunk_id": chunk_id,
                        "score": round(similarity, 4),
                        "source_file": metadata.get("source_file", ""),
                        "page": metadata.get("page"),
                        "chunk_index": metadata.get("chunk_index"),
                        "metadata": metadata,
                        "text": text,  # Include actual text content
                        "source": "vector"
                    })

            logger.debug(f"Vector search returned {len(formatted_results)} results")
            return formatted_results

        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            raise

    def get_chunks_by_ids(self, library_id: str, chunk_ids: list[str]) -> dict[str, str]:
        """
        Fetch text content for a list of chunk IDs.

        Used to enrich graph search results (which have no text) before passing
        them to the LLM.

        Returns:
            Dict mapping chunk_id -> text content.
        """
        if not chunk_ids:
            return {}
        collection = self._get_collection(library_id)
        try:
            results = collection.get(ids=chunk_ids, include=["documents"])
            id_to_text: dict[str, str] = {}
            if results and results.get("ids") and results.get("documents"):
                for cid, doc in zip(results["ids"], results["documents"]):
                    id_to_text[cid] = doc or ""
            return id_to_text
        except Exception as e:
            logger.warning(f"get_chunks_by_ids failed: {e}")
            return {}

    def delete_by_source(self, library_id: str, source_file: str) -> int:
        """
        Delete all chunks from a specific source file.

        Args:
            library_id: The library ID.
            source_file: The source filename to delete.

        Returns:
            Number of chunks deleted.
        """
        collection = self._get_collection(library_id)

        try:
            # Get IDs of chunks from this source
            results = collection.get(
                where={"source_file": source_file},
                include=[]
            )

            if results and results["ids"]:
                collection.delete(ids=results["ids"])
                count = len(results["ids"])
                logger.info(f"Deleted {count} chunks from {source_file}")
                return count

            return 0

        except Exception as e:
            logger.error(f"Failed to delete chunks: {e}")
            return 0

    def clear_all(self, library_id: str) -> int:
        """
        Delete all chunks in a library without deleting the collection.

        Args:
            library_id: The library ID.

        Returns:
            Number of chunks deleted.
        """
        collection = self._get_collection(library_id)

        try:
            count = collection.count()
            if count > 0:
                results = collection.get(include=[])
                if results and results["ids"]:
                    collection.delete(ids=results["ids"])
                    logger.info(f"Cleared {count} chunks from library {library_id}")
                    return count
            return 0

        except Exception as e:
            logger.error(f"Failed to clear library vectors: {e}")
            return 0

    def delete_library(self, library_id: str) -> bool:
        """
        Delete an entire library (collection).

        Args:
            library_id: The library ID to delete.

        Returns:
            True if deleted.
        """
        if not self._initialized or self._client is None:
            return False

        collection_name = f"lib_{library_id.replace('-', '_')}"

        try:
            self._client.delete_collection(collection_name)
            if collection_name in self._collections:
                del self._collections[collection_name]
            logger.info(f"Deleted vector collection: {collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete collection: {e}")
            return False

    def count(self, library_id: str) -> int:
        """Get the number of chunks in a library."""
        try:
            collection = self._get_collection(library_id)
            return collection.count()
        except Exception:
            return 0

    def list_sources(self, library_id: str) -> list[str]:
        """
        List all unique source files in a library.

        Args:
            library_id: The library ID.

        Returns:
            List of unique source filenames.
        """
        collection = self._get_collection(library_id)

        try:
            results = collection.get(include=["metadatas"])
            sources = set()

            if results and results["metadatas"]:
                for metadata in results["metadatas"]:
                    if metadata and "source_file" in metadata:
                        sources.add(metadata["source_file"])

            return sorted(list(sources))

        except Exception as e:
            logger.error(f"Failed to list sources: {e}")
            return []

    def get_source_details(
        self,
        library_id: str,
        source_file: str
    ) -> dict:
        """
        Get detailed information about a specific source file.

        Args:
            library_id: The library ID.
            source_file: The source filename.

        Returns:
            Dict with chunk details including IDs, pages, indexes.
        """
        collection = self._get_collection(library_id)

        try:
            results = collection.get(
                where={"source_file": source_file},
                include=["metadatas", "embeddings"]
            )

            chunks = []
            if results and results["ids"]:
                for i, chunk_id in enumerate(results["ids"]):
                    metadata = results["metadatas"][i] if results["metadatas"] else {}
                    embedding = results["embeddings"][i] if results["embeddings"] else []

                    chunks.append({
                        "chunk_id": chunk_id,
                        "page": metadata.get("page", ""),
                        "chunk_index": metadata.get("chunk_index", "0"),
                        "embedding_dim": len(embedding) if embedding else 0,
                        "embedding_preview": embedding[:5] if embedding else []
                    })

            # Sort by chunk_index
            chunks.sort(key=lambda x: int(x.get("chunk_index", 0) or 0))

            return {
                "source_file": source_file,
                "chunk_count": len(chunks),
                "chunks": chunks
            }

        except Exception as e:
            logger.error(f"Failed to get source details: {e}")
            return {
                "source_file": source_file,
                "chunk_count": 0,
                "chunks": [],
                "error": str(e)
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


def get_vector_db_service() -> VectorDBService:
    """Get the singleton vector database service instance."""
    return VectorDBService()


def initialize_vector_db_service() -> VectorDBService:
    """Initialize the vector database service."""
    service = get_vector_db_service()
    service.initialize()
    return service
