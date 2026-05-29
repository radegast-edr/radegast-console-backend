from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import auth, teams, devices, packs, logs, admin, groups


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create upload directory
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    # Initialize database
    await init_db()
    yield


app = FastAPI(
    title="Radegast EDR",
    description="EDR management platform backend",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(teams.router)
app.include_router(devices.router)
app.include_router(groups.router)
app.include_router(packs.router)
app.include_router(logs.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.1", port=8000)
