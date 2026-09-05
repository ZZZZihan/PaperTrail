"""Persist conservative call reservations so restarts cannot reset the local allowance."""

import hashlib
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from papertrail.errors import ImportFailure
from papertrail.repository import Repository


@dataclass(frozen=True)
class Budget:
    limit: Decimal
    input_price: Decimal
    output_price: Decimal
    currency: str
    scope: str

    @classmethod
    def from_env(cls) -> "Budget | None":
        try:
            values = [
                Decimal(os.environ[key])
                for key in (
                    "PAPERTRAIL_MODEL_BUDGET",
                    "PAPERTRAIL_MODEL_INPUT_PRICE_PER_MILLION",
                    "PAPERTRAIL_MODEL_OUTPUT_PRICE_PER_MILLION",
                )
            ]
            if not all(value.is_finite() and value >= 0 for value in values):
                return None
            currency = os.environ["PAPERTRAIL_MODEL_CURRENCY"].strip().upper()
            if not currency or len(currency) > 12:
                return None
            # Scope is an explicit allowance name, independent of switching model or prices.
            scope = os.getenv("PAPERTRAIL_MODEL_BUDGET_SCOPE", "v01-development")
            return cls(*values, currency, hashlib.sha256(scope.encode()).hexdigest())
        except (KeyError, ValueError, InvalidOperation):
            return None

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (input_tokens * self.input_price + output_tokens * self.output_price) / 1_000_000


class CallLedger:
    def __init__(self, repository: Repository, question_id: UUID, budget: Budget | None):
        self.repository = repository
        self.question_id = question_id
        self.budget = budget
        self.current: UUID | None = None

    def before_call(self, metadata: dict) -> None:
        from papertrail.model import ModelError

        try:
            self.repository.require_service_guard()
        except ImportFailure as exc:
            raise ModelError(exc.code, exc.message) from exc
        if self.budget is None:
            raise ModelError(
                "budget_not_configured",
                "请在本地配置中填写获准预算、币种及输入/输出单价，重启后再试。",
            )
        budget = self.budget
        reserved = budget.cost(metadata["input_token_upper_bound"], metadata["max_output_tokens"])
        with self.repository.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(18091802)")
            task = conn.execute(
                "SELECT status FROM questions WHERE id = %s", (self.question_id,)
            ).fetchone()
            if task is None or task["status"] not in {"pending", "running"}:
                raise ModelError("interrupted", "问题处理已结束或中断，请刷新历史查看。")
            rows = conn.execute(
                "SELECT currency, COALESCE(SUM(COALESCE(actual_cost, reserved_cost)), 0) AS used "
                "FROM model_calls WHERE budget_scope = %s GROUP BY currency",
                (budget.scope,),
            ).fetchall()
            if any(row["currency"] != budget.currency for row in rows):
                raise ModelError(
                    "budget_currency_changed", "本轮预算币种与既有记录不同，请恢复配置。"
                )
            used = sum((row["used"] for row in rows), Decimal(0))
            if used + reserved > budget.limit:
                raise ModelError(
                    "budget_exceeded", "本轮剩余预算不足以预留下一次调用，请核对用量和预算后再试。"
                )
            self.current = uuid4()
            conn.execute(
                "INSERT INTO model_calls(id, question_id, budget_scope, currency, stage, "
                "reserved_cost, details) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    self.current,
                    self.question_id,
                    budget.scope,
                    budget.currency,
                    metadata["stage"],
                    reserved,
                    Jsonb(metadata),
                ),
            )

    def record_call(self, details: dict) -> None:
        if self.current is None or self.budget is None:
            return
        usage = details.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        actual = None
        if (
            isinstance(prompt_tokens, int)
            and not isinstance(prompt_tokens, bool)
            and isinstance(completion_tokens, int)
            and not isinstance(completion_tokens, bool)
            and min(prompt_tokens, completion_tokens) >= 0
        ):
            actual = self.budget.cost(prompt_tokens, completion_tokens)
        augmented = {
            **details,
            "estimated_cost": str(actual) if actual is not None else None,
            "currency": self.budget.currency,
            "cost_source": "configured_token_rates" if actual is not None else "unknown",
            "price_per_million": {
                "input": str(self.budget.input_price),
                "output": str(self.budget.output_price),
            },
        }
        details.update(augmented)
        with self.repository.connect() as conn:
            conn.execute(
                "UPDATE model_calls SET actual_cost = %s, details = %s, completed_at = now() "
                "WHERE id = %s",
                (actual, Jsonb(augmented), self.current),
            )
        self.current = None

    def snapshot(self) -> dict:
        with self.repository.connect() as conn:
            rows = conn.execute(
                "SELECT id, currency, stage, reserved_cost, actual_cost, details, created_at, "
                "completed_at FROM model_calls WHERE question_id = %s ORDER BY created_at",
                (self.question_id,),
            ).fetchall()
        return {
            "calls": [
                {
                    **row,
                    "id": str(row["id"]),
                    "reserved_cost": str(row["reserved_cost"]),
                    "actual_cost": str(row["actual_cost"])
                    if row["actual_cost"] is not None
                    else None,
                    "created_at": row["created_at"].isoformat(),
                    "completed_at": row["completed_at"].isoformat()
                    if row["completed_at"]
                    else None,
                }
                for row in rows
            ],
            "estimated_cost": str(
                sum(
                    (row["actual_cost"] for row in rows if row["actual_cost"] is not None),
                    Decimal(0),
                )
            ),
            "unknown_cost_calls": sum(row["actual_cost"] is None for row in rows),
            "currency": self.budget.currency if self.budget else None,
        }
