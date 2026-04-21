"""
Log streaming API routes.

Provides a Server-Sent Events stream of the application log file.
Uses stdlib only: open(), os, asyncio.sleep() — no new packages.
"""

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.config import get_app_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["Logs"])

_LOG_FILE: Path = get_app_data_dir() / "logs" / "app.log"
_MAX_TAIL_LINES = 100   # lines sent on initial connection
_POLL_INTERVAL = 1.0    # seconds between file checks


@router.get(
    "/stream",
    summary="Stream application log file via SSE",
)
async def stream_logs():
    """
    Stream new log lines in real-time using Server-Sent Events.

    On connect, sends the last _MAX_TAIL_LINES lines from the log file,
    then follows the file and streams any new lines as they appear.
    """
    async def generate():
        file_pos = 0
        try:
            # Send tail of existing log content on connect
            if _LOG_FILE.exists():
                with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                    tail = all_lines[-_MAX_TAIL_LINES:]
                    file_pos = f.tell()
                for line in tail:
                    line = line.rstrip("\n")
                    if line:
                        yield f"data: {line}\n\n"

            # Follow new lines
            while True:
                await asyncio.sleep(_POLL_INTERVAL)
                if not _LOG_FILE.exists():
                    continue
                try:
                    size = os.path.getsize(_LOG_FILE)
                except OSError:
                    continue

                if size < file_pos:
                    # Log was truncated or rotated — reset to beginning
                    file_pos = 0

                if size > file_pos:
                    with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(file_pos)
                        new_content = f.read()
                        file_pos = f.tell()
                    for line in new_content.splitlines():
                        if line.strip():
                            yield f"data: {line}\n\n"

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Log stream error: {e}")
            yield f"data: [Log stream error: {e}]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
