"""HTTP contracts for the local paper library."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from papertrail.config import Settings
from papertrail.errors import ImportFailure
from papertrail.ingestion import Ingestion
from papertrail.repository import Repository

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class Paper(BaseModel):
    id: UUID
    filename: str
    sha256: str
    size_bytes: int
    page_count: int
    parser_version: str
    created_at: datetime


class PaperDetail(Paper):
    empty_pages: list[int]


class ImportResult(BaseModel):
    paper: Paper
    duplicate: bool


class Page(BaseModel):
    paper_id: UUID
    page_index: int
    text: str


class BodyTooLarge(Exception):
    pass


class UploadLimit:
    """Bound multipart bytes before Starlette spools them, including chunked requests."""

    def __init__(self, app, limit: int):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        used = 0

        async def limited_receive():
            nonlocal used
            message = await receive()
            used += len(message.get("body", b""))
            if used > self.limit:
                raise BodyTooLarge
            return message

        async def guarded_send(message):
            if used > self.limit:
                raise BodyTooLarge
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except BodyTooLarge:
            response = JSONResponse(
                {"error": {"code": "file_too_large", "message": "上传内容超过大小上限。"}},
                status_code=413,
            )
            await response(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = Repository(settings)
    ingestion = Ingestion(settings, repository)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for name in ("staging", "papers"):
            (settings.data_dir / name).mkdir(parents=True, exist_ok=True)
        repository.migrate()
        yield

    app = FastAPI(
        title="PaperTrail API",
        version=version("papertrail"),
        description="单用户本地论文导入与逐页核对。页索引从 0 开始。",
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.ingestion = ingestion
    app.add_middleware(UploadLimit, limit=settings.max_upload_bytes + 1024 * 1024)

    @app.exception_handler(ImportFailure)
    async def import_failure(request: Request, exc: ImportFailure):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status
        )

    @app.exception_handler(psycopg.Error)
    async def database_failure(request: Request, exc: psycopg.Error):
        logger.error("Database operation failed: %s", type(exc).__name__)
        return JSONResponse(
            {
                "error": {
                    "code": "database_unavailable",
                    "message": "数据服务暂时不可用，请稍后重试。",
                }
            },
            status_code=503,
        )

    @app.exception_handler(OSError)
    async def storage_failure(request: Request, exc: OSError):
        logger.error("Local storage operation failed: %s", type(exc).__name__)
        return JSONResponse(
            {
                "error": {
                    "code": "storage_failure",
                    "message": "本地文件保存或读取失败，请检查磁盘空间。",
                }
            },
            status_code=500,
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health():
        return HealthResponse()

    @app.get("/api/config", tags=["system"])
    def config():
        return {"max_upload_bytes": settings.max_upload_bytes, "max_pages": settings.max_pages}

    @app.post("/api/papers", response_model=ImportResult, tags=["papers"])
    def upload(file: UploadFile, response: Response):
        paper, duplicate = ingestion.import_file(file.file, file.filename or "论文.pdf")
        response.status_code = 200 if duplicate else 201
        return {"paper": paper, "duplicate": duplicate}

    @app.get("/api/papers", response_model=list[Paper], tags=["papers"])
    def list_papers(offset: Annotated[int, Query(ge=0)] = 0):
        return repository.list(offset)

    @app.get("/api/papers/{paper_id}", response_model=PaperDetail, tags=["papers"])
    def get_paper(paper_id: UUID):
        paper = repository.get(paper_id)
        if paper is None:
            raise ImportFailure("not_found", "找不到这篇论文，请返回论文库。", 404)
        return paper

    @app.get("/api/papers/{paper_id}/pages/{page_index}", response_model=Page, tags=["papers"])
    def get_page(paper_id: UUID, page_index: int):
        page = repository.page(paper_id, page_index)
        if page is None:
            raise ImportFailure("page_not_found", "找不到这一页，请检查页码。", 404)
        return page

    @app.get("/api/papers/{paper_id}/file", tags=["papers"])
    def get_file(paper_id: UUID):
        paper = repository.get(paper_id)
        if paper is None:
            raise ImportFailure("not_found", "找不到这篇论文。", 404)
        path = repository.file_path(paper_id)
        if not path.is_file():
            raise ImportFailure("file_missing", "原 PDF 文件丢失，请检查本地数据目录。", 409)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=paper["filename"],
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
