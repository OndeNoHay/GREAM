#!/usr/bin/env python3
"""
Entry point for GraphRagExec Local AI Server.

This script starts the FastAPI server using uvicorn.
It is designed to work both as a Python script and as a PyInstaller executable.

Usage:
    python run.py                    # Development mode
    python run.py --port 8080        # Custom port
    GraphRagExec.exe                 # Production executable

Environment Variables:
    GRAPHRAGEXEC_HOST: Server host (default: 127.0.0.1)
    GRAPHRAGEXEC_PORT: Server port (default: 8000)
    GRAPHRAGEXEC_DEBUG: Enable debug mode (default: false)
"""

import argparse
import logging
import multiprocessing
import sys
from typing import NoReturn

import uvicorn

from app.config import get_app_settings, log_startup_info, is_frozen


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="GraphRagExec - Local AI Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                    Start with default settings
  python run.py --port 8080        Start on port 8080
  python run.py --debug            Enable debug mode

Environment Variables:
  GRAPHRAGEXEC_HOST     Server host binding
  GRAPHRAGEXEC_PORT     Server port
  GRAPHRAGEXEC_DEBUG    Enable debug logging
        """
    )

    settings = get_app_settings()

    parser.add_argument(
        "--host",
        type=str,
        default=settings.host,
        help=f"Host to bind to (default: {settings.host})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.port,
        help=f"Port to listen on (default: {settings.port})"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=settings.debug,
        help="Enable debug mode with auto-reload"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)"
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0"
    )

    return parser.parse_args()


def configure_logging(debug: bool = False) -> None:
    """
    Configure logging based on debug mode.

    Args:
        debug: Enable debug-level logging.
    """
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)


def main() -> NoReturn:
    """
    Main entry point for the server.

    Parses arguments, configures logging, and starts the uvicorn server.
    """
    # Required for PyInstaller on Windows with multiprocessing
    multiprocessing.freeze_support()

    args = parse_args()
    configure_logging(args.debug)

    logger = logging.getLogger(__name__)

    # Log startup configuration
    log_startup_info()
    logger.info(f"Starting server on {args.host}:{args.port}")

    if is_frozen():
        logger.info("Running as PyInstaller executable")
    else:
        logger.info("Running as Python script")

    # Configure uvicorn
    config = uvicorn.Config(
        app="app.main:app",
        host=args.host,
        port=args.port,
        reload=args.debug and not is_frozen(),  # No reload for .exe
        workers=args.workers if not args.debug else 1,
        log_level="debug" if args.debug else "info",
        access_log=args.debug,
        # Disable reload when frozen (PyInstaller)
        reload_dirs=None if is_frozen() else ["app"],
    )

    server = uvicorn.Server(config)

    try:
        logger.info(f"Server starting at http://{args.host}:{args.port}")
        logger.info("Press Ctrl+C to stop the server")
        server.run()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
