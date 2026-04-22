"""
Tests de Fase 2 — MCP document_loader.

Cubre:
  - Funciones de extracción (unit tests sin MCP transport)
  - Herramientas MCP: load_document, list_documents, get_document_metadata
  - Test de integración: arranque real del servidor vía MCPClientManager
    (marcado con @pytest.mark.integration — se salta en CI sin procesos reales)
"""

import json
import sys
import pathlib

import pytest

from mcp_servers.document_loader.server import (
    load_document,
    list_documents,
    get_document_metadata,
    _extract_text,
)


# ---------------------------------------------------------------------------
# load_document — texto plano y markdown
# ---------------------------------------------------------------------------

class TestLoadDocument:
    def test_txt_file(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Hello GRAEM", encoding="utf-8")
        assert load_document(str(f)) == "Hello GRAEM"

    def test_md_file(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("# Title\nContent here", encoding="utf-8")
        result = load_document(str(f))
        assert "Title" in result
        assert "Content" in result

    def test_file_not_found(self):
        result = load_document("/nonexistent/file.pdf")
        assert result.startswith("Error")
        assert "not found" in result

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        result = load_document(str(f))
        assert "Error" in result
        assert "unsupported" in result.lower()

    def test_path_is_directory(self, tmp_path):
        result = load_document(str(tmp_path))
        assert "Error" in result

    def test_unicode_content(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Ñoño áéíóú 中文 日本語", encoding="utf-8")
        result = load_document(str(f))
        assert "Ñoño" in result


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------

class TestListDocuments:
    def test_returns_supported_types(self, tmp_path):
        (tmp_path / "a.pdf").touch()
        (tmp_path / "b.docx").touch()
        (tmp_path / "c.txt").write_text("x")
        (tmp_path / "d.xyz").touch()      # unsupported — must be excluded

        result = json.loads(list_documents(str(tmp_path)))
        assert len(result) == 3
        extensions = {pathlib.Path(p).suffix.lower() for p in result}
        assert extensions == {".pdf", ".docx", ".txt"}

    def test_extension_filter(self, tmp_path):
        (tmp_path / "a.pdf").touch()
        (tmp_path / "b.docx").touch()

        result = json.loads(list_documents(str(tmp_path), extensions=[".pdf"]))
        assert len(result) == 1
        assert result[0].endswith(".pdf")

    def test_nonexistent_directory(self):
        result = json.loads(list_documents("/nonexistent/dir"))
        assert result == []

    def test_empty_directory(self, tmp_path):
        result = json.loads(list_documents(str(tmp_path)))
        assert result == []

    def test_recursive_default(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("x")
        (tmp_path / "top.txt").write_text("x")

        result = json.loads(list_documents(str(tmp_path)))
        assert len(result) == 2

    def test_non_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("x")
        (tmp_path / "top.txt").write_text("x")

        result = json.loads(list_documents(str(tmp_path), recursive=False))
        assert len(result) == 1
        assert result[0].endswith("top.txt")

    def test_md_included_by_default(self, tmp_path):
        (tmp_path / "readme.md").write_text("# Readme")
        result = json.loads(list_documents(str(tmp_path)))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_document_metadata
# ---------------------------------------------------------------------------

class TestGetDocumentMetadata:
    def test_txt_metadata(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("Some content", encoding="utf-8")
        meta = json.loads(get_document_metadata(str(f)))
        assert meta["name"] == "notes.txt"
        assert meta["extension"] == ".txt"
        assert meta["size_bytes"] > 0
        assert "path" in meta

    def test_not_found(self):
        meta = json.loads(get_document_metadata("/nonexistent/file.pdf"))
        assert "error" in meta

    def test_size_accurate(self, tmp_path):
        content = "x" * 100
        f = tmp_path / "test.txt"
        f.write_text(content, encoding="utf-8")
        meta = json.loads(get_document_metadata(str(f)))
        assert meta["size_bytes"] == 100


# ---------------------------------------------------------------------------
# _extract_text helper
# ---------------------------------------------------------------------------

def test_extract_text_utf8(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("línea uno\nlínea dos", encoding="utf-8")
    assert _extract_text(f) == "línea uno\nlínea dos"


# ---------------------------------------------------------------------------
# Integration: arranque real del servidor MCP
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_loader_server_starts_and_lists_tools():
    """
    Arranca document_loader como subproceso stdio real y verifica que
    MCPClientManager puede descubrir sus herramientas.

    Requiere que el entorno tenga los paquetes instalados (no se ejecuta en CI
    básico sin procesos reales).
    """
    from app.models.agents import MCPServerConfig
    from app.services.mcp_client_manager import MCPClientManager

    # Usamos una instancia fresca para no contaminar el singleton del app
    manager = MCPClientManager.__new__(MCPClientManager)
    manager._sessions = {}
    manager._contexts = {}
    manager._configs = {}

    config = MCPServerConfig(
        name="document_loader_test",
        type="stdio",
        command=sys.executable,
        args=["-m", "mcp_servers.document_loader.server"],
        enabled=True,
        timeout_seconds=15,
    )

    try:
        ok = await manager.start_server(config)
        assert ok, "document_loader server failed to start"

        tools = await manager.list_tools("document_loader_test")
        tool_names = {t.name for t in tools}
        assert "load_document" in tool_names
        assert "list_documents" in tool_names
        assert "get_document_metadata" in tool_names
    finally:
        await manager.stop_all()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_loader_call_tool_txt(tmp_path):
    """Llama a load_document sobre un .txt real a través del transporte MCP."""
    from app.models.agents import MCPServerConfig
    from app.services.mcp_client_manager import MCPClientManager

    manager = MCPClientManager.__new__(MCPClientManager)
    manager._sessions = {}
    manager._contexts = {}
    manager._configs = {}

    doc = tmp_path / "hello.txt"
    doc.write_text("S1000D Co-Author test", encoding="utf-8")

    config = MCPServerConfig(
        name="dl_call_test",
        type="stdio",
        command=sys.executable,
        args=["-m", "mcp_servers.document_loader.server"],
        enabled=True,
        timeout_seconds=15,
    )

    try:
        await manager.start_server(config)
        result = await manager.call_tool("dl_call_test", "load_document", {"path": str(doc)})
        texts = [item["text"] for item in result["content"] if item.get("type") == "text"]
        combined = "\n".join(texts)
        assert "S1000D Co-Author test" in combined
    finally:
        await manager.stop_all()
