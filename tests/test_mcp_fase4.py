"""
Tests de Fase 4 — XML Viewer API + Playwright MCP.

Cubre:
  - GET /api/viewer/output/<filename>  (serve files, path traversal)
  - GET /api/viewer/output             (list files)
  - Viewer HTML presente en static/
  - Test de integración: arranque real de @playwright/mcp
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture: TestClient de la app GRAEM
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Cliente síncrono de la app FastAPI (sin lifespan completo)."""
    from app.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def sample_xml_file(tmp_path, monkeypatch):
    """
    Crea un fichero XML de prueba en el directorio output/ del viewer
    y restaura el directorio original al terminar.
    """
    import app.api.routes.viewer as viewer_mod

    # Redirigir _OUTPUT_DIR al tmp_path de este test
    original_dir = viewer_mod._OUTPUT_DIR
    viewer_mod._OUTPUT_DIR = tmp_path
    monkeypatch.setattr(viewer_mod, "_OUTPUT_DIR", tmp_path)

    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<dmodule xmlns:dc="http://www.purl.org/dc/elements/1.1/">
  <identAndStatusSection>
    <dmAddress>
      <dmIdent>
        <dmCode modelIdentCode="ATEST" systemDiffCode="A" systemCode="32"
                subSystemCode="0" subSubSystemCode="0" assyCode="00"
                disassyCode="00" disassyCodeVariant="A" infoCode="040"
                infoCodeVariant="A" itemLocationCode="D"/>
        <issueInfo issueNumber="001" inWork="00"/>
        <language languageIsoCode="en" countryIsoCode="US"/>
      </dmIdent>
    </dmAddress>
    <dmStatus>
      <security securityClassification="01"/>
    </dmStatus>
  </identAndStatusSection>
  <content>
    <description>
      <levelledPara>
        <title>Hydraulic Power System</title>
        <para>The hydraulic system supplies pressurized fluid to landing gear.</para>
      </levelledPara>
    </description>
  </content>
</dmodule>"""

    xml_file = tmp_path / "dm_test.xml"
    xml_file.write_text(xml_content, encoding="utf-8")
    yield xml_file, xml_content

    viewer_mod._OUTPUT_DIR = original_dir


# ---------------------------------------------------------------------------
# GET /api/viewer/output/<filename>
# ---------------------------------------------------------------------------

class TestGetOutputFile:
    def test_serves_existing_xml(self, client, sample_xml_file):
        xml_file, expected_content = sample_xml_file
        r = client.get(f"/api/viewer/output/{xml_file.name}")
        assert r.status_code == 200
        assert "dmodule" in r.text
        assert "Hydraulic Power System" in r.text

    def test_404_for_nonexistent_file(self, client, sample_xml_file):
        sample_xml_file  # ensure output dir is patched
        r = client.get("/api/viewer/output/nonexistent_file.xml")
        assert r.status_code == 404

    def test_path_traversal_blocked_dotdot(self, client, sample_xml_file):
        sample_xml_file
        r = client.get("/api/viewer/output/../../../etc/passwd")
        # Starlette normalizes the path before routing (/../ collapsed) so
        # the URL no longer matches /api/viewer/output/{filename} → 404.
        # Any non-200 response (400, 404, 422) proves traversal is blocked.
        assert r.status_code != 200

    def test_path_traversal_blocked_encoded_slash(self, client, sample_xml_file):
        sample_xml_file
        r = client.get("/api/viewer/output/..%2F..%2Fetc%2Fpasswd")
        assert r.status_code in (400, 404)

    def test_content_type_xml(self, client, sample_xml_file):
        xml_file, _ = sample_xml_file
        r = client.get(f"/api/viewer/output/{xml_file.name}")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "xml" in ct or "octet-stream" in ct

    def test_serves_txt_file(self, client, sample_xml_file, tmp_path, monkeypatch):
        import app.api.routes.viewer as viewer_mod
        monkeypatch.setattr(viewer_mod, "_OUTPUT_DIR", tmp_path)
        txt = tmp_path / "report.txt"
        txt.write_text("GRAEM report", encoding="utf-8")
        r = client.get("/api/viewer/output/report.txt")
        assert r.status_code == 200
        assert "GRAEM report" in r.text


# ---------------------------------------------------------------------------
# GET /api/viewer/output  (listing)
# ---------------------------------------------------------------------------

class TestListOutputFiles:
    def test_returns_json(self, client, sample_xml_file):
        sample_xml_file
        r = client.get("/api/viewer/output")
        assert r.status_code == 200
        data = r.json()
        assert "files" in data
        assert "count" in data

    def test_lists_created_file(self, client, sample_xml_file):
        xml_file, _ = sample_xml_file
        r = client.get("/api/viewer/output")
        names = [f["name"] for f in r.json()["files"]]
        assert xml_file.name in names

    def test_empty_dir_returns_zero(self, client, monkeypatch, tmp_path):
        import app.api.routes.viewer as viewer_mod
        empty = tmp_path / "empty_output"
        empty.mkdir()
        monkeypatch.setattr(viewer_mod, "_OUTPUT_DIR", empty)
        r = client.get("/api/viewer/output")
        assert r.json()["count"] == 0


# ---------------------------------------------------------------------------
# XML viewer HTML present in static/
# ---------------------------------------------------------------------------

class TestXmlViewerStatic:
    def test_xml_viewer_html_served(self, client):
        r = client.get("/static/xml_viewer.html")
        assert r.status_code == 200
        assert "S1000D" in r.text
        assert "GRAEM" in r.text

    def test_xml_viewer_has_load_endpoint(self, client):
        r = client.get("/static/xml_viewer.html")
        assert "/api/viewer/output/" in r.text

    def test_xml_viewer_has_syntax_highlight(self, client):
        r = client.get("/static/xml_viewer.html")
        assert "highlight" in r.text.lower() or "xml-output" in r.text

    def test_xml_viewer_supports_file_param(self, client):
        r = client.get("/static/xml_viewer.html")
        assert 'params.get("file")' in r.text or "params.get" in r.text


# ---------------------------------------------------------------------------
# Integración: @playwright/mcp arranca via MCPClientManager
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_playwright_mcp_starts_and_has_tools():
    """
    Arranca @playwright/mcp como subproceso stdio real con npx.

    La primera ejecución descarga el paquete (~30 s); en runs posteriores
    usa la caché de npm/npx.
    """
    from app.models.agents import MCPServerConfig
    from app.services.mcp_client_manager import MCPClientManager

    manager = MCPClientManager.__new__(MCPClientManager)
    manager._sessions = {}
    manager._contexts = {}
    manager._configs = {}

    config = MCPServerConfig(
        name="playwright_test",
        type="stdio",
        command="npx",
        args=["-y", "@playwright/mcp", "--headless"],
        enabled=True,
        timeout_seconds=120,  # primera descarga puede tardar
    )

    try:
        ok = await manager.start_server(config)
        assert ok, "@playwright/mcp server failed to start (is Node.js available?)"

        tools = await manager.list_tools("playwright_test")
        tool_names = {t.name for t in tools}

        # @playwright/mcp expone herramientas de navegación y acción
        assert len(tool_names) >= 3, f"Expected ≥3 tools, got: {tool_names}"

        # Verificar presencia de herramientas básicas de Playwright MCP
        nav_tools = {t for t in tool_names if "navigate" in t.lower() or "browser" in t.lower() or "page" in t.lower()}
        assert len(nav_tools) >= 1, f"No navigation tools found in: {tool_names}"
    finally:
        await manager.stop_all()
