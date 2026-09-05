"""One cancellable OpenAI-compatible JSON call, without retries or tool access."""

import asyncio
import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx2 as httpx


class ModelError(Exception):
    """Only these safe messages may cross the API boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_json(value: object) -> bool:
    """PostgreSQL JSONB rejects NUL, unpaired surrogates and nonfinite numbers."""
    if isinstance(value, str):
        return "\0" not in value and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_safe_json(key) and _safe_json(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_safe_json(item) for item in value)
    return True


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model_name: str = ""
    timeout: float = 45.0
    max_output_tokens: int = 1800
    thinking: str = ""

    @classmethod
    def from_env(cls) -> "ModelConfig":
        try:
            timeout = float(os.getenv("PAPERTRAIL_MODEL_TIMEOUT", "45"))
            maximum = int(os.getenv("PAPERTRAIL_MODEL_MAX_OUTPUT_TOKENS", "1800"))
            if not math.isfinite(timeout):
                timeout = -1
        except (ValueError, TypeError):
            timeout, maximum = -1, -1
        return cls(
            base_url=os.getenv("PAPERTRAIL_MODEL_BASE_URL", "").strip().rstrip("/"),
            api_key=os.getenv("PAPERTRAIL_MODEL_API_KEY", "").strip(),
            model_name=os.getenv("PAPERTRAIL_MODEL_NAME", "").strip(),
            timeout=timeout,
            max_output_tokens=maximum,
            thinking=os.getenv("PAPERTRAIL_MODEL_THINKING", "").strip().lower(),
        )

    @property
    def model(self) -> str:
        return self.model_name

    @property
    def configured(self) -> bool:
        try:
            endpoint = urlsplit(self.base_url)
            port = endpoint.port
            return bool(
                endpoint.hostname
                and (port is None or 1 <= port <= 65535)
                and _safe_json(self.base_url)
                and (
                    endpoint.scheme == "https"
                    or (
                        endpoint.scheme == "http"
                        and endpoint.hostname in {"127.0.0.1", "localhost"}
                    )
                )
                and not endpoint.username
                and not endpoint.password
                and not endpoint.query
                and not endpoint.fragment
                and self.api_key
                and self.model_name
                and _safe_json(self.model_name)
                and math.isfinite(self.timeout)
                and 0 < self.timeout <= 180
                and 64 <= self.max_output_tokens <= 16_000
                and self.thinking in {"", "enabled", "disabled"}
            )
        except ValueError:
            return False

    def public(self) -> dict:
        """No credentials, full URL, or provider error body is returned."""
        endpoint = urlsplit(self.base_url) if self.configured else None
        origin = (
            f"{endpoint.scheme}://{endpoint.hostname}"
            + (f":{endpoint.port}" if endpoint.port else "")
            if endpoint
            else None
        )
        return {
            "configured": self.configured,
            "model_name": self.model_name if _safe_json(self.model_name) else None,
            "timeout_seconds": self.timeout if math.isfinite(self.timeout) else None,
            "max_output_tokens": self.max_output_tokens,
            "temperature": 0,
            "thinking": self.thinking
            if self.thinking in {"enabled", "disabled"}
            else "provider_default",
            "response_format": "json_object",
            "provider_origin": origin,
            "endpoint_sha256": hashlib.sha256(self.base_url.encode(errors="replace")).hexdigest(),
        }


class ModelClient:
    def __init__(
        self,
        config: ModelConfig,
        *,
        transport=None,
        before_call: Callable[[dict], None] | None = None,
        record_call: Callable[[dict], None] | None = None,
    ):
        self.config = config
        self.transport = transport
        self.before_call = before_call
        self.record_call = record_call
        self.calls: list[dict] = []

    async def _request(self, payload: dict, timeout: float) -> dict:
        # wait_for is a total deadline; HTTP timeouts alone measure inactivity.
        async def receive():
            async with httpx.AsyncClient(
                transport=self.transport, timeout=timeout, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    self.config.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code >= 400 or response.is_redirect:
                        raise ModelError(
                            "model_failure", "模型服务拒绝请求，请检查配置或稍后重试。"
                        )
                    body = bytearray()
                    async for block in response.aiter_bytes():
                        body.extend(block)
                        if len(body) > 2_000_000:
                            raise ModelError(
                                "invalid_output", "模型响应过大，请减少输出上限后重试。"
                            )
                    try:
                        parsed = json.loads(body)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise ModelError(
                            "invalid_output", "模型服务未返回有效 JSON 响应。"
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise ModelError("invalid_output", "模型响应格式无效，请重试。")
                    return parsed

        return await asyncio.wait_for(receive(), timeout=timeout)

    def complete_json(
        self, stage: str, messages: list[dict], *, deadline: float | None = None
    ) -> dict:
        if not self.config.configured:
            raise ModelError("model_not_configured", "请先在本地 .env 配置模型服务、名称和密钥。")
        timeout = (
            min(self.config.timeout, deadline - time.monotonic())
            if deadline
            else (self.config.timeout)
        )
        if timeout <= 0:
            raise ModelError("model_timeout", "问答处理超时，请缩短问题后重试。")
        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.config.max_output_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if self.config.thinking:
            payload["thinking"] = {"type": self.config.thinking}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        system = json.dumps(
            [m for m in messages if m["role"] == "system"], ensure_ascii=False, sort_keys=True
        ).encode()
        call = {
            "stage": stage,
            "model": self.config.model_name,
            "request_sha256": hashlib.sha256(encoded).hexdigest(),
            "prompt_sha256": hashlib.sha256(system).hexdigest(),
            # UTF-8 bytes conservatively bound tokenizer tokens, plus framing space.
            "input_token_upper_bound": len(encoded) + 256 * len(messages) + 256,
            "max_output_tokens": self.config.max_output_tokens,
            "usage": None,
            "cost": None,
            "cost_status": "unknown",
            "status": "pending",
        }
        if self.before_call:
            self.before_call(dict(call))
        started = time.monotonic()
        self.calls.append(call)
        try:
            raw = asyncio.run(self._request(payload, timeout))
            usage = raw.get("usage")
            if isinstance(usage, dict):
                # Store numerical usage only, never arbitrary provider fields.
                call["usage"] = {
                    key: value
                    for key, value in usage.items()
                    if key
                    in {
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "prompt_cache_hit_tokens",
                        "prompt_cache_miss_tokens",
                    }
                    and type(value) is int
                    and value >= 0
                }
            if not _safe_json(raw):
                raise ModelError("invalid_output", "模型响应包含无法保存的字符或数值，请重试。")
            if isinstance(raw.get("model"), str):
                call["returned_model"] = raw["model"][:200]
            try:
                choice = raw["choices"][0]
                if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
                    raise ValueError("invalid choice")
                if choice.get("finish_reason") != "stop":
                    raise ValueError("incomplete response")
                content = choice["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("content is not a string")
                call["response_sha256"] = hashlib.sha256(content.encode()).hexdigest()
                result = json.loads(content)
                if not isinstance(result, dict) or not _safe_json(result):
                    raise ValueError("JSON object required")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ModelError("invalid_output", "模型未返回完整的结构化结果，请重试。") from exc
            call["status"] = "succeeded"
            return result
        except (TimeoutError, httpx.TimeoutException) as exc:
            call["status"] = "failed"
            call["error_code"] = "model_timeout"
            raise ModelError("model_timeout", "模型请求超时，问题已保留，可稍后重试。") from exc
        except httpx.HTTPError as exc:
            call["status"] = "failed"
            call["error_code"] = "model_failure"
            raise ModelError(
                "model_failure", "无法连接模型服务，请检查本地配置和网络后重试。"
            ) from exc
        except ModelError as exc:
            call["status"] = "failed"
            call["error_code"] = exc.code
            raise
        finally:
            call["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            if self.record_call:
                self.record_call(dict(call))
