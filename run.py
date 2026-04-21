#!/usr/bin/env python3
"""
Entry point for GraphRagExec Local AI Server.
"""

import argparse
import logging
import multiprocessing
import sys
import threading
import time
import webbrowser
from typing import NoReturn

import uvicorn

# CRITICAL: Import the app object directly. 
# This forces PyInstaller to find and bundle the 'app' module.
from app.main import app as fastapi_app
from app.config import get_app_settings, log_startup_info, is_frozen


def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(description="GraphRagExec - Local AI Server")
    settings = get_app_settings()

    parser.add_argument("--host", type=str, default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--debug", action="store_true", default=settings.debug)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")

    return parser.parse_args()


def configure_logging(debug: bool = False) -> None:
    """
    Configure logging based on debug mode.
    """
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def main() -> NoReturn:
    """
    Main entry point for the server.
    """
    # Required for PyInstaller on Windows with multiprocessing
    multiprocessing.freeze_support()

    args = parse_args()
    configure_logging(args.debug)

    logger = logging.getLogger(__name__)

    # Log startup configuration
    log_startup_info()
    
    if is_frozen():
        logger.info("Running as PyInstaller executable")
    else:
        logger.info("Running as Python script")

    # Configure uvicorn
    config = uvicorn.Config(
        app=fastapi_app,         # Use the imported object, NOT the string "app.main:app"
        host=args.host,
        port=args.port,
        reload=args.debug and not is_frozen(),
        workers=args.workers if not args.debug else 1,
        log_level="debug" if args.debug else "info",
        access_log=args.debug,
        reload_dirs=None if is_frozen() else ["app"],
    )

    server = uvicorn.Server(config)

    def open_browser():
        while not server.started:
            time.sleep(0.1)
        webbrowser.open(f"http://{args.host}:{args.port}")

    threading.Thread(target=open_browser, daemon=True).start()

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