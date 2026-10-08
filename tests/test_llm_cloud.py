import json

import pytest

from core import llm as llm_mod
from core.llm import FallbackLLM, LLMError, OpenAILLM, make_llm


class Broken(llm_mod.LLM):
    name = "broken"

    def __init__(self, after=0):
        super().__init__({"model": "x"})
        self.calls, self.after = 0, after

    def complete(self, system, user, **kw):
        self.calls += 1
        raise LLMError("429 rate limited")

    def stream(self, system, user, **kw):
        self.calls += 1
        for i in range(self.after):
            yield f"p{i} "
        raise LLMError("connection reset")


def fallback_pair(primary, t):
    fb = make_llm({"backend": "fake", "responses": "from fallback"})
    return FallbackLLM(primary, fb, retry_after_s=60, clock=lambda: t[0], log=lambda m: None), fb


def test_fallback_on_error_then_skips_primary_until_retry():
    t = [0.0]
    broken = Broken()
    f, fb = fallback_pair(broken, t)
    assert f.complete("s", "u") == "from fallback"
    assert f.complete("s", "u") == "from fallback"
    assert broken.calls == 1            # primary skipped during the retry window
    t[0] = 61
    f.complete("s", "u")
    assert broken.calls == 2            # retried after the window


def test_stream_falls_back_only_before_first_token():
    t = [0.0]
    f, _ = fallback_pair(Broken(after=0), t)
    assert "".join(f.stream("s", "u")).strip() == "from fallback"
    f2, _ = fallback_pair(Broken(after=2), t)
    with pytest.raises(LLMError):
        list(f2.stream("s", "u"))


def test_missing_key_falls_back(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    f = make_llm({"backend": "groq", "model": "m",
                  "fallback": {"backend": "fake", "responses": "local"}})
    assert isinstance(f, FallbackLLM)
    f.log = lambda m: None
    assert f.complete("s", "u") == "local"
    assert f.last_used is f.fallback


def test_groq_preset():
    g = make_llm({"backend": "groq", "model": "llama-3.3-70b-versatile"})
    assert isinstance(g, OpenAILLM)
    assert g.base_url == "https://api.groq.com/openai/v1" and g.key_env == "GROQ_API_KEY"
    o = make_llm({"backend": "openai", "model": "m", "base_url": "http://box:8000/v1/", "api_key_env": "K"})
    assert o.base_url == "http://box:8000/v1" and o.key_env == "K"


class FakeResp:
    def __init__(self, status=200, lines=(), body=None):
        self.status_code, self._lines, self._body, self.text = status, lines, body, "err"
        self.encoding = "ISO-8859-1"

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:  # mimic requests: decode raw UTF-8 bytes with self.encoding
            yield line.encode("utf-8").decode(self.encoding)

    def json(self):
        return self._body


def test_openai_stream_parses_sse(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    sent = {}
    lines = ["", ": keep-alive",
             "data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}}]}),
             "data: " + json.dumps({"choices": [{"delta": {"content": "Table "}}]}),
             "data: " + json.dumps({"choices": [{"delta": {"content": "3 doesn’t."}}]}, ensure_ascii=False),
             "data: [DONE]"]

    def post(url, headers, json, timeout, stream):
        sent.update(url=url, auth=headers["Authorization"], body=json)
        return FakeResp(lines=lines)

    monkeypatch.setattr(llm_mod.requests, "post", post)
    g = make_llm({"backend": "groq", "model": "m"})
    assert "".join(g.stream("sys", "user", json_mode=True)) == "Table 3 doesn’t."
    assert sent["url"].endswith("/chat/completions") and sent["auth"] == "Bearer k"
    assert sent["body"]["stream"] and sent["body"]["response_format"] == {"type": "json_object"}


def test_openai_http_error_is_llmerror(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **kw: FakeResp(status=429))
    with pytest.raises(LLMError, match="429"):
        make_llm({"backend": "groq", "model": "m"}).complete("s", "u")


def test_same_resource():
    from core.llm import same_resource
    ol = {"backend": "ollama", "model": "qwen2.5:7b"}
    assert same_resource(ol, {"backend": "ollama", "model": "other"})
    assert not same_resource({"backend": "groq", "model": "m", "fallback": ol}, ol)
    assert not same_resource(ol, {"backend": "ollama", "host": "http://gpu:11434"})


def test_retry_wait_parsing():
    from core.llm import retry_wait_s

    class R:
        def __init__(self, headers=None, text=""):
            self.headers, self.text = headers or {}, text

    assert retry_wait_s(R({"retry-after": "3"})) == 3
    assert abs(retry_wait_s(R(text="Please try again in 4.268571428s. Need more")) - 4.2686) < 1e-3
    assert retry_wait_s(R(text="try again in 1m2.5s")) == 62.5
    assert retry_wait_s(R(text="try again in 250ms")) == 0.25
    assert retry_wait_s(R(text="nope")) is None


def test_short_429_is_retried_in_place(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    calls = []

    def post(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            r = FakeResp(status=429); r.text = "try again in 2.1s"; r.headers = {}
            return r
        return FakeResp(body={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_mod.requests, "post", post)
    assert make_llm({"backend": "groq", "model": "m"}).complete("s", "u") == "ok"
    assert len(calls) == 2
    # a long wait is not retried: it raises so the fallback can take over
    calls.clear()
    def post_long(*a, **kw):
        calls.append(1)
        r = FakeResp(status=429); r.text = "try again in 40s"; r.headers = {}
        return r
    monkeypatch.setattr(llm_mod.requests, "post", post_long)
    with pytest.raises(LLMError):
        make_llm({"backend": "groq", "model": "m"}).complete("s", "u")
    assert len(calls) == 1


def test_claude_cli_never_sees_api_key_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    llm = make_llm({"backend": "claude-cli", "model": "opus"})
    assert "ANTHROPIC_API_KEY" not in llm.env()
    assert make_llm({"backend": "claude-cli", "use_api_key": True}).env()["ANTHROPIC_API_KEY"] == "sk-should-not-leak"


def test_fallback_waits_only_as_long_as_rate_limit_asks():
    t = [0.0]

    class Limited(Broken):
        def complete(self, system, user, **kw):
            self.calls += 1
            raise LLMError("429", retry_after=10)

    lim = Limited()
    f, _ = fallback_pair(lim, t)
    f.complete("s", "u")
    t[0] = 12            # past the 10 s (+1) the provider asked for, well before retry_after_s=60
    f.complete("s", "u")
    assert lim.calls == 2


def test_fallback_chain_three_levels():
    t = [0.0]
    cfg = {"backend": "groq", "model": "a",
           "fallback": {"backend": "groq", "model": "b",
                        "fallback": {"backend": "fake", "responses": "local"}}}
    import os
    os.environ.pop("GROQ_API_KEY", None)
    f = make_llm(cfg)
    f.log = f.fallback.log = lambda m: None
    assert f.complete("s", "u") == "local"
    assert f.fallback.last_used is f.fallback.fallback


def test_empty_thinking_answer_falls_through(monkeypatch):
    """Hidden reasoning that uses all max_tokens returns no text: fall back, don't show a blank."""
    from core.llm import served_by
    monkeypatch.setenv("GROQ_API_KEY", "k")
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": ""}, "finish_reason": "length"}]}),
             "data: [DONE]"]
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **kw: FakeResp(lines=lines))
    f = make_llm({"backend": "groq", "model": "m", "fallback": {"backend": "fake", "responses": "fallback answer"}})
    f.log = lambda m: None
    assert "".join(f.stream("s", "u")).strip() == "fallback answer"
    assert served_by(f) is f.fallback
    assert f.skip_until <= f.clock() + 1.5      # not a rate limit: retry the primary next time
