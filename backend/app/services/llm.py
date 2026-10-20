"""The one door to the language model (self-hosted, e.g. Qwen3.5).

Talks to any OpenAI-compatible chat endpoint (vLLM, Ollama, llama.cpp server), so the model is
changed in ``.env`` without touching email processing, the database or the dashboard.

- Every answer must be JSON matching a pydantic schema; anything else counts as a failure.
- Timeouts, a retry, a limit on parallel requests, and a circuit breaker: after repeated
  failures the model is not called for a while and emails simply go to people.
- Statistics (task, model, latency, ok) are stored; prompts and answers never are, and
  nothing from the email is logged.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import ModelCall

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

BREAKER_FAILURES = 3
BREAKER_COOL_DOWN = 120.0  # seconds
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class ModelUnavailable(Exception):
    """The model could not give a valid answer. The message is safe to show staff."""


@dataclass
class ModelAnswer[T]:
    data: T
    model: str
    latency_ms: int


class LanguageModel(Protocol):
    enabled: bool

    def ask(
        self,
        db: Session | None,
        task: str,
        system: str,
        user: str,
        schema: type[T],
        *,
        strong: bool = False,
        email_id=None,
    ) -> ModelAnswer[T]: ...

    def cached_health(self, max_age: float = 30) -> tuple[bool, str]:
        """health(), re-checked at most every ``max_age`` seconds (dashboards poll often)."""
        now = time.monotonic()
        if now - self._health_at > max_age:
            self._health, self._health_at = self.health(), now
        return self._health

    def health(self) -> tuple[bool, str]: ...


def _parse_json(text: str) -> dict:
    text = _THINK.sub("", text).strip()
    text = _FENCE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start : end + 1])


class OpenAICompatibleModel:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.enabled = settings.llm_enabled
        self._s = settings
        self._http = http or httpx.Client(timeout=settings.llm_timeout_seconds)
        self._slots = threading.BoundedSemaphore(max(1, settings.llm_max_concurrency))
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0
        self._health: tuple[bool, str] = (False, "Not checked yet")
        self._health_at = -1e9
        try:
            self._extra = json.loads(settings.llm_extra_body) if settings.llm_extra_body.strip() else {}
        except json.JSONDecodeError as exc:
            raise ValueError("FNB_LLM_EXTRA_BODY is not valid JSON") from exc

    # -- circuit breaker -----------------------------------------------------------------------

    def _check_breaker(self) -> None:
        with self._lock:
            if time.monotonic() < self._open_until:
                raise ModelUnavailable("The AI model is paused after repeated failures; retrying shortly")

    def _record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures = 0
                return
            self._failures += 1
            if self._failures >= BREAKER_FAILURES:
                self._open_until = time.monotonic() + BREAKER_COOL_DOWN
                log.error(
                    "AI model failed %d times in a row; pausing calls for %ss",
                    self._failures,
                    int(BREAKER_COOL_DOWN),
                )

    @property
    def breaker_open(self) -> bool:
        return time.monotonic() < self._open_until

    # -- calls ----------------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._s.llm_api_key}"} if self._s.llm_api_key else {}

    def _chat(self, model: str, system: str, user: str, schema: type[BaseModel]) -> str:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            **self._extra,
        }
        acquired = self._slots.acquire(timeout=self._s.llm_timeout_seconds)
        if not acquired:
            raise ModelUnavailable("The AI model is busy")
        try:
            resp = self._http.post(
                self._s.llm_base_url.rstrip("/") + "/chat/completions", json=body, headers=self._headers()
            )
        finally:
            self._slots.release()
        if resp.status_code != 200:
            raise ModelUnavailable(f"The AI model returned HTTP {resp.status_code}")
        return resp.json()["choices"][0]["message"]["content"] or ""

    def ask(self, db, task, system, user, schema, *, strong=False, email_id=None):
        if not self.enabled:
            raise ModelUnavailable("The AI model is not switched on")
        model = self._s.llm_drafting_model if strong else self._s.llm_fast_model
        self._check_breaker()
        started = time.monotonic()
        error: str | None = None
        try:
            for attempt in range(self._s.llm_max_retries + 1):
                try:
                    raw = self._chat(model, system, user, schema)
                    data = schema.model_validate(_parse_json(raw))
                    self._record(True)
                    return ModelAnswer(data, model, int((time.monotonic() - started) * 1000))
                except (ValidationError, ValueError, KeyError, IndexError) as exc:
                    error = "invalid_output"  # wrong shape: ask once more, then give up
                    log.warning(
                        "AI model gave an invalid %s answer (attempt %d): %s",
                        task,
                        attempt + 1,
                        type(exc).__name__,
                    )
                except httpx.TimeoutException:
                    error = "timeout"
                except httpx.HTTPError as exc:
                    error = type(exc).__name__
                except ModelUnavailable as exc:
                    error = str(exc)[:120]
            self._record(False)
            raise ModelUnavailable(f"The AI model could not answer ({error})")
        finally:
            if db is not None:
                db.add(
                    ModelCall(
                        task=task,
                        model=model[:120],
                        ok=error is None,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        error=error,
                        email_id=email_id,
                    )
                )

    def cached_health(self, max_age: float = 30) -> tuple[bool, str]:
        """health(), re-checked at most every ``max_age`` seconds (dashboards poll often)."""
        now = time.monotonic()
        if now - self._health_at > max_age:
            self._health, self._health_at = self.health(), now
        return self._health

    def health(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Not switched on (FNB_LLM_ENABLED)"
        try:
            resp = self._http.get(
                self._s.llm_base_url.rstrip("/") + "/models", headers=self._headers(), timeout=5
            )
        except httpx.HTTPError as exc:
            return False, f"Cannot reach the model server ({type(exc).__name__})"
        if resp.status_code != 200:
            return False, f"Model server returned HTTP {resp.status_code}"
        served = {m.get("id") for m in resp.json().get("data", [])}
        wanted = {self._s.llm_fast_model, self._s.llm_drafting_model}
        missing = sorted(m for m in wanted if m and m not in served)
        if missing:
            return False, "Model server is up but does not serve: " + ", ".join(missing)
        if self.breaker_open:
            return False, "Paused after repeated failures"
        return True, "Serving " + ", ".join(sorted(m for m in wanted if m))


_instance: OpenAICompatibleModel | None = None
_instance_lock = threading.Lock()


def get_model(settings: Settings) -> OpenAICompatibleModel:
    """One shared client per process (keeps the circuit breaker and the parallel-request limit)."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = OpenAICompatibleModel(settings)
        return _instance
