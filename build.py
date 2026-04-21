#!/usr/bin/env python3
"""
PyInstaller build script for GraphRagExec.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.absolute()


def find_data_files() -> list[tuple[str, str]]:
    """
    Find data files that need to be included in the build.
    """
    root = get_project_root()
    data_files = []

    # Include static files (HTML, CSS, JS)
    static_dir = root / "app" / "static"
    if static_dir.exists():
        data_files.append((str(static_dir), "app/static")) 

    return data_files


def find_hidden_imports() -> list[str]:
    """
    Get list of hidden imports that PyInstaller might miss.
    """
    return [
        "fastapi",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "pydantic",
        "pydantic_settings",
        "pydantic.deprecated.decorator",
        "jinja2",
        # ChromaDB & ONNX (The cause of the recent error)
        "chromadb",
        "chromadb.api.segment",
        "chromadb.telemetry.posthog",
        "onnxruntime",
        "tokenizers",
        "sqlite3",
        "hnswlib",
        "kuzu",
        "openai",
        "openai.resources",
        "pypdf",
        "docx",
        "openpyxl",
        "markdown",
        "httpx",
        "httpx._transports",
        "httpx._transports.default",
        "multipart",
        "python_multipart",
        "aiofiles",
        "encodings",
        "encodings.utf_8",
        "encodings.ascii",
        "encodings.latin_1",
        # Google Drive integration
        "googleapiclient",
        "googleapiclient.discovery",
        "googleapiclient.http",
        "googleapiclient._helpers",
        "google.auth",
        "google.auth.transport.requests",
        "google.oauth2.credentials",
        "google_auth_oauthlib",
        "google_auth_oauthlib.flow",
        # Agent framework (PydanticAI)
        "pydantic_ai",
        "pydantic_ai.agent",
        "pydantic_ai.tools",
        "pydantic_ai.mcp",
    ]


def get_pyinstaller_command(
    onefile: bool = True,
    debug: bool = False,
    console: bool = True
) -> list[str]:
    """
    Build the PyInstaller command with all necessary flags.
    """
    root = get_project_root()
    entry_point = root / "run.py"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(entry_point),
        "--name", "GraphRagExec",
        "--clean",
        "--noconfirm",
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    if console:
        cmd.append("--console")
    else:
        cmd.append("--windowed")

    if debug:
        cmd.append("--debug=all")
    else:
        cmd.append("--strip")

    for module in find_hidden_imports():
        cmd.extend(["--hidden-import", module])

    # Add data files
    for src, dst in find_data_files():
        sep = ";" if sys.platform == "win32" else ":"
        cmd.extend(["--add-data", f"{src}{sep}{dst}"])

    # Collect all submodules for complex packages
    collect_packages = [
        "app",
        "chromadb",
        "onnxruntime",  # <--- Added to ensure DLLs and models are included
        "tokenizers",   # <--- Often needed by ChromaDB
        "kuzu",
        "uvicorn",
        "fastapi",
        "pydantic",
        "openai",
        # Google Drive integration
        "googleapiclient",
        "google.auth",
        "google_auth_oauthlib",
    ]

    for package in collect_packages:
        cmd.extend(["--collect-all", package])

    copy_metadata = [
        "chromadb",
        "openai",
        "httpx",
        "tqdm",
        "requests",
        "packaging",
        "onnxruntime", # <--- Added
        "tokenizers",   # <--- Added
        # Google Drive integration
        "google-api-python-client",
        "google-auth",
        "google-auth-oauthlib",
    ]

    for package in copy_metadata:
        cmd.extend(["--copy-metadata", package])

    cmd.extend([
        "--log-level", "INFO" if not debug else "DEBUG",
    ])

    return cmd


def clean_build_dirs() -> None:
    root = get_project_root()
    dirs_to_clean = ["build", "dist", "__pycache__"]
    files_to_clean = ["GraphRagExec.spec"]

    for dir_name in dirs_to_clean:
        dir_path = root / dir_name
        if dir_path.exists():
            shutil.rmtree(dir_path)

    for file_name in files_to_clean:
        file_path = root / file_name
        if file_path.exists():
            file_path.unlink()


def build(onefile: bool = True, debug: bool = False, clean: bool = True) -> bool:
    root = get_project_root()
    print("=" * 60)
    print("GraphRagExec Build Script")
    print("=" * 60)

    if clean:
        clean_build_dirs()

    cmd = get_pyinstaller_command(onefile=onefile, debug=debug)
    
    try:
        subprocess.run(cmd, cwd=str(root), check=True)
    except subprocess.CalledProcessError:
        return False

    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onefile", action="store_true", default=True)
    parser.add_argument("--onedir", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-clean", action="store_true")

    args = parser.parse_args()
    onefile = not args.onedir

    success = build(onefile=onefile, debug=args.debug, clean=not args.no_clean)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())