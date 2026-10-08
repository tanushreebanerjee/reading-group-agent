"""Pluggable LLM backends behind one small interface.

Every backend implements:
    complete(system, user, *, json_mode=False, max_tokens=None, temperature=None) -> str
    stream(system, user, **kw) -> Iterator[str]

Switching backends is a config change: make_llm(cfg["llm"]["answer"]).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Iterator

import requests


class LLMError(RuntimeError):
    pass


class LLM:
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.model = cfg.get("model", "")
        self.default_max_tokens = int(cfg.get("max_tokens", 512))
        self.default_temperature = float(cfg.get("temperature", 0.2))

    def _params(self, max_tokens, temperature):
        return (
            self.default_max_tokens if max_tokens is None else max_tokens,
            self.default_temperature if temperature is None else temperature,
        )

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 max_tokens: int | None = None, temperature: float | None = None) -> str:
        raise NotImplementedError

    def stream(self, system: str, user: str, **kw) -> Iterator[str]:
        yield self.complete(system, user, **kw)

    def __repr__(self):
        return f"<{self.name}:{self.model}>"


class OllamaLLM(LLM):
    name = "ollama"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        import ollama

        self.client = ollama.Client(host=os.environ.get("OLLAMA_HOST") or cfg.get("host"))

    def _options(self, max_tokens, temperature):
        mt, temp = self._params(max_tokens, temperature)
        return {"num_ctx": int(self.cfg.get("num_ctx", 8192)), "num_predict": mt, "temperature": temp}

    def _messages(self, system, user):
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def complete(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        try:
            r = self.client.chat(
                model=self.model,
                messages=self._messages(system, user),
                format="json" if json_mode else None,
                options=self._options(max_tokens, temperature),
                keep_alive=self.cfg.get("keep_alive", "30m"),
            )
        except Exception as e:  # connection refused, model missing, ...
            raise LLMError(f"ollama ({self.model}): {e}") from e
        return r["message"]["content"]

    def stream(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        try:
            for part in self.client.chat(
                model=self.model,
                messages=self._messages(system, user),
                format="json" if json_mode else None,
                options=self._options(max_tokens, temperature),
                keep_alive=self.cfg.get("keep_alive", "30m"),
                stream=True,
            ):
                chunk = part["message"]["content"]
                if chunk:
                    yield chunk
        except Exception as e:
            raise LLMError(f"ollama ({self.model}): {e}") from e


class ClaudeCLILLM(LLM):
    """Shells out to `claude -p` (headless Claude Code) on the developer's login.

    Slow (seconds to tens of seconds per call). Fine for prep; occasional live answers only.
    """

    name = "claude-cli"

    def complete(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        exe = shutil.which("claude")
        if not exe:
            raise LLMError("claude CLI not found on PATH")
        if json_mode:
            system += "\n\nRespond with a single JSON object and nothing else."
        cmd = [exe, "-p", "--output-format", "text", "--no-session-persistence",
               "--system-prompt", system, "--tools", ""]
        if self.model:
            cmd += ["--model", self.model]
        try:
            r = subprocess.run(cmd, input=user, capture_output=True, text=True,
                               timeout=float(self.cfg.get("timeout_s", 600)))
        except subprocess.TimeoutExpired as e:
            raise LLMError("claude -p timed out") from e
        if r.returncode != 0:
            raise LLMError(f"claude -p failed ({r.returncode}): {r.stderr.strip()[:500]}")
        return r.stdout.strip()


def _require_env(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        raise LLMError(f"{var} is not set (add it to .env)")
    return val


class GeminiLLM(LLM):
    """Google AI Studio free tier. UNTESTED in the first pass."""

    name = "gemini"

    def complete(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        key = _require_env("GEMINI_API_KEY")
        mt, temp = self._params(max_tokens, temperature)
        gen = {"maxOutputTokens": mt, "temperature": temp}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        r = requests.post(url, params={"key": key}, json=body, timeout=120)
        if r.status_code != 200:
            raise LLMError(f"gemini {r.status_code}: {r.text[:300]}")
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)


class AnthropicLLM(LLM):
    """Paid API, for later. Uses prompt caching on the system block. UNTESTED in the first pass."""

    name = "anthropic"

    def complete(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        key = _require_env("ANTHROPIC_API_KEY")
        mt, temp = self._params(max_tokens, temperature)
        if json_mode:
            system += "\n\nRespond with a single JSON object and nothing else."
        body = {
            "model": self.model,
            "max_tokens": mt,
            "temperature": temp,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json=body, timeout=120,
        )
        if r.status_code != 200:
            raise LLMError(f"anthropic {r.status_code}: {r.text[:300]}")
        return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


class OpenAILLM(LLM):
    """OpenAI-compatible chat completions: OpenAI itself, or any server with the same API
    (Groq, Cerebras, OpenRouter, a vLLM box) via `base_url` and `api_key_env`.

    The presets below fill in base_url and api_key_env for known providers, so
    `backend: groq` is shorthand for `backend: openai` pointed at Groq.
    """

    name = "openai"
    PRESETS = {
        "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
        "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
        "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
        "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    }

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        preset = cfg.get("backend", "openai")
        base_url, key_env = self.PRESETS.get(preset, self.PRESETS["openai"])
        self.name = preset if preset in self.PRESETS else "openai"
        self.base_url = (cfg.get("base_url") or base_url).rstrip("/")
        self.key_env = cfg.get("api_key_env") or key_env
        self.timeout = float(cfg.get("timeout_s", 60))

    def _request(self, system, user, json_mode, max_tokens, temperature, stream):
        key = _require_env(self.key_env)
        mt, temp = self._params(max_tokens, temperature)
        body = {
            "model": self.model,
            "max_tokens": mt,
            "temperature": temp,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": stream,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                              json=body, timeout=self.timeout, stream=stream)
        except requests.RequestException as e:
            raise LLMError(f"{self.name} ({self.model}): {e}") from e
        if r.status_code != 200:
            raise LLMError(f"{self.name} {r.status_code}: {r.text[:300]}")
        return r

    def complete(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        r = self._request(system, user, json_mode, max_tokens, temperature, stream=False)
        return r.json()["choices"][0]["message"]["content"] or ""

    def stream(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        r = self._request(system, user, json_mode, max_tokens, temperature, stream=True)
        try:
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                choices = json.loads(data).get("choices") or [{}]
                chunk = (choices[0].get("delta") or {}).get("content")
                if chunk:
                    yield chunk
        except requests.RequestException as e:
            raise LLMError(f"{self.name} ({self.model}): stream broke: {e}") from e


class FallbackLLM(LLM):
    """Try `primary`; on any backend error use `fallback` (e.g. Groq -> local Ollama).

    After a failure the primary is skipped for `retry_after_s`, so a rate limit or an
    outage costs one failed request, not one per call. A stream that fails after it
    has started yielding text is not retried (the display already shows part of it).
    """

    name = "fallback"

    def __init__(self, primary: LLM, fallback: LLM, retry_after_s: float = 60.0, clock=None, log=None):
        super().__init__(primary.cfg)
        self.primary, self.fallback = primary, fallback
        self.retry_after_s = retry_after_s
        self.clock = clock or __import__("time").monotonic
        self.log = log or (lambda msg: print(msg, flush=True))
        self.skip_until = 0.0
        self.last_used: LLM = primary

    def _use_primary(self) -> bool:
        return self.clock() >= self.skip_until

    def _failed(self, e: Exception) -> None:
        self.skip_until = self.clock() + self.retry_after_s
        self.log(f"[llm] {self.primary!r} failed ({e}); using {self.fallback!r} "
                 f"for the next {self.retry_after_s:.0f}s")

    def complete(self, system, user, **kw):
        if self._use_primary():
            try:
                out = self.primary.complete(system, user, **kw)
                self.last_used = self.primary
                return out
            except LLMError as e:
                self._failed(e)
        self.last_used = self.fallback
        return self.fallback.complete(system, user, **kw)

    def stream(self, system, user, **kw):
        if self._use_primary():
            started = False
            try:
                for piece in self.primary.stream(system, user, **kw):
                    started = True
                    self.last_used = self.primary
                    yield piece
                return
            except LLMError as e:
                if started:
                    raise
                self._failed(e)
        self.last_used = self.fallback
        yield from self.fallback.stream(system, user, **kw)

    def __repr__(self):
        return f"<{self.primary!r} -> {self.fallback!r}>"


class FakeLLM(LLM):
    """Deterministic backend for tests and offline smoke runs.

    cfg["responses"] may be a list (returned in order, last one repeats) or a string.
    """

    name = "fake"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        resp = cfg.get("responses", "The paper doesn't say.")
        self.responses = [resp] if isinstance(resp, str) else list(resp)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, *, json_mode=False, max_tokens=None, temperature=None):
        self.calls.append((system, user))
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        out = self.responses[idx]
        return json.dumps(out) if isinstance(out, dict) else out

    def stream(self, system, user, **kw):
        text = self.complete(system, user, **kw)
        for word in text.split(" "):
            yield word + " "


BACKENDS = {
    "ollama": OllamaLLM,
    "claude-cli": ClaudeCLILLM,
    "gemini": GeminiLLM,
    "anthropic": AnthropicLLM,
    "openai": OpenAILLM,
    "groq": OpenAILLM,
    "cerebras": OpenAILLM,
    "openrouter": OpenAILLM,
    "fake": FakeLLM,
}


def same_resource(a: dict, b: dict) -> bool:
    """True if two role configs run on the same local server (so their calls contend)."""
    local = ("ollama",)
    if a.get("backend") not in local or b.get("backend") not in local:
        return False
    return (a.get("host") or os.environ.get("OLLAMA_HOST")) == (b.get("host") or os.environ.get("OLLAMA_HOST"))


def make_llm(cfg: dict) -> LLM:
    """Build the backend for one role. If cfg has a `fallback` section (another role-style
    config), wrap it so errors fall through to that backend, e.g. a free cloud API that
    falls back to local Ollama when it is rate-limited, offline, or missing its key."""
    backend = cfg.get("backend")
    if backend not in BACKENDS:
        raise ValueError(f"unknown LLM backend {backend!r}; choose from {sorted(BACKENDS)}")
    primary = BACKENDS[backend]({k: v for k, v in cfg.items() if k != "fallback"})
    fb = cfg.get("fallback")
    if not fb:
        return primary
    return FallbackLLM(primary, make_llm(fb), retry_after_s=float(cfg.get("retry_after_s", 60)))
