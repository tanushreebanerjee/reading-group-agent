import asyncio

import pytest

from assistant import settings as S
from display.server import Hub, origin_ok


def test_coerce_ranges_and_choices():
    assert S.coerce(S.BY_KEY["answer.max_sentences"], "4") == 4
    assert S.coerce(S.BY_KEY["trigger.thresholds.gap"], "0.65") == 0.65
    with pytest.raises(ValueError, match="between"):
        S.coerce(S.BY_KEY["trigger.thresholds.gap"], 1.5)
    with pytest.raises(ValueError, match="number"):
        S.coerce(S.BY_KEY["trigger.cooldown_s"], "soon")
    with pytest.raises(ValueError, match="choose"):
        S.coerce(S.BY_KEY["mode"], "loud")


def test_paths():
    cfg = {"trigger": {"thresholds": {"gap": 0.7}}}
    S.set_path(cfg, "trigger.thresholds.gap", 0.5)
    assert S.get_path(cfg, "trigger.thresholds.gap") == 0.5
    assert S.get_path(cfg, "answer.retrieval_k") is None


def test_model_options_hide_cloud_without_key():
    cfg = {"model_presets": {"Groq A": {"backend": "groq", "model": "a"},
                             "Claude": {"backend": "claude-cli", "model": "sonnet"},
                             "Local 7b": {"backend": "ollama", "model": "qwen2.5:7b"}}}
    no_key = S.model_options(cfg, ["qwen2.5:7b", "llama3:8b"], env={})
    assert "Groq A" not in no_key and "Claude" in no_key
    assert "Local · llama3:8b" in no_key and "Local · qwen2.5:7b" not in no_key  # already a preset
    assert "Groq A" in S.model_options(cfg, [], env={"GROQ_API_KEY": "k"})


def test_role_config_keeps_role_params_unless_preset_sets_them():
    base = {"backend": "ollama", "model": "x", "temperature": 0.0, "max_tokens": 200, "num_ctx": 8192}
    assert S.role_config({"backend": "groq", "model": "m"}, base) == \
        {"backend": "groq", "model": "m", "temperature": 0.0, "max_tokens": 200}
    assert S.role_config({"backend": "groq", "model": "m", "max_tokens": 1500}, base)["max_tokens"] == 1500


def test_describe():
    assert S.describe({"backend": "groq", "model": "qwen/qwen3.8-27b"}) == "qwen/qwen3.8-27b (groq)"
    assert S.describe({"backend": "ollama", "model": "qwen2.5:7b"}) == "qwen2.5:7b (local)"


def test_origin_check():
    assert origin_ok(None)
    assert origin_ok("http://127.0.0.1:8765") and origin_ok("http://localhost:8765")
    assert not origin_ok("https://evil.example.com")


def test_hub_replays_settings_without_error():
    hub = Hub()
    asyncio.run(hub.send({"type": "settings", "fields": [], "error": "bad"}))
    assert hub.state["settings"] == {"type": "settings", "fields": []}
