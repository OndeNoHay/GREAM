"""
Viewer API — sirve ficheros del directorio output/ para el XML viewer.

Solo permite acceso a ficheros en output/ (sin path traversal).
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/viewer", tags=["Viewer"])

# Raíz del proyecto (…/GREAM/)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
_OUTPUT_DIR = _PROJECT_ROOT / "output"


@router.get("/output/{filename}", summary="Serve a file from the output directory")
async def get_output_file(filename: str) -> FileResponse:
    """
    Devuelve un fichero del directorio output/.

    Solo se permite el nombre de fichero sin subdirectorios.
    Usado por xml_viewer.html para cargar el XML generado por el agente.
    """
    # Prevención de path traversal: solo nombre de fichero plano
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = (_OUTPUT_DIR / safe_name).resolve()

    # Verificar que la ruta resultante sigue dentro de output/
    if not str(file_path).startswith(str(_OUTPUT_DIR)):
        raise HTTPException(status_code=400, detail="Access denied")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File '{safe_name}' not found in output/")

    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        str(file_path),
        media_type=media_type or "application/octet-stream",
        filename=safe_name,
    )


@router.get("/output", summary="List files in the output directory")
async def list_output_files() -> dict:
    """Lista los ficheros disponibles en output/."""
    if not _OUTPUT_DIR.exists():
        return {"files": []}
    files = [
        {"name": f.name, "size_bytes": f.stat().st_size}
        for f in sorted(_OUTPUT_DIR.iterdir())
        if f.is_file()
    ]
    return {"files": files, "count": len(files)}
