"""
Google Drive integration service.

Handles OAuth 2.0 authentication, folder browsing, and file download/export
for importing documents from Google Drive into the RAG pipeline.
"""

import io
import json
import logging
from pathlib import Path
from typing import Optional, Any

from app.config import get_app_data_dir, get_settings_manager

logger = logging.getLogger(__name__)

# Google API imports (may not be available if packages not installed)
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from googleapiclient.errors import HttpError
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.warning("Google API packages not installed. Google Drive integration disabled.")


# OAuth scopes for read-only access
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Supported MIME types that can be processed by document_processor
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}

# Google Workspace MIME types that need export
GOOGLE_WORKSPACE_MIME_TYPES = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
}

# Export MIME type mappings for Google Workspace files
EXPORT_MIME_MAP = {
    "application/vnd.google-apps.document": {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
    },
    "application/vnd.google-apps.spreadsheet": {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
    },
    "application/vnd.google-apps.presentation": {
        "pdf": "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
}

# File extension mapping for exports
EXPORT_EXTENSION_MAP = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


class GoogleDriveService:
    """
    Service for Google Drive OAuth authentication and file operations.

    Singleton pattern - one instance per application.
    """

    _instance: Optional["GoogleDriveService"] = None
    _service: Optional[Any] = None
    _credentials: Optional[Any] = None
    _pending_flow: Optional[Any] = None
    _user_email: Optional[str] = None

    TOKEN_FILENAME = "google_drive_token.json"
    CREDENTIALS_FILENAME = "google_credentials.json"

    def __new__(cls) -> "GoogleDriveService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._service = None
            cls._instance._credentials = None
            cls._instance._pending_flow = None
            cls._instance._user_email = None
        return cls._instance

    def _get_token_path(self) -> Path:
        """Get path to stored OAuth token."""
        return get_app_data_dir() / "config" / self.TOKEN_FILENAME

    def _get_credentials_path(self) -> Path:
        """Get path to OAuth client credentials."""
        return get_app_data_dir() / "config" / self.CREDENTIALS_FILENAME

    @property
    def is_available(self) -> bool:
        """Check if Google API packages are installed."""
        return GOOGLE_AVAILABLE

    @property
    def is_authenticated(self) -> bool:
        """Check if user is authenticated with valid credentials."""
        return self._credentials is not None and self._credentials.valid

    @property
    def user_email(self) -> Optional[str]:
        """Get the authenticated user's email."""
        return self._user_email

    def has_credentials_file(self) -> bool:
        """Check if OAuth client credentials file exists."""
        return self._get_credentials_path().exists()

    def save_credentials_file(self, credentials_json: str) -> bool:
        """
        Save OAuth client credentials JSON to app data directory.

        Args:
            credentials_json: Contents of credentials.json from Google Cloud Console.

        Returns:
            True if saved successfully.
        """
        try:
            # Validate JSON format
            data = json.loads(credentials_json)
            if "installed" not in data and "web" not in data:
                logger.error("Invalid credentials.json format")
                return False

            creds_path = self._get_credentials_path()
            creds_path.parent.mkdir(parents=True, exist_ok=True)
            with open(creds_path, "w", encoding="utf-8") as f:
                f.write(credentials_json)

            logger.info(f"Saved Google credentials to {creds_path}")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in credentials: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")
            return False

    def try_load_token(self) -> bool:
        """
        Try to load saved OAuth token.

        Returns:
            True if authenticated successfully (existing valid/refreshed token).
        """
        if not GOOGLE_AVAILABLE:
            return False

        token_path = self._get_token_path()
        if not token_path.exists():
            logger.debug("No saved Google Drive token found")
            return False

        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

            if creds.valid:
                self._credentials = creds
                self._build_service()
                self._fetch_user_info()
                logger.info("Loaded valid Google Drive token")
                return True

            if creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Google Drive token")
                creds.refresh(Request())
                self._credentials = creds
                self._save_token()
                self._build_service()
                self._fetch_user_info()
                return True

            logger.warning("Google Drive token expired and cannot be refreshed")
            return False

        except Exception as e:
            logger.error(f"Failed to load Google Drive token: {e}")
            return False

    def get_auth_url(self) -> Optional[str]:
        """
        Start OAuth flow and return authorization URL.

        User should open this URL in browser, grant access, and copy the auth code.

        Returns:
            Authorization URL, or None if credentials file not found.
        """
        if not GOOGLE_AVAILABLE:
            logger.error("Google API packages not installed")
            return None

        creds_path = self._get_credentials_path()
        if not creds_path.exists():
            logger.error("No credentials.json file found")
            return None

        try:
            # Use OOB (out-of-band) flow - user copies code manually
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path),
                SCOPES,
                redirect_uri="urn:ietf:wg:oauth:2.0:oob"
            )
            auth_url, _ = flow.authorization_url(
                prompt="consent",
                access_type="offline"
            )
            self._pending_flow = flow
            logger.info("Generated Google OAuth authorization URL")
            return auth_url

        except Exception as e:
            logger.error(f"Failed to create OAuth flow: {e}")
            return None

    def complete_auth(self, auth_code: str) -> bool:
        """
        Complete OAuth flow with authorization code.

        Args:
            auth_code: Authorization code from Google consent page.

        Returns:
            True if authentication completed successfully.
        """
        if not GOOGLE_AVAILABLE:
            return False

        if self._pending_flow is None:
            logger.error("No pending OAuth flow - call get_auth_url first")
            return False

        try:
            self._pending_flow.fetch_token(code=auth_code.strip())
            self._credentials = self._pending_flow.credentials
            self._pending_flow = None

            self._save_token()
            self._build_service()
            self._fetch_user_info()

            logger.info(f"Google Drive authentication completed for {self._user_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to complete OAuth: {e}")
            self._pending_flow = None
            return False

    def disconnect(self) -> bool:
        """
        Disconnect Google Drive (clear saved token).

        Returns:
            True if disconnected successfully.
        """
        try:
            # Clear in-memory state
            self._credentials = None
            self._service = None
            self._user_email = None
            self._pending_flow = None

            # Delete token file
            token_path = self._get_token_path()
            if token_path.exists():
                token_path.unlink()
                logger.info("Deleted Google Drive token")

            return True

        except Exception as e:
            logger.error(f"Failed to disconnect: {e}")
            return False

    def _save_token(self) -> None:
        """Save OAuth token to file."""
        if self._credentials is None:
            return

        try:
            token_path = self._get_token_path()
            token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(self._credentials.to_json())
            logger.debug(f"Saved Google Drive token to {token_path}")
        except Exception as e:
            logger.error(f"Failed to save token: {e}")

    def _build_service(self) -> None:
        """Build Google Drive API service object."""
        if self._credentials is None:
            return

        try:
            self._service = build("drive", "v3", credentials=self._credentials)
            logger.debug("Built Google Drive service")
        except Exception as e:
            logger.error(f"Failed to build Drive service: {e}")

    def _fetch_user_info(self) -> None:
        """Fetch authenticated user's email."""
        if self._service is None:
            return

        try:
            about = self._service.about().get(fields="user").execute()
            self._user_email = about.get("user", {}).get("emailAddress")
        except Exception as e:
            logger.warning(f"Failed to fetch user info: {e}")

    # =========================================================================
    # Folder Browsing
    # =========================================================================

    def list_folder(
        self,
        folder_id: str = "root",
        page_size: int = 100
    ) -> dict[str, Any]:
        """
        List files and subfolders in a folder.

        Args:
            folder_id: Folder ID ("root" for root folder).
            page_size: Maximum items per page.

        Returns:
            Dict with 'files' list and 'path' breadcrumb.
        """
        if self._service is None:
            return {"files": [], "path": [], "error": "Not authenticated"}

        try:
            # Query for files in this folder
            query = f"'{folder_id}' in parents and trashed = false"

            all_files = []
            page_token = None

            while True:
                response = self._service.files().list(
                    q=query,
                    spaces="drive",
                    pageSize=page_size,
                    fields=(
                        "nextPageToken, "
                        "files(id, name, mimeType, modifiedTime, size, parents)"
                    ),
                    orderBy="folder,name",
                    pageToken=page_token,
                ).execute()

                files = response.get("files", [])
                for f in files:
                    mime_type = f.get("mimeType", "")
                    is_folder = mime_type == "application/vnd.google-apps.folder"
                    is_google_workspace = mime_type in GOOGLE_WORKSPACE_MIME_TYPES
                    is_supported = (
                        is_folder or
                        is_google_workspace or
                        mime_type in SUPPORTED_MIME_TYPES
                    )

                    all_files.append({
                        "id": f.get("id"),
                        "name": f.get("name"),
                        "mimeType": mime_type,
                        "modifiedTime": f.get("modifiedTime"),
                        "size": f.get("size"),
                        "isFolder": is_folder,
                        "isGoogleWorkspace": is_google_workspace,
                        "supported": is_supported,
                        "typeLabel": GOOGLE_WORKSPACE_MIME_TYPES.get(mime_type, ""),
                    })

                page_token = response.get("nextPageToken")
                if page_token is None:
                    break

            # Get breadcrumb path
            path = self._get_folder_path(folder_id)

            return {"files": all_files, "path": path}

        except HttpError as e:
            logger.error(f"Failed to list folder: {e}")
            return {"files": [], "path": [], "error": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error listing folder: {e}")
            return {"files": [], "path": [], "error": str(e)}

    def _get_folder_path(self, folder_id: str) -> list[dict]:
        """
        Get breadcrumb path from root to folder.

        Returns:
            List of {id, name} dicts from root to current folder.
        """
        if self._service is None or folder_id == "root":
            return [{"id": "root", "name": "My Drive"}]

        path = []
        current_id = folder_id

        try:
            # Walk up the folder tree
            while current_id and current_id != "root":
                metadata = self._service.files().get(
                    fileId=current_id,
                    fields="id, name, parents"
                ).execute()

                path.insert(0, {
                    "id": metadata.get("id"),
                    "name": metadata.get("name")
                })

                parents = metadata.get("parents", [])
                current_id = parents[0] if parents else None

            # Add root
            path.insert(0, {"id": "root", "name": "My Drive"})

        except Exception as e:
            logger.warning(f"Failed to get folder path: {e}")
            return [{"id": "root", "name": "My Drive"}]

        return path

    # =========================================================================
    # File Download
    # =========================================================================

    def get_file_info(self, file_id: str) -> Optional[dict]:
        """
        Get metadata for a single file.

        Returns:
            File metadata dict, or None if not found.
        """
        if self._service is None:
            return None

        try:
            metadata = self._service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, modifiedTime"
            ).execute()

            mime_type = metadata.get("mimeType", "")
            is_google_workspace = mime_type in GOOGLE_WORKSPACE_MIME_TYPES
            is_supported = (
                is_google_workspace or
                mime_type in SUPPORTED_MIME_TYPES
            )

            return {
                "id": metadata.get("id"),
                "name": metadata.get("name"),
                "mimeType": mime_type,
                "size": metadata.get("size"),
                "modifiedTime": metadata.get("modifiedTime"),
                "isGoogleWorkspace": is_google_workspace,
                "supported": is_supported,
            }

        except Exception as e:
            logger.error(f"Failed to get file info: {e}")
            return None

    def download_file(self, file_id: str) -> Optional[tuple[bytes, str, str]]:
        """
        Download file content.

        For regular files: direct download.
        For Google Workspace files: export to configured format.

        Args:
            file_id: Google Drive file ID.

        Returns:
            Tuple of (content_bytes, filename, mime_type), or None on error.
        """
        if self._service is None:
            return None

        try:
            # Get file metadata
            metadata = self._service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size"
            ).execute()

            mime_type = metadata.get("mimeType", "")
            filename = metadata.get("name", "unknown")

            if mime_type in GOOGLE_WORKSPACE_MIME_TYPES:
                # Export Google Workspace file
                return self._export_workspace_file(file_id, filename, mime_type)
            else:
                # Download regular file
                return self._download_regular_file(file_id, filename, mime_type)

        except Exception as e:
            logger.error(f"Failed to download file {file_id}: {e}")
            return None

    def _download_regular_file(
        self,
        file_id: str,
        filename: str,
        mime_type: str
    ) -> Optional[tuple[bytes, str, str]]:
        """Download a regular (non-Google Workspace) file."""
        try:
            request = self._service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()

            buffer.seek(0)
            content = buffer.read()

            logger.info(f"Downloaded file: {filename} ({len(content)} bytes)")
            return content, filename, mime_type

        except Exception as e:
            logger.error(f"Failed to download regular file: {e}")
            return None

    def _export_workspace_file(
        self,
        file_id: str,
        filename: str,
        source_mime_type: str
    ) -> Optional[tuple[bytes, str, str]]:
        """Export a Google Workspace file to downloadable format."""
        try:
            # Get export format from settings
            settings_mgr = get_settings_manager()
            export_format = settings_mgr.ai_settings.google_drive.export_format

            # Get export MIME type
            format_map = EXPORT_MIME_MAP.get(source_mime_type, {})
            export_mime = format_map.get(export_format)

            if not export_mime:
                # Default to PDF
                export_mime = "application/pdf"
                export_format = "pdf"

            # Export the file
            request = self._service.files().export_media(
                fileId=file_id,
                mimeType=export_mime
            )
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                status, done = downloader.next_chunk()

            buffer.seek(0)
            content = buffer.read()

            # Add appropriate extension to filename
            extension = EXPORT_EXTENSION_MAP.get(export_mime, f".{export_format}")
            export_filename = f"{filename}{extension}"

            logger.info(
                f"Exported Google Workspace file: {filename} -> {export_filename} "
                f"({len(content)} bytes)"
            )
            return content, export_filename, export_mime

        except Exception as e:
            logger.error(f"Failed to export workspace file: {e}")
            return None

    def is_file_supported(self, mime_type: str) -> bool:
        """Check if a file type can be processed."""
        return (
            mime_type in SUPPORTED_MIME_TYPES or
            mime_type in GOOGLE_WORKSPACE_MIME_TYPES
        )


def get_google_drive_service() -> GoogleDriveService:
    """Get the singleton Google Drive service instance."""
    return GoogleDriveService()
