"""HTTP entry point for the PaperTrail development baseline."""

from importlib.metadata import version
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="PaperTrail API",
    version=version("papertrail"),
    description="论文研读助手的开发接口。当前仅提供进程存活检查。",
)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@app.get("/health", tags=["system"])
def health() -> HealthResponse:
    """Report process liveness; this does not check storage or model availability."""
    return HealthResponse()
