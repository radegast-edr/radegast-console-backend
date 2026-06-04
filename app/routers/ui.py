from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["ui"])

# First check if app/web_build exists (as packaged in PyPI wheel)
dir_web = (Path(__file__).parent.parent / "web_build").resolve()
if not dir_web.exists():
    # Fallback to the development repository path
    dir_web = (Path(__file__).parent.parent.parent / "web" / "build").resolve()


@router.get("/{full_path:path}")
async def serve_ui(full_path: str) -> FileResponse:
    path = (dir_web / full_path).resolve()
    if not path.is_relative_to(dir_web):
        raise HTTPException(status_code=404, detail="Not found.")

    if path.is_file():
        return FileResponse(str(path))
    return FileResponse(str(dir_web / "index.html"))
