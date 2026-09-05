"""HTTP contracts for the local paper library."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import BackgroundTasks, FastAPI, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from papertrail.budget import Budget
from papertrail.config import Settings
from papertrail.errors import ImportFailure
from papertrail.ingestion import Ingestion
from papertrail.questions import QuestionService
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


class QuestionInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    request_id: UUID

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value):
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("问题不能包含空字符。")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("问题包含无效字符。") from exc
            return value.strip()
        return value


class Citation(BaseModel):
    chunk_id: str
    paper_id: UUID
    page_index: int
    quote: str


class Claim(BaseModel):
    text: str
    citations: list[Citation]


class IntroductionInput(BaseModel):
    request_id: UUID


class IntroductionTerm(BaseModel):
    term: str
    explanation: str
    citations: list[Citation]


class Introduction(BaseModel):
    summary: Claim
    problem: Claim
    contribution: Claim
    mechanism: Claim
    evidence_and_limits: Claim
    terms: list[IntroductionTerm]


class Question(BaseModel):
    id: UUID
    paper_id: UUID
    question: str
    status: Literal["pending", "running", "answered", "insufficient_evidence", "failed"]
    stage: str
    claims: list[Claim]
    message: str
    error_code: str | None
    support_status: str | None
    human_review: dict | None
    trace: dict
    created_at: datetime
    completed_at: datetime | None
    kind: Literal["qa", "introduction"] = "qa"
    introduction: Introduction | None = None


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
    questions = QuestionService(repository)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        guard = repository.connect() if settings.exclusive_service else None
        try:
            if guard is not None:
                guard.autocommit = True
                acquired = guard.execute(
                    "SELECT pg_try_advisory_lock(18091803) AS acquired"
                ).fetchone()["acquired"]
                if not acquired:
                    raise RuntimeError("PaperTrail 已有服务使用这个数据库，请先停止原服务。")
                repository.bind_service_guard(guard)
            for name in ("staging", "papers"):
                (settings.data_dir / name).mkdir(parents=True, exist_ok=True)
            repository.migrate()
            repository.recover_questions()
            yield
        finally:
            if guard is not None:
                guard.close()

    app = FastAPI(
        title="PaperTrail API",
        version=version("papertrail"),
        description="单用户本地论文证据问答。页索引从 0 开始。",
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.ingestion = ingestion
    app.state.questions = questions
    app.add_middleware(UploadLimit, limit=settings.max_upload_bytes + 1024 * 1024)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Do not echo malformed Unicode or arbitrarily large user input into JSON responses.
        return JSONResponse(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "请求参数无效。请检查文件、问题（1—2000 字）或页面标识后重试。",
                }
            },
            status_code=422,
        )

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
        from papertrail.model import ModelConfig

        model = ModelConfig.from_env()
        budget = Budget.from_env()
        configured = model.configured
        reason = None
        if not configured:
            reason = "请在 .env.local 配置模型服务、模型名称和密钥，再重启应用。"
        elif budget is None:
            reason = (
                "请在 .env.local 配置预算模式及对应额度："
                "金额与单价，或已获准配额的调用上限，再重启应用。"
            )
        return {
            "max_upload_bytes": settings.max_upload_bytes,
            "max_pages": settings.max_pages,
            "model": {
                "configured": configured and budget is not None,
                "model": model.model_name or None,
                "reason": reason,
            },
        }

    @app.post(
        "/api/papers/{paper_id}/questions",
        response_model=Question,
        status_code=202,
        tags=["questions"],
    )
    def ask_question(paper_id: UUID, body: QuestionInput, background: BackgroundTasks):
        row, created = repository.create_question(paper_id, body.request_id, body.question)
        if created:
            background.add_task(questions.run, row["id"], paper_id, body.question)
        return row

    @app.get("/api/papers/{paper_id}/questions", response_model=list[Question], tags=["questions"])
    def question_history(paper_id: UUID, offset: Annotated[int, Query(ge=0)] = 0):
        if repository.get(paper_id) is None:
            raise ImportFailure("not_found", "找不到这篇论文，请返回论文库。", 404)
        return repository.questions(paper_id, offset)

    @app.get(
        "/api/papers/{paper_id}/questions/{question_id}",
        response_model=Question,
        tags=["questions"],
    )
    def question_detail(paper_id: UUID, question_id: UUID):
        row = repository.question(paper_id, question_id)
        if row is None:
            raise ImportFailure("question_not_found", "当前论文下找不到这个问题。", 404)
        return row

    @app.post(
        "/api/papers/{paper_id}/introduction",
        response_model=Question,
        status_code=202,
        tags=["introductions"],
    )
    def generate_introduction(paper_id: UUID, body: IntroductionInput, background: BackgroundTasks):
        row, created = repository.create_introduction(paper_id, body.request_id)
        if created:
            background.add_task(questions.run_introduction, row["id"], paper_id)
        return row

    @app.get(
        "/api/papers/{paper_id}/introduction",
        response_model=Question | None,
        tags=["introductions"],
    )
    def paper_introduction(paper_id: UUID):
        if repository.get(paper_id) is None:
            raise ImportFailure("not_found", "找不到这篇论文，请返回论文库。", 404)
        return repository.introduction(paper_id)

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
