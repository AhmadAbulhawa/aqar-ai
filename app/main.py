import os
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routes.furniture_routes import router as furniture_router
from app.routes.design_routes import router as design_router
from app.routes.room_routes import router as room_router


# Windows does not consistently register these browser-safe image MIME types.
# StaticFiles otherwise serves validated catalog images as octet-stream.
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")


app = FastAPI(
    title="Aqar AI Backend",
    version="0.3.0"
)


frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/uploads",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "uploads"),
    name="uploads",
)


app.include_router(furniture_router)
app.include_router(design_router)
app.include_router(room_router)


init_db()


@app.get("/")
def home():
    return {
        "message": "Aqar AI Backend is running"
    }


@app.head("/")
def home_head():
    return Response(status_code=200)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.head("/health")
def health_head():
    return Response(status_code=200)
