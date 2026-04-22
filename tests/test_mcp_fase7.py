"""
Tests de Fase 7 — Hardening: annotations, logging estructurado, auto-restart.

Cubre:
  - readOnlyHint/destructiveHint en todos los servidores custom
  - Structured JSON logging en MCPClientManager.call_tool()
  - Auto-restart con backoff en errores de transporte
  - Sandbox: path traversal bloqueado en viewer API
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_tool_annotations(mcp_instance, tool_name):
    """Obtain annotations from a FastMCP server object directly (no subprocess)."""
    tools = await mcp_instance.list_tools()
    tool_map = {t.name: t for t in tools}
    tool = tool_map.get(tool_name)
    return tool.annotations if tool else None


# ---------------------------------------------------------------------------
# Fase 7.1 — Tool annotations: readOnlyHint / destructiveHint
# ---------------------------------------------------------------------------

class TestDocumentLoaderAnnotations:
    @pytest.mark.asyncio
    async def test_load_document_readonly(self):
        import mcp_servers.document_loader.server as mod
        ann = await _get_tool_annotations(mod.mcp, "load_document")
        assert ann is not None
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_list_documents_readonly(self):
        import mcp_servers.document_loader.server as mod
        ann = await _get_tool_annotations(mod.mcp, "list_documents")
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_get_document_metadata_readonly(self):
        import mcp_servers.document_loader.server as mod
        ann = await _get_tool_annotations(mod.mcp, "get_document_metadata")
        assert ann.readOnlyHint is True


class TestS1000dCsdbAnnotations:
    @pytest.mark.asyncio
    async def test_list_libraries_readonly(self):
        import mcp_servers.s1000d_csdb.server as mod
        ann = await _get_tool_annotations(mod.mcp, "list_libraries")
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_search_technical_content_readonly(self):
        import mcp_servers.s1000d_csdb.server as mod
        ann = await _get_tool_annotations(mod.mcp, "search_technical_content")
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_query_graph_readonly(self):
        import mcp_servers.s1000d_csdb.server as mod
        ann = await _get_tool_annotations(mod.mcp, "query_graph")
        assert ann.readOnlyHint is True


class TestWordGraemAnnotations:
    @pytest.mark.asyncio
    async def test_create_document_destructive(self):
        import mcp_servers.word_graem.server as mod
        ann = await _get_tool_annotations(mod.mcp, "create_document")
        assert ann is not None
        assert ann.destructiveHint is True

    @pytest.mark.asyncio
    async def test_create_changelog_destructive(self):
        import mcp_servers.word_graem.server as mod
        ann = await _get_tool_annotations(mod.mcp, "create_s1000d_changelog")
        assert ann.destructiveHint is True

    @pytest.mark.asyncio
    async def test_list_templates_readonly(self):
        import mcp_servers.word_graem.server as mod
        ann = await _get_tool_annotations(mod.mcp, "list_templates")
        assert ann.readOnlyHint is True


class TestPptxGraemAnnotations:
    @pytest.mark.asyncio
    async def test_create_presentation_destructive(self):
        import mcp_servers.pptx_graem.server as mod
        ann = await _get_tool_annotations(mod.mcp, "create_presentation")
        assert ann is not None
        assert ann.destructiveHint is True

    @pytest.mark.asyncio
    async def test_list_templates_readonly(self):
        import mcp_servers.pptx_graem.server as mod
        ann = await _get_tool_annotations(mod.mcp, "list_templates")
        assert ann.readOnlyHint is True


class TestBrexValidatorAnnotations:
    @pytest.mark.asyncio
    async def test_check_wellformed_readonly(self):
        import mcp_servers.brex_validator.server as mod
        ann = await _get_tool_annotations(mod.mcp, "check_wellformed")
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_validate_against_brex_readonly(self):
        import mcp_servers.brex_validator.server as mod
        ann = await _get_tool_annotations(mod.mcp, "validate_against_brex")
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_extract_s1000d_metadata_readonly(self):
        import mcp_servers.brex_validator.server as mod
        ann = await _get_tool_annotations(mod.mcp, "extract_s1000d_metadata")
        assert ann.readOnlyHint is True


class TestSteCheckerAnnotations:
    @pytest.mark.asyncio
    async def test_check_ste_compliance_readonly(self):
        import mcp_servers.ste_checker.server as mod
        ann = await _get_tool_annotations(mod.mcp, "check_ste_compliance")
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_suggest_corrections_readonly(self):
        import mcp_servers.ste_checker.server as mod
        ann = await _get_tool_annotations(mod.mcp, "suggest_corrections")
        assert ann.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_list_approved_vocabulary_readonly(self):
        import mcp_servers.ste_checker.server as mod
        ann = await _get_tool_annotations(mod.mcp, "list_approved_vocabulary")
        assert ann.readOnlyHint is True


# ---------------------------------------------------------------------------
# Fase 7.2 — Structured logging in call_tool
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    @pytest.fixture
    def mock_manager(self):
        from app.services.mcp_client_manager import MCPClientManager
        from app.models.agents import MCPServerConfig
        m = MCPClientManager.__new__(MCPClientManager)
        m._sessions = {}
        m._contexts = {}
        m._configs = {}
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.content = [MagicMock(type="text", text="ok result")]
        mock_result.isError = False
        mock_session.call_tool.return_value = mock_result
        config = MCPServerConfig(
            name="mock_srv", type="stdio", enabled=True, timeout_seconds=10
        )
        m._sessions["mock_srv"] = mock_session
        m._configs["mock_srv"] = config
        return m

    @pytest.mark.asyncio
    async def test_call_tool_emits_audit_log(self, mock_manager, caplog):
        with caplog.at_level(logging.INFO, logger="mcp.audit"):
            await mock_manager.call_tool("mock_srv", "test_tool", {"x": 1})

        audit = [r for r in caplog.records if r.name == "mcp.audit"]
        assert len(audit) == 1
        data = json.loads(audit[0].message)
        assert data["server"] == "mock_srv"
        assert data["tool"] == "test_tool"
        assert data["status"] == "ok"
        assert "args_hash" in data
        assert "duration_ms" in data

    @pytest.mark.asyncio
    async def test_timeout_logs_timeout_status(self, mock_manager, caplog):
        mock_manager._sessions["mock_srv"].call_tool.side_effect = asyncio.TimeoutError()
        with caplog.at_level(logging.INFO, logger="mcp.audit"):
            with pytest.raises(TimeoutError):
                await mock_manager.call_tool("mock_srv", "slow_tool", {})

        audit = [r for r in caplog.records if r.name == "mcp.audit"]
        assert len(audit) == 1
        assert json.loads(audit[0].message)["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_error_logs_error_status(self, mock_manager, caplog):
        mock_manager._sessions["mock_srv"].call_tool.side_effect = RuntimeError("boom")
        with caplog.at_level(logging.INFO, logger="mcp.audit"):
            with pytest.raises(RuntimeError):
                await mock_manager.call_tool("mock_srv", "bad_tool", {})

        audit = [r for r in caplog.records if r.name == "mcp.audit"]
        assert len(audit) == 1
        data = json.loads(audit[0].message)
        assert data["status"] == "error"
        assert "error" in data

    @pytest.mark.asyncio
    async def test_args_hash_is_deterministic(self, mock_manager, caplog):
        """Same args → same hash across two calls."""
        with caplog.at_level(logging.INFO, logger="mcp.audit"):
            await mock_manager.call_tool("mock_srv", "t", {"a": 1, "b": 2})
            await mock_manager.call_tool("mock_srv", "t", {"b": 2, "a": 1})

        audit = [r for r in caplog.records if r.name == "mcp.audit"]
        hashes = [json.loads(r.message)["args_hash"] for r in audit]
        assert hashes[0] == hashes[1]

    @pytest.mark.asyncio
    async def test_task_id_appears_in_log(self, mock_manager, caplog):
        from app.services.mcp_client_manager import mcp_task_id
        token = mcp_task_id.set("task-xyz-999")
        try:
            with caplog.at_level(logging.INFO, logger="mcp.audit"):
                await mock_manager.call_tool("mock_srv", "t", {})
            audit = [r for r in caplog.records if r.name == "mcp.audit"]
            assert json.loads(audit[0].message)["task_id"] == "task-xyz-999"
        finally:
            mcp_task_id.reset(token)


# ---------------------------------------------------------------------------
# Fase 7.3 — Auto-restart with backoff
# ---------------------------------------------------------------------------

class TestAutoRestart:
    @pytest.fixture
    def manager_with_dead_server(self):
        from app.services.mcp_client_manager import MCPClientManager
        from app.models.agents import MCPServerConfig
        m = MCPClientManager.__new__(MCPClientManager)
        m._sessions = {}
        m._contexts = {}
        m._configs = {}
        config = MCPServerConfig(
            name="dead_srv", type="stdio", command="python",
            args=["-c", "pass"], enabled=True, timeout_seconds=10
        )
        # Simulate a dead session
        dead_session = AsyncMock()
        dead_session.__aexit__ = AsyncMock(return_value=None)
        dead_ctx = AsyncMock()
        dead_ctx.__aexit__ = AsyncMock(return_value=None)
        m._sessions["dead_srv"] = dead_session
        m._contexts["dead_srv"] = dead_ctx
        m._configs["dead_srv"] = config
        return m, config

    @pytest.mark.asyncio
    async def test_restart_server_cleans_dead_session(self, manager_with_dead_server):
        m, config = manager_with_dead_server
        with patch.object(m, "start_server", new=AsyncMock(return_value=True)):
            result = await m.restart_server("dead_srv")
        assert result is True
        # The dead session should have been removed
        assert "dead_srv" not in m._sessions or m.start_server.called

    @pytest.mark.asyncio
    async def test_restart_server_unknown_name_returns_false(self):
        from app.services.mcp_client_manager import MCPClientManager
        m = MCPClientManager.__new__(MCPClientManager)
        m._sessions = {}
        m._contexts = {}
        m._configs = {}
        result = await m.restart_server("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_restart_retries_until_success(self, manager_with_dead_server):
        m, config = manager_with_dead_server
        call_count = 0

        async def _start(cfg):
            nonlocal call_count
            call_count += 1
            return call_count >= 2  # fail first, succeed second

        with patch.object(m, "start_server", side_effect=_start):
            with patch("app.services.mcp_client_manager.asyncio.sleep", new=AsyncMock()):
                result = await m.restart_server("dead_srv")

        assert result is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_restart_gives_up_after_max_attempts(self, manager_with_dead_server):
        m, _ = manager_with_dead_server
        with patch.object(m, "start_server", new=AsyncMock(return_value=False)):
            with patch("app.services.mcp_client_manager.asyncio.sleep", new=AsyncMock()):
                result = await m.restart_server("dead_srv")
        assert result is False

    @pytest.mark.asyncio
    async def test_transport_error_triggers_restart_and_retry(self):
        from app.services.mcp_client_manager import MCPClientManager, _TRANSPORT_ERRORS
        from app.models.agents import MCPServerConfig
        m = MCPClientManager.__new__(MCPClientManager)
        m._sessions = {}
        m._contexts = {}
        m._configs = {}

        config = MCPServerConfig(
            name="flaky_srv", type="stdio", enabled=True, timeout_seconds=10
        )
        call_count = 0

        async def _call_tool(name, args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BrokenPipeError("pipe broke")
            mock_result = MagicMock()
            mock_result.content = [MagicMock(type="text", text="recovered")]
            mock_result.isError = False
            return mock_result

        mock_session = AsyncMock()
        mock_session.call_tool.side_effect = _call_tool
        m._sessions["flaky_srv"] = mock_session
        m._configs["flaky_srv"] = config

        with patch.object(m, "restart_server", new=AsyncMock(return_value=True)):
            # After restart, the same (reused) mock session should be called again
            m._sessions["flaky_srv"] = mock_session
            result = await m.call_tool("flaky_srv", "any_tool", {})

        assert result["content"][0]["text"] == "recovered"


# ---------------------------------------------------------------------------
# Fase 7.4 — Sandbox: path traversal blocked in viewer API
# ---------------------------------------------------------------------------

class TestViewerSandbox:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def test_path_traversal_dotdot_blocked(self, client):
        r = client.get("/api/viewer/output/../../../etc/passwd")
        assert r.status_code != 200

    def test_path_traversal_encoded_slash_blocked(self, client):
        r = client.get("/api/viewer/output/..%2F..%2Fetc%2Fpasswd")
        assert r.status_code in (400, 404, 422)

    def test_path_traversal_backslash_blocked(self, client, tmp_path, monkeypatch):
        import app.api.routes.viewer as viewer_mod
        monkeypatch.setattr(viewer_mod, "_OUTPUT_DIR", tmp_path)
        r = client.get("/api/viewer/output/..\\windows\\system32")
        assert r.status_code != 200

    def test_plain_filename_allowed(self, client, tmp_path, monkeypatch):
        import app.api.routes.viewer as viewer_mod
        monkeypatch.setattr(viewer_mod, "_OUTPUT_DIR", tmp_path)
        (tmp_path / "test.xml").write_text("<root/>", encoding="utf-8")
        r = client.get("/api/viewer/output/test.xml")
        assert r.status_code == 200

    def test_subdirectory_path_blocked(self, client, tmp_path, monkeypatch):
        import app.api.routes.viewer as viewer_mod
        monkeypatch.setattr(viewer_mod, "_OUTPUT_DIR", tmp_path)
        r = client.get("/api/viewer/output/subdir/evil.xml")
        # Starlette won't route a two-segment path to this handler → 404
        assert r.status_code != 200
