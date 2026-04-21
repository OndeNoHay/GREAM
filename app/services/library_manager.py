"""
Library manager for organizing documents into collections.

Each library is a separate namespace for documents, allowing users
to segment information by project/system.
"""

import json
import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import ensure_data_directories

logger = logging.getLogger(__name__)


@dataclass
class Library:
    """Represents a document library/collection."""
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str
    document_count: int = 0


class LibraryManager:
    """
    Manages library CRUD operations.

    Libraries are stored in %APPDATA%/GraphRagExec/config/libraries.json
    """

    _instance: Optional["LibraryManager"] = None
    _libraries_file: Path
    _libraries: dict[str, Library]

    def __new__(cls) -> "LibraryManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize the library manager."""
        dirs = ensure_data_directories()
        self._libraries_file = dirs["config"] / "libraries.json"
        self._libraries = self._load_libraries()

        # Create default library if none exist
        if not self._libraries:
            self.create_library(
                name="Default",
                description="Default document library"
            )

    def _load_libraries(self) -> dict[str, Library]:
        """Load libraries from file."""
        if self._libraries_file.exists():
            try:
                with open(self._libraries_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                libraries = {}
                for lib_data in data:
                    lib = Library(**lib_data)
                    libraries[lib.id] = lib
                logger.info(f"Loaded {len(libraries)} libraries")
                return libraries
            except Exception as e:
                logger.warning(f"Failed to load libraries: {e}")

        return {}

    def _save_libraries(self) -> None:
        """Save libraries to file."""
        try:
            data = [asdict(lib) for lib in self._libraries.values()]
            with open(self._libraries_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug("Libraries saved")
        except Exception as e:
            logger.error(f"Failed to save libraries: {e}")
            raise

    def create_library(
        self,
        name: str,
        description: str = ""
    ) -> Library:
        """
        Create a new library.

        Args:
            name: Library name.
            description: Optional description.

        Returns:
            The created Library.

        Raises:
            ValueError: If name is empty or already exists.
        """
        if not name or not name.strip():
            raise ValueError("Library name cannot be empty")

        # Check for duplicate names
        for lib in self._libraries.values():
            if lib.name.lower() == name.strip().lower():
                raise ValueError(f"Library '{name}' already exists")

        now = datetime.utcnow().isoformat()
        library = Library(
            id=str(uuid.uuid4()),
            name=name.strip(),
            description=description.strip(),
            created_at=now,
            updated_at=now,
            document_count=0
        )

        self._libraries[library.id] = library
        self._save_libraries()

        logger.info(f"Created library: {library.name} ({library.id})")
        return library

    def get_library(self, library_id: str) -> Optional[Library]:
        """Get a library by ID."""
        return self._libraries.get(library_id)

    def get_library_by_name(self, name: str) -> Optional[Library]:
        """Get a library by name (case-insensitive)."""
        for lib in self._libraries.values():
            if lib.name.lower() == name.lower():
                return lib
        return None

    def list_libraries(self) -> list[Library]:
        """Get all libraries sorted by name."""
        return sorted(self._libraries.values(), key=lambda x: x.name.lower())

    def update_library(
        self,
        library_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None
    ) -> Library:
        """
        Update a library.

        Args:
            library_id: ID of the library to update.
            name: New name (optional).
            description: New description (optional).

        Returns:
            The updated Library.

        Raises:
            ValueError: If library not found or name conflict.
        """
        library = self._libraries.get(library_id)
        if not library:
            raise ValueError(f"Library not found: {library_id}")

        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Library name cannot be empty")

            # Check for duplicate names (excluding current)
            for lib in self._libraries.values():
                if lib.id != library_id and lib.name.lower() == name.lower():
                    raise ValueError(f"Library '{name}' already exists")

            library.name = name

        if description is not None:
            library.description = description.strip()

        library.updated_at = datetime.utcnow().isoformat()
        self._save_libraries()

        logger.info(f"Updated library: {library.name}")
        return library

    def delete_library(self, library_id: str) -> bool:
        """
        Delete a library.

        WARNING: This does not delete the associated data in vector/graph DBs.
        That should be handled by the caller.

        Args:
            library_id: ID of the library to delete.

        Returns:
            True if deleted.

        Raises:
            ValueError: If library not found or is the last one.
        """
        if library_id not in self._libraries:
            raise ValueError(f"Library not found: {library_id}")

        if len(self._libraries) <= 1:
            raise ValueError("Cannot delete the last library")

        library = self._libraries[library_id]
        del self._libraries[library_id]
        self._save_libraries()

        logger.info(f"Deleted library: {library.name}")
        return True

    def increment_document_count(self, library_id: str, count: int = 1) -> None:
        """Increment the document count for a library."""
        library = self._libraries.get(library_id)
        if library:
            library.document_count += count
            library.updated_at = datetime.utcnow().isoformat()
            self._save_libraries()

    def decrement_document_count(self, library_id: str, count: int = 1) -> None:
        """Decrement the document count for a library."""
        library = self._libraries.get(library_id)
        if library:
            library.document_count = max(0, library.document_count - count)
            library.updated_at = datetime.utcnow().isoformat()
            self._save_libraries()

    def set_document_count(self, library_id: str, count: int) -> None:
        """Set the document count for a library."""
        library = self._libraries.get(library_id)
        if library:
            library.document_count = max(0, count)
            library.updated_at = datetime.utcnow().isoformat()
            self._save_libraries()

    def get_default_library(self) -> Library:
        """Get the first library (default)."""
        libraries = self.list_libraries()
        return libraries[0] if libraries else self.create_library("Default", "Default library")


def get_library_manager() -> LibraryManager:
    """Get the singleton library manager instance."""
    return LibraryManager()
