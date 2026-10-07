from core.config import load_config


def test_defaults_need_no_paid_keys(cfg):
    assert cfg["llm"]["prep"]["backend"] == "claude-cli"
    assert cfg["llm"]["answer"]["backend"] == "ollama"
    assert cfg["llm"]["trigger"]["backend"] == "ollama"
    assert cfg["stt"]["backend"] == "faster-whisper"


def test_overrides_deep_merge():
    cfg = load_config(overrides={"mode": "engaged", "trigger": {"cooldown_s": 10}})
    assert cfg["mode"] == "engaged"
    assert cfg["trigger"]["cooldown_s"] == 10
    assert cfg["trigger"]["interval_s"] == 15  # untouched sibling kept


def test_paths_resolved(cfg):
    from pathlib import Path

    assert Path(cfg["paths"]["prompts_dir"]).is_absolute()
