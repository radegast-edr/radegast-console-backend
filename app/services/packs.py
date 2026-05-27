import os
import shutil
from pathlib import Path

import aiofiles

from app.config import settings


def get_upload_path(pack_id: int, version: str, filename: str) -> str:
    dir_path = Path(settings.upload_dir) / str(pack_id) / version
    dir_path.mkdir(parents=True, exist_ok=True)
    return str(dir_path / filename)


async def save_upload(content: bytes, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "wb") as f:
        await f.write(content)


def delete_pack_files(pack_id: int):
    dir_path = Path(settings.upload_dir) / str(pack_id)
    if dir_path.exists():
        shutil.rmtree(dir_path)
