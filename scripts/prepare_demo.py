#!/usr/bin/env python3
"""
prepare_demo.py — GRAEM PoC 1 "S1000D Co-Author" demo preparation script.

Performs:
  1. Prerequisite checks (Python version, required packages, Ollama model)
  2. Demo data validation (4 source files present and parseable)
  3. MCP server health check (list_tools on each enabled server)
  4. FastAPI server startup (uvicorn, background)
  5. Browser open to localhost UI

Usage:
    python scripts/prepare_demo.py [--no-browser] [--port 8000]

Exit codes:
    0 — all checks passed, server started
    1 — prerequisite failure
    2 — MCP health check failure
    3 — server startup failure
"""

import argparse
import asyncio
import importlib
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DATA_DIR = REPO_ROOT / "demo_data"
CONFIG_FILE = REPO_ROOT / "config" / "mcp_servers.yaml"

DEMO_FILES = [
    "OEM_bulletin_HYD-2024-001.txt",
    "DMC-ATEST-A-32-00-00-00A-040A-D_001-00.xml",
    "DMC-ATEST-A-32-10-00-00A-040A-D_001-00.xml",
    "DMC-ATEST-A-32-10-00-00A-520A-D_001-00.xml",
]

REQUIRED_PACKAGES = [
    "fastapi",
    "uvicorn",
    "mcp",
    "fastmcp",
    "chromadb",
    "kuzu",
    "openai",
    "lxml",
    "xmlschema",
    "docx",
    "pptx",
]

PREFERRED_MODEL = "qwen3:14b"
FALLBACK_MODEL = "qwen3:8b"

MCP_SERVERS_TO_CHECK = [
    "mcp_servers.document_loader.server",
    "mcp_servers.s1000d_csdb.server",
    "mcp_servers.word_graem.server",
    "mcp_servers.pptx_graem.server",
    "mcp_servers.brex_validator.server",
    "mcp_servers.ste_checker.server",
]

# ── ANSI colours ─────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty() and os.name != "nt" or os.environ.get("FORCE_COLOR")


def _c(code: str, text: str) -> str:
    if _USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text


OK = _c("32", "✓")
FAIL = _c("31", "✗")
WARN = _c("33", "⚠")
INFO = _c("36", "→")


def _step(label: str) -> None:
    print(f"\n{_c('1', label)}")


def _ok(msg: str) -> None:
    print(f"  {OK}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {FAIL}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {WARN}  {msg}")


def _info(msg: str) -> None:
    print(f"  {INFO}  {msg}")


# ── 1. Prerequisite checks ────────────────────────────────────────────────────

def check_python_version() -> bool:
    major, minor = sys.version_info[:2]
    if major == 3 and minor >= 11:
        _ok(f"Python {major}.{minor}")
        return True
    _fail(f"Python {major}.{minor} — requires 3.11+")
    return False


def check_packages() -> bool:
    all_ok = True
    for pkg in REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            _ok(f"package: {pkg}")
        except ImportError:
            _fail(f"package: {pkg}  (run: pip install -r requirements.txt)")
            all_ok = False
    return all_ok


def check_ollama_model() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            _fail("ollama list failed — is Ollama running?")
            return False, ""
        lines = result.stdout.lower()
        if PREFERRED_MODEL.lower() in lines:
            _ok(f"Ollama model: {PREFERRED_MODEL}")
            return True, PREFERRED_MODEL
        if FALLBACK_MODEL.lower() in lines:
            _warn(f"Preferred model {PREFERRED_MODEL} not found; using {FALLBACK_MODEL}")
            return True, FALLBACK_MODEL
        _fail(
            f"Neither {PREFERRED_MODEL} nor {FALLBACK_MODEL} found.\n"
            f"       Run: ollama pull {FALLBACK_MODEL}"
        )
        return False, ""
    except FileNotFoundError:
        _fail("ollama not found in PATH — install from https://ollama.com")
        return False, ""
    except subprocess.TimeoutExpired:
        _fail("ollama list timed out — is Ollama running?")
        return False, ""


def check_demo_files() -> bool:
    all_ok = True
    for name in DEMO_FILES:
        path = DEMO_DATA_DIR / name
        if not path.exists():
            _fail(f"demo file missing: {path.relative_to(REPO_ROOT)}")
            all_ok = False
            continue
        if path.suffix == ".xml":
            try:
                from lxml import etree as ET
                ET.parse(str(path))
                _ok(f"demo file OK (XML parses): {name}")
            except Exception as exc:
                _fail(f"demo file XML parse error: {name} — {exc}")
                all_ok = False
        else:
            _ok(f"demo file present: {name}")
    return all_ok


def check_config_file() -> bool:
    if CONFIG_FILE.exists():
        _ok(f"config file: {CONFIG_FILE.relative_to(REPO_ROOT)}")
        return True
    _fail(f"config file missing: {CONFIG_FILE.relative_to(REPO_ROOT)}")
    return False


# ── 2. MCP server health check ────────────────────────────────────────────────

async def _check_mcp_server(module_path: str) -> tuple[str, bool, list[str]]:
    server_name = module_path.split(".")[-2]
    try:
        sys.path.insert(0, str(REPO_ROOT))
        mod = importlib.import_module(module_path)
        mcp_obj = getattr(mod, "mcp", None)
        if mcp_obj is None:
            return server_name, False, []
        tools = await mcp_obj.list_tools()
        tool_names = [t.name for t in tools]
        return server_name, True, tool_names
    except Exception as exc:
        return server_name, False, [str(exc)]


async def check_mcp_servers() -> bool:
    all_ok = True
    for module_path in MCP_SERVERS_TO_CHECK:
        name, ok, tools = await _check_mcp_server(module_path)
        if ok:
            _ok(f"MCP {name}: {len(tools)} tools — {', '.join(tools)}")
        else:
            _fail(f"MCP {name}: {tools[0] if tools else 'unknown error'}")
            all_ok = False
    return all_ok


# ── 3. Server startup ─────────────────────────────────────────────────────────

def start_server(port: int) -> subprocess.Popen | None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", str(port),
             "--reload", "--log-level", "info"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _info(f"uvicorn started (PID {proc.pid}) on port {port} — waiting for readiness...")
        return proc
    except Exception as exc:
        _fail(f"Failed to start uvicorn: {exc}")
        return None


def wait_for_server(port: int, timeout: float = 30.0) -> bool:
    import urllib.request
    url = f"http://localhost:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ── 4. Print demo summary ─────────────────────────────────────────────────────

def print_summary(port: int, model: str) -> None:
    print()
    print(_c("1;36", "=" * 62))
    print(_c("1;36", "  GRAEM PoC 1 — S1000D Co-Author  READY"))
    print(_c("1;36", "=" * 62))
    print(f"  UI:      http://localhost:{port}")
    print(f"  API:     http://localhost:{port}/docs")
    print(f"  Model:   {model}")
    print(f"  Data:    {DEMO_DATA_DIR.relative_to(REPO_ROOT)} ({len(DEMO_FILES)} files)")
    print()
    print(_c("33", "  Demo script: docs/DEMO_SCRIPT.md"))
    print(_c("1;36", "=" * 62))


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prepare GRAEM demo environment")
    p.add_argument("--no-browser", action="store_true", help="Skip browser open")
    p.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    p.add_argument("--skip-mcp-check", action="store_true",
                   help="Skip MCP server health check (faster startup)")
    return p.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    print(_c("1;36", "\nGREAM PoC 1 — Demo Preparation"))
    print(_c("36", f"  Repo: {REPO_ROOT}"))

    # ── Step 1: Prerequisites ──
    _step("1/4  Prerequisite checks")
    prereq_ok = True
    prereq_ok &= check_python_version()
    prereq_ok &= check_packages()
    model_ok, model = check_ollama_model()
    prereq_ok &= model_ok
    prereq_ok &= check_demo_files()
    prereq_ok &= check_config_file()

    if not prereq_ok:
        print()
        _fail("Prerequisites failed — fix issues above and re-run.")
        return 1

    # ── Step 2: MCP health ──
    if not args.skip_mcp_check:
        _step("2/4  MCP server health check")
        mcp_ok = await check_mcp_servers()
        if not mcp_ok:
            print()
            _fail("One or more MCP servers failed health check.")
            _info("Run with --skip-mcp-check to bypass (fallback: native tools only).")
            return 2
    else:
        _step("2/4  MCP server health check  [SKIPPED]")

    # ── Step 3: Start FastAPI server ──
    _step("3/4  Starting FastAPI server")
    proc = start_server(args.port)
    if proc is None:
        return 3

    if wait_for_server(args.port):
        _ok(f"Server is up at http://localhost:{args.port}")
    else:
        _warn("Server did not respond to /health in 30 s — it may still be starting.")
        _info("Check uvicorn output in the terminal where it was launched.")

    # ── Step 4: Open browser ──
    _step("4/4  Opening browser")
    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        try:
            webbrowser.open(url)
            _ok(f"Browser opened: {url}")
        except Exception as exc:
            _warn(f"Could not open browser: {exc}")
            _info(f"Open manually: {url}")
    else:
        _info(f"--no-browser set. Open manually: {url}")

    print_summary(args.port, model)
    return 0


def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(_async_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
