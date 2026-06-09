import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
import uvicorn

from app.config import settings
from app.database import init_db
from app.middleware.request_logging import RequestLoggingMiddleware
from app.routers import admin, auth, apikeys, devices, exclusions, groups, install, logs, packs, releases, teams, ui
from app.services.email import process_email_queue_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create upload directory
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.releases_dir).mkdir(parents=True, exist_ok=True)
    # Initialize database
    await init_db()

    email_task = None
    if settings.enable_email_worker:
        email_task = asyncio.create_task(process_email_queue_loop())

    try:
        yield
    finally:
        if email_task:
            email_task.cancel()
            try:
                await email_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="Radegast EDR",
    description="EDR management platform backend",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.rate_limits = defaultdict(list)

# CORS
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
api_version = "1"

# Routers
app.include_router(prefix=f"/api/v{api_version}", router=auth.router)
app.include_router(prefix=f"/api/v{api_version}", router=teams.router)
app.include_router(prefix=f"/api/v{api_version}", router=devices.router)
app.include_router(prefix=f"/api/v{api_version}", router=install.install_router)
app.include_router(prefix=f"/api/v{api_version}", router=groups.router)
app.include_router(prefix=f"/api/v{api_version}", router=exclusions.router)
app.include_router(prefix=f"/api/v{api_version}", router=packs.router)
app.include_router(prefix=f"/api/v{api_version}", router=apikeys.router)
app.include_router(prefix=f"/api/v{api_version}", router=logs.router)
app.include_router(prefix=f"/api/v{api_version}", router=admin.router)
app.include_router(prefix=f"/api/v{api_version}", router=releases.router)
app.include_router(prefix="/ui", router=ui.router)
app.add_middleware(RequestLoggingMiddleware)


@app.get(f"/api/v{api_version}/health")
async def health():
    return {"status": "ok"}


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    file_favicon = Path("web") / "static" / "favicon.ico"
    if not file_favicon.exists():
        file_favicon = ui.dir_web / "favicon.ico"
    if not file_favicon.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_favicon, media_type="image/x-icon")


@app.get('/')
async def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/", status_code=302)


@app.get('/.well-known/security.txt')
async def security_txt() -> FileResponse:
    file_security = Path(__file__).parent.parent / "security.txt"
    if not file_security.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_security, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
