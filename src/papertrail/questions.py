"""Persist a question before work, then run one bounded fixed QA pipeline."""

import logging
import time
from dataclasses import replace
from uuid import UUID

from papertrail.budget import Budget, CallLedger
from papertrail.repository import Repository

logger = logging.getLogger(__name__)


class QuestionService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def run(self, question_id: UUID, paper_id: UUID, question: str) -> None:
        self._run(question_id, paper_id, question, kind="qa")

    def run_introduction(self, question_id: UUID, paper_id: UUID) -> None:
        from papertrail.introduction import INTRODUCTION_QUESTION

        self._run(question_id, paper_id, INTRODUCTION_QUESTION, kind="introduction")

    def _run(self, question_id: UUID, paper_id: UUID, question: str, *, kind: str) -> None:
        from papertrail.introduction import INTRODUCTION_MAX_OUTPUT_TOKENS, introduce_paper
        from papertrail.model import ModelClient, ModelConfig
        from papertrail.qa import answer_question

        started = time.monotonic()
        ledger = CallLedger(self.repository, question_id, Budget.from_env())
        result = {
            "status": "failed",
            "claims": [],
            "error_code": "internal_error",
            "message": "处理未能完成，任务已保存。请查看本地服务状态后主动重试。",
        }
        try:
            self.repository.progress_question(question_id, "retrieving")
            paper = self.repository.get(paper_id)
            pages = self.repository.pages(paper_id)
            config = ModelConfig.from_env()
            if kind == "introduction" and config.configured:
                config = replace(
                    config,
                    max_output_tokens=max(config.max_output_tokens, INTRODUCTION_MAX_OUTPUT_TOKENS),
                )
            client = ModelClient(
                config,
                before_call=ledger.before_call,
                record_call=ledger.record_call,
            )

            def progress(stage):
                self.repository.progress_question(question_id, stage)

            if kind == "introduction":
                result = introduce_paper(paper, pages, client=client, progress=progress)
            else:
                result = answer_question(paper, pages, question, client=client, progress=progress)
        except Exception as error:
            # No raw provider text, question content or credentials in application logs.
            logger.error("Question failed: %s", type(error).__name__)
        finally:
            try:
                snapshot = ledger.snapshot()
            except Exception as error:
                logger.error("Call ledger snapshot unavailable: %s", type(error).__name__)
                snapshot = {"status": "unavailable", "cost_status": "unknown"}
            result.setdefault("trace", {})["ledger"] = snapshot
            result["trace"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
            try:
                self.repository.finish_question(question_id, result)
            except Exception as error:
                logger.error("Question result save failed: %s", type(error).__name__)
                # A malformed provider value must not keep the global task slot forever.
                # If the DB remains unavailable, startup / 5-minute expiry recovers it.
                try:
                    self.repository.finish_question(
                        question_id,
                        {
                            "status": "failed",
                            "claims": [],
                            "error_code": "result_save_failed",
                            "message": "结果保存失败，原问题已保留。请检查本地服务后主动重试。",
                            "trace": {"cost_status": "unknown"},
                        },
                    )
                except Exception as final_error:
                    logger.error("Question recovery deferred: %s", type(final_error).__name__)
