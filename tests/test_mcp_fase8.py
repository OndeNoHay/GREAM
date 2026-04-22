"""
Tests de Fase 8 — Demo: prepare_demo helpers y dataset de demo.

Cubre:
  - Presencia y parseabilidad de los 4 archivos de demo_data/
  - check_packages detecta módulos faltantes correctamente
  - check_ollama_model maneja variantes de modelo y Ollama ausente
  - check_mcp_servers retorna True cuando todos los servidores responden
  - wait_for_server hace polling hasta timeout
  - print_summary no lanza excepciones
"""

import asyncio
import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DATA_DIR = REPO_ROOT / "demo_data"

# ---------------------------------------------------------------------------
# Fase 8.1 — Demo dataset files
# ---------------------------------------------------------------------------

class TestDemoDataFiles:
    @pytest.fixture(autouse=True)
    def _ensure_lxml(self):
        pytest.importorskip("lxml")

    def test_oem_bulletin_exists(self):
        p = DEMO_DATA_DIR / "OEM_bulletin_HYD-2024-001.txt"
        assert p.exists(), f"Missing: {p}"

    def test_oem_bulletin_not_empty(self):
        p = DEMO_DATA_DIR / "OEM_bulletin_HYD-2024-001.txt"
        assert p.stat().st_size > 500

    @pytest.mark.parametrize("filename", [
        "DMC-ATEST-A-32-00-00-00A-040A-D_001-00.xml",
        "DMC-ATEST-A-32-10-00-00A-040A-D_001-00.xml",
        "DMC-ATEST-A-32-10-00-00A-520A-D_001-00.xml",
    ])
    def test_xml_dm_exists_and_parses(self, filename):
        from lxml import etree as ET
        p = DEMO_DATA_DIR / filename
        assert p.exists(), f"Missing: {p}"
        tree = ET.parse(str(p))
        root = tree.getroot()
        assert root.tag == "dmodule", f"Root tag not <dmodule> in {filename}"

    def test_hydraulic_dm_has_prv_content(self):
        from lxml import etree as ET
        p = DEMO_DATA_DIR / "DMC-ATEST-A-32-00-00-00A-040A-D_001-00.xml"
        tree = ET.parse(str(p))
        text = ET.tostring(tree.getroot(), encoding="unicode")
        assert "PRV" in text
        assert "3000 PSI" in text
        assert "HYD-PRV-3A" in text

    def test_landing_gear_desc_dm_has_retraction_specs(self):
        from lxml import etree as ET
        p = DEMO_DATA_DIR / "DMC-ATEST-A-32-10-00-00A-040A-D_001-00.xml"
        tree = ET.parse(str(p))
        text = ET.tostring(tree.getroot(), encoding="unicode")
        assert "320 mm" in text
        assert "8 seconds" in text

    def test_maintenance_procedure_dm_has_steps(self):
        from lxml import etree as ET
        p = DEMO_DATA_DIR / "DMC-ATEST-A-32-10-00-00A-520A-D_001-00.xml"
        tree = ET.parse(str(p))
        root = tree.getroot()
        steps = root.findall(".//{*}proceduralStep")
        assert len(steps) >= 5, "Expected at least 5 procedural steps"

    def test_maintenance_procedure_has_warnings(self):
        from lxml import etree as ET
        p = DEMO_DATA_DIR / "DMC-ATEST-A-32-10-00-00A-520A-D_001-00.xml"
        tree = ET.parse(str(p))
        root = tree.getroot()
        warnings = root.findall(".//{*}warning")
        assert len(warnings) >= 2

    def test_all_dms_have_dm_code_attributes(self):
        from lxml import etree as ET
        required_attrs = ["modelIdentCode", "systemCode", "infoCode"]
        for filename in [
            "DMC-ATEST-A-32-00-00-00A-040A-D_001-00.xml",
            "DMC-ATEST-A-32-10-00-00A-040A-D_001-00.xml",
            "DMC-ATEST-A-32-10-00-00A-520A-D_001-00.xml",
        ]:
            p = DEMO_DATA_DIR / filename
            tree = ET.parse(str(p))
            dm_code = tree.getroot().find(".//{*}dmCode")
            assert dm_code is not None, f"No <dmCode> in {filename}"
            for attr in required_attrs:
                assert dm_code.get(attr), f"Missing {attr} in {filename}"

    def test_procedure_dm_infocode_is_520(self):
        from lxml import etree as ET
        p = DEMO_DATA_DIR / "DMC-ATEST-A-32-10-00-00A-520A-D_001-00.xml"
        tree = ET.parse(str(p))
        dm_code = tree.getroot().find(".//{*}dmCode")
        assert dm_code.get("infoCode") == "520"

    def test_description_dms_infocode_is_040(self):
        from lxml import etree as ET
        for filename in [
            "DMC-ATEST-A-32-00-00-00A-040A-D_001-00.xml",
            "DMC-ATEST-A-32-10-00-00A-040A-D_001-00.xml",
        ]:
            p = DEMO_DATA_DIR / filename
            tree = ET.parse(str(p))
            dm_code = tree.getroot().find(".//{*}dmCode")
            assert dm_code.get("infoCode") == "040", f"infoCode not 040 in {filename}"


# ---------------------------------------------------------------------------
# Fase 8.2 — check_packages
# ---------------------------------------------------------------------------

class TestCheckPackages:
    def test_all_installed_returns_true(self, capsys):
        with patch.dict("sys.modules", {}):
            import importlib as il
            with patch.object(il, "import_module", return_value=MagicMock()):
                sys.path.insert(0, str(REPO_ROOT))
                from scripts.prepare_demo import check_packages, REQUIRED_PACKAGES
                with patch("scripts.prepare_demo.importlib.import_module", return_value=MagicMock()):
                    result = check_packages()
                assert result is True

    def test_missing_package_returns_false(self, capsys):
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.prepare_demo import REQUIRED_PACKAGES

        def _raise_on_lxml(name):
            if name == "lxml":
                raise ImportError("no module named lxml")
            return MagicMock()

        import scripts.prepare_demo as mod
        with patch.object(mod.importlib, "import_module", side_effect=_raise_on_lxml):
            result = mod.check_packages()
        assert result is False


# ---------------------------------------------------------------------------
# Fase 8.3 — check_ollama_model
# ---------------------------------------------------------------------------

class TestCheckOllamaModel:
    def _run(self, stdout: str, returncode: int = 0):
        sys.path.insert(0, str(REPO_ROOT))
        import scripts.prepare_demo as mod
        mock_result = MagicMock()
        mock_result.returncode = returncode
        mock_result.stdout = stdout
        with patch("scripts.prepare_demo.subprocess.run", return_value=mock_result):
            return mod.check_ollama_model()

    def test_preferred_model_found(self, capsys):
        ok, model = self._run("qwen3:14b latest 4.5 GB\n")
        assert ok is True
        assert model == "qwen3:14b"

    def test_fallback_model_found(self, capsys):
        ok, model = self._run("qwen3:8b latest 4.0 GB\n")
        assert ok is True
        assert model == "qwen3:8b"

    def test_no_model_found(self, capsys):
        ok, model = self._run("llama3:8b latest 4.0 GB\n")
        assert ok is False
        assert model == ""

    def test_ollama_not_found(self, capsys):
        sys.path.insert(0, str(REPO_ROOT))
        import scripts.prepare_demo as mod
        with patch("scripts.prepare_demo.subprocess.run",
                   side_effect=FileNotFoundError("ollama not found")):
            ok, model = mod.check_ollama_model()
        assert ok is False

    def test_ollama_returncode_nonzero(self, capsys):
        ok, model = self._run("", returncode=1)
        assert ok is False


# ---------------------------------------------------------------------------
# Fase 8.4 — check_mcp_servers
# ---------------------------------------------------------------------------

class TestCheckMcpServers:
    @pytest.mark.asyncio
    async def test_all_servers_ok(self, capsys):
        sys.path.insert(0, str(REPO_ROOT))
        import scripts.prepare_demo as mod

        mock_tool = MagicMock()
        mock_tool.name = "mock_tool"
        mock_mcp = MagicMock()
        mock_mcp.list_tools = AsyncMock(return_value=[mock_tool])
        mock_mod = MagicMock()
        mock_mod.mcp = mock_mcp

        with patch.object(mod.importlib, "import_module", return_value=mock_mod):
            result = await mod.check_mcp_servers()
        assert result is True

    @pytest.mark.asyncio
    async def test_server_import_error_returns_false(self, capsys):
        sys.path.insert(0, str(REPO_ROOT))
        import scripts.prepare_demo as mod

        with patch.object(mod.importlib, "import_module",
                          side_effect=ImportError("missing dep")):
            result = await mod.check_mcp_servers()
        assert result is False

    @pytest.mark.asyncio
    async def test_server_missing_mcp_attr_returns_false(self, capsys):
        sys.path.insert(0, str(REPO_ROOT))
        import scripts.prepare_demo as mod

        mock_mod = MagicMock(spec=[])  # no .mcp attribute
        with patch.object(mod.importlib, "import_module", return_value=mock_mod):
            result = await mod.check_mcp_servers()
        assert result is False


# ---------------------------------------------------------------------------
# Fase 8.5 — wait_for_server
# ---------------------------------------------------------------------------

class TestWaitForServer:
    def test_returns_true_when_server_responds(self):
        sys.path.insert(0, str(REPO_ROOT))
        import urllib.request
        import scripts.prepare_demo as mod

        with patch("urllib.request.urlopen", return_value=MagicMock()):
            with patch("scripts.prepare_demo.time.monotonic", side_effect=[0.0, 1.0, 2.0]):
                result = mod.wait_for_server(8000, timeout=30.0)
        assert result is True

    def test_returns_false_on_timeout(self):
        sys.path.insert(0, str(REPO_ROOT))
        import urllib.request
        import scripts.prepare_demo as mod

        call_count = [0]

        def _monotonic():
            call_count[0] += 1
            return 0.0 if call_count[0] == 1 else 31.0

        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            with patch("scripts.prepare_demo.time.monotonic", side_effect=_monotonic):
                with patch("scripts.prepare_demo.time.sleep"):
                    result = mod.wait_for_server(8000, timeout=30.0)
        assert result is False


# ---------------------------------------------------------------------------
# Fase 8.6 — print_summary smoke test
# ---------------------------------------------------------------------------

class TestPrintSummary:
    def test_print_summary_runs_without_error(self, capsys):
        sys.path.insert(0, str(REPO_ROOT))
        import scripts.prepare_demo as mod
        mod.print_summary(port=8000, model="qwen3:14b")
        out = capsys.readouterr().out
        assert "8000" in out
        assert "qwen3:14b" in out
