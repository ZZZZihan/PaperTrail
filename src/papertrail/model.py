"""One cancellable OpenAI-compatible JSON call, without retries or tool access."""

import asyncio
import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit

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
    base_url: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    model_name: str = ""
    timeout: float = 45.0
    max_output_tokens: int = 1800
    thinking: str = ""
    allow_http_origin: str = field(default="", repr=False)
    profile: str = "compatible"
    reasoning_effort: str = "none"

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
            allow_http_origin=os.getenv("PAPERTRAIL_MODEL_ALLOW_HTTP_ORIGIN", "").strip(),
            profile=os.getenv("PAPERTRAIL_MODEL_PROFILE", "compatible").strip().lower(),
        )

    @property
    def model(self) -> str:
        return self.model_name

    def _allows_http_endpoint(self, endpoint: SplitResult) -> bool:
        """An explicit origin permits exactly one HTTP scheme/host/effective-port tuple."""
        raw = self.allow_http_origin
        if (
            not raw
            or not _safe_json(raw)
            or any(ord(char) <= 32 or ord(char) == 127 for char in raw)
            or "?" in raw
            or "#" in raw
        ):
            return False
        allowed = urlsplit(raw)
        port = allowed.port
        return bool(
            allowed.scheme == endpoint.scheme == "http"
            and allowed.hostname
            and not any(char in allowed.hostname for char in "*,")
            and allowed.username is None
            and allowed.password is None
            and allowed.path in {"", "/"}
            and (port is None or 1 <= port <= 65535)
            and allowed.hostname == endpoint.hostname
            and (port or 80) == (endpoint.port or 80)
        )

    @property
    def configured(self) -> bool:
        try:
            endpoint = urlsplit(self.base_url)
            port = endpoint.port
            return bool(
                endpoint.hostname
                and (port is None or 1 <= port <= 65535)
                and _safe_json(self.base_url)
                and not any(ord(char) <= 32 or ord(char) == 127 for char in self.base_url)
                and (
                    endpoint.scheme == "https"
                    or (
                        endpoint.scheme == "http"
                        and (
                            endpoint.hostname in {"127.0.0.1", "localhost"}
                            or self._allows_http_endpoint(endpoint)
                        )
                    )
                )
                and endpoint.username is None
                and endpoint.password is None
                and "?" not in self.base_url
                and "#" not in self.base_url
                and self.api_key
                and self.model_name
                and _safe_json(self.model_name)
                and math.isfinite(self.timeout)
                and 0 < self.timeout <= 180
                and 64 <= self.max_output_tokens <= 16_000
                and self.thinking in {"", "enabled", "disabled"}
                and self.profile in {"compatible", "openai"}
                and (self.profile != "openai" or self.thinking == "")
                and isinstance(self.reasoning_effort, str)
                and self.reasoning_effort in {"none", "low"}
                and (self.profile == "openai" or self.reasoning_effort == "none")
            )
        except ValueError:
            return False

    def public(self) -> dict:
        """No credentials, full URL, or provider error body is returned."""
        endpoint = urlsplit(self.base_url) if self.configured else None
        host = (
            f"[{endpoint.hostname}]"
            if endpoint and ":" in endpoint.hostname
            else endpoint.hostname
            if endpoint
            else None
        )
        origin = (
            f"{endpoint.scheme}://{host}" + (f":{endpoint.port}" if endpoint.port else "")
            if endpoint
            else None
        )
        return {
            "configured": self.configured,
            "model_name": self.model_name if _safe_json(self.model_name) else None,
            "timeout_seconds": self.timeout if math.isfinite(self.timeout) else None,
            "max_output_tokens": self.max_output_tokens,
            "profile": self.profile if self.profile in {"compatible", "openai"} else "invalid",
            "output_token_parameter": "max_completion_tokens"
            if self.profile == "openai"
            else "max_tokens"
            if self.profile == "compatible"
            else None,
            "reasoning_effort": self.reasoning_effort
            if self.profile == "openai"
            and isinstance(self.reasoning_effort, str)
            and self.reasoning_effort in {"none", "low"}
            else None,
            "temperature": 0 if self.profile == "compatible" else None,
            "thinking": "not_sent"
            if self.profile == "openai"
            else self.thinking
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
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if self.config.profile == "openai":
            payload["max_completion_tokens"] = self.config.max_output_tokens
            payload["reasoning_effort"] = self.config.reasoning_effort
        else:
            payload["max_tokens"] = self.config.max_output_tokens
            payload["temperature"] = 0
            if self.config.thinking:
                payload["thinking"] = {"type": self.config.thinking}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        system = json.dumps(
            [m for m in messages if m["role"] == "system"], ensure_ascii=False, sort_keys=True
        ).encode()
        call = {
            "stage": stage,
            "model": self.config.model_name,
            "profile": self.config.profile,
            "output_token_parameter": self.config.public()["output_token_parameter"],
            "reasoning_effort": payload.get("reasoning_effort"),
            "temperature": payload.get("temperature"),
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
                completion_details = usage.get("completion_tokens_details")
                if isinstance(completion_details, dict):
                    reasoning_tokens = completion_details.get("reasoning_tokens")
                    if type(reasoning_tokens) is int and reasoning_tokens >= 0:
                        # A provider-reported subset of completion_tokens, never added
                        # to totals or priced again. Missing/invalid values stay unknown.
                        call["reasoning_tokens"] = reasoning_tokens
            if not _safe_json(raw):
                call["output_issue"] = "unsafe_response"
                raise ModelError("invalid_output", "模型响应包含无法保存的字符或数值，请重试。")
            if isinstance(raw.get("model"), str):
                call["returned_model"] = raw["model"][:200]
            output_issue = "invalid_response_shape"
            content = None
            try:
                choice = raw["choices"][0]
                output_issue = "invalid_choice"
                if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
                    raise ValueError("invalid choice")
                output_issue = "incomplete_response"
                if choice.get("finish_reason") != "stop":
                    raise ValueError("incomplete response")
                output_issue = "missing_content"
                content = choice["message"]["content"]
                output_issue = "non_string_content"
                if not isinstance(content, str):
                    raise ValueError("content is not a string")
                call["response_sha256"] = hashlib.sha256(content.encode()).hexdigest()
                output_issue = "invalid_json"
                result = json.loads(content)
                output_issue = "non_object"
                if not isinstance(result, dict):
                    raise ValueError("JSON object required")
                output_issue = "unsafe_json_value"
                if not _safe_json(result):
                    raise ValueError("JSON value cannot be persisted")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                call["output_issue"] = output_issue
                if isinstance(content, str):
                    stripped = content.strip()
                    call["output_shape"] = {
                        "content_chars": len(content),
                        "first_nonspace_codepoint": ord(stripped[0]) if stripped else None,
                        "last_nonspace_codepoint": ord(stripped[-1]) if stripped else None,
                    }
                if isinstance(exc, json.JSONDecodeError):
                    # The standard decoder's msg is a fixed parser description. Never save
                    # exc.doc, str(exc), snippets, or the provider's content in this record.
                    call["json_error"] = {
                        "lineno": exc.lineno,
                        "colno": exc.colno,
                        "pos": exc.pos,
                        "msg": exc.msg,
                    }
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
