#!/usr/bin/env python3
"""
PyInstaller build script for GraphRagExec.

This script packages the application into a single executable (.exe) file
that can run on Windows 11 without requiring Python installation.

Usage:
    python build.py             # Build with default settings
    python build.py --onefile   # Build as single file (default)
    python build.py --debug     # Include debug information

Requirements:
    - PyInstaller >= 6.4.0
    - All application dependencies installed

Output:
    - dist/GraphRagExec.exe (single file executable)
    - dist/GraphRagExec/ (directory mode, if --onedir specified)
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.absolute()


# def find_data_files() -> list[tuple[str, str]]:
#     """
#     Find data files that need to be included in the build.

#     Returns:
#         List of (source, destination) tuples for --add-data.
#     """
#     root = get_project_root()
#     data_files = []

#     # Include static files (HTML, CSS, JS)
#     static_dir = root / "app" / "static"
#     if static_dir.exists():
#         sep = ";" if sys.platform == "win32" else ":"
#         data_files.append((str(static_dir), f"app{sep}static"))

#     return data_files

def find_data_files() -> list[tuple[str, str]]:
    root = get_project_root()
    data_files = []

    static_dir = root / "app" / "static"
    if static_dir.exists():
        # DON'T use 'sep' here. Use a forward slash for the internal folder structure.
        data_files.append((str(static_dir), "app/static")) 

    return data_files


def find_hidden_imports() -> list[str]:
    """
    Get list of hidden imports that PyInstaller might miss.

    Returns:
        List of module names to include.
    """
    return [
        # FastAPI and dependencies
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

        # Pydantic
        "pydantic",
        "pydantic_settings",
        "pydantic.deprecated.decorator",

        # Jinja2 for templates
        "jinja2",

        # ChromaDB
        "chromadb",
        "chromadb.config",
        "chromadb.api",
        "chromadb.api.types",
        "chromadb.db",
        "chromadb.db.impl",
        "chromadb.db.impl.sqlite",
        "sqlite3",
        "hnswlib",

        # Kùzu
        "kuzu",

        # OpenAI client
        "openai",
        "openai.resources",

        # Document processing
        "pypdf",
        "docx",
        "openpyxl",
        "markdown",

        # HTTP client
        "httpx",
        "httpx._transports",
        "httpx._transports.default",

        # Multipart handling
        "multipart",
        "python_multipart",

        # Async file handling
        "aiofiles",

        # Standard library extensions
        "encodings",
        "encodings.utf_8",
        "encodings.ascii",
        "encodings.latin_1",
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

    # Single file or directory mode
    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    # Console or windowed
    if console:
        cmd.append("--console")
    else:
        cmd.append("--windowed")

    # Debug mode
    if debug:
        cmd.append("--debug=all")
    else:
        cmd.append("--strip")

    # Add hidden imports
    for module in find_hidden_imports():
        cmd.extend(["--hidden-import", module])

    # Add data files (static assets)
    for src, dst in find_data_files():
        sep = ";" if sys.platform == "win32" else ":"
        cmd.extend(["--add-data", f"{src}{sep}{dst}"])

    # Collect all submodules for complex packages
    collect_packages = [
        "chromadb",
        "kuzu",
        "uvicorn",
        "fastapi",
        "pydantic",
        "openai",
        "pypdf",
        "docx",
        "openpyxl",
    ]

    for package in collect_packages:
        cmd.extend(["--collect-all", package])

    # Copy metadata for packages that need it
    copy_metadata = [
        "chromadb",
        "openai",
        "httpx",
        "tqdm",
        "requests",
        "packaging",
    ]

    for package in copy_metadata:
        cmd.extend(["--copy-metadata", package])

    cmd.extend([
        "--log-level", "INFO" if not debug else "DEBUG",
    ])

    return cmd


def clean_build_dirs() -> None:
    """Remove previous build artifacts."""
    root = get_project_root()
    dirs_to_clean = ["build", "dist", "__pycache__"]
    files_to_clean = ["GraphRagExec.spec"]

    for dir_name in dirs_to_clean:
        dir_path = root / dir_name
        if dir_path.exists():
            print(f"Removing {dir_path}...")
            shutil.rmtree(dir_path)

    for file_name in files_to_clean:
        file_path = root / file_name
        if file_path.exists():
            print(f"Removing {file_path}...")
            file_path.unlink()


def build(
    onefile: bool = True,
    debug: bool = False,
    clean: bool = True
) -> bool:
    """Build the executable."""
    root = get_project_root()

    print("=" * 60)
    print("GraphRagExec Build Script")
    print("=" * 60)

    if clean:
        print("\nCleaning previous builds...")
        clean_build_dirs()

    entry_point = root / "run.py"
    if not entry_point.exists():
        print(f"ERROR: Entry point not found: {entry_point}")
        return False

    cmd = get_pyinstaller_command(onefile=onefile, debug=debug)

    print("\nPyInstaller command:")
    print(" ".join(cmd[:10]) + " ...")

    print("\nStarting build process...")
    print("-" * 60)

    try:
        subprocess.run(cmd, cwd=str(root), check=True, capture_output=False)
    except subprocess.CalledProcessError as e:
        print(f"\nBuild FAILED with exit code {e.returncode}")
        return False
    except FileNotFoundError:
        print("\nERROR: PyInstaller not found. Install with: pip install pyinstaller")
        return False

    print("-" * 60)

    if onefile:
        exe_path = root / "dist" / "GraphRagExec.exe"
        if sys.platform != "win32":
            exe_path = root / "dist" / "GraphRagExec"
    else:
        exe_path = root / "dist" / "GraphRagExec" / "GraphRagExec.exe"
        if sys.platform != "win32":
            exe_path = root / "dist" / "GraphRagExec" / "GraphRagExec"

    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\nBuild SUCCESSFUL!")
        print(f"Executable: {exe_path}")
        print(f"Size: {size_mb:.1f} MB")
        return True
    else:
        print(f"\nBuild completed but executable not found at {exe_path}")
        return False


def main() -> int:
    """Main entry point for build script."""
    parser = argparse.ArgumentParser(
        description="Build GraphRagExec executable",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--onefile",
        action="store_true",
        default=True,
        help="Build as single file (default)"
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Build as directory instead of single file"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Include debug information"
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Don't clean build directories"
    )

    args = parser.parse_args()
    onefile = not args.onedir

    success = build(
        onefile=onefile,
        debug=args.debug,
        clean=not args.no_clean
    )

    if success:
        print("\n" + "=" * 60)
        print("BUILD COMPLETE")
        print("=" * 60)
        print("""
Next steps:
1. Test the executable: dist/GraphRagExec.exe
2. Open in browser: http://127.0.0.1:8000
3. Configure AI API in Settings (gear icon)
4. Data is stored in %APPDATA%/GraphRagExec/
5. API documentation: http://127.0.0.1:8000/docs

Note: You need to configure the AI API settings
before importing documents. Default is Ollama at localhost:11434
        """)
        return 0
    else:
        print("\nBuild failed. Check the output above for errors.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
