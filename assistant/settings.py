"""Settings that can be changed mid-meeting from the control page (/control).

Changes apply to the running session only (config.yaml is not modified) and are
written to events.jsonl, so log.md records what was in effect when.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.llm import OpenAILLM

FROM_CONFIG = "(from config)"


@dataclass
class Field:
    key: str            # dotted config path, e.g. "trigger.cooldown_s"; "llm.answer" for model pickers
    label: str
    kind: str           # choice | int | float | model
    options: list = field(default_factory=list)
    min: float | None = None
    max: float | None = None
    help: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", [])}


FIELDS = [
    Field("mode", "Mode", "choice", ["ask", "engaged", "discuss"],
          help="engaged: raises a hand for corrections and unanswered questions; "
               "discuss: also for points that add to the current topic"),
    Field("llm.answer", "Answer model", "model", help="answers and raised-hand text"),
    Field("llm.interjection", "Raised-hand text model", "model",
          help="prepared in the background when a hand goes up, so a slower, more careful model is fine"),
    Field("llm.trigger", "Raised-hand checker model", "model",
          help="runs every few seconds in engaged mode; cloud free tiers may hit rate limits"),
    Field("voice.mode", "Speak", "choice", ["off", "answers", "answers+reveal"],
          help="answers: speak answers when asked; answers+reveal: also speak a raised hand when revealed"),
    Field("voice.backend", "Voice", "choice", ["kokoro", "say", "groq"],
          help="kokoro: local, natural; say: macOS, instant; groq: Orpheus (cloud, accept terms first)"),
    Field("answer.max_sentences", "Answer length (sentences)", "int", min=1, max=6),
    Field("answer.retrieval_k", "Paper excerpts per answer", "int", min=1, max=10,
          help="more = better recall, slower on local models"),
    Field("answer.context", "Paper context", "choice", ["retrieval", "full_paper"],
          help="full_paper sends the whole paper: only for large-context cloud models"),
    Field("answer.transcript_window_s", "Transcript context for answers (s)", "float", min=10, max=300),
    Field("question_pause_s", "Question ends after a pause of (s)", "float", min=0.5, max=5),
    Field("trigger.interval_s", "Raised-hand check interval (s)", "float", min=5, max=120),
    Field("trigger.cooldown_s", "Raised-hand cooldown (s)", "float", min=0, max=1800),
    Field("trigger.thresholds.contradiction", "Contradiction threshold", "float", min=0, max=1),
    Field("trigger.thresholds.gap", "Gap threshold", "float", min=0, max=1),
    Field("trigger.thresholds.point", "Point threshold (discuss mode)", "float", min=0, max=1),
]
BY_KEY = {f.key: f for f in FIELDS}


def get_path(cfg: dict, key: str):
    node = cfg
    for k in key.split("."):
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


def set_path(cfg: dict, key: str, value) -> None:
    *parents, leaf = key.split(".")
    node = cfg
    for k in parents:
        node = node.setdefault(k, {})
    node[leaf] = value


def key_available(preset: dict, env=os.environ) -> bool:
    """False if a cloud preset's API key is missing (so the picker doesn't offer it)."""
    backend = preset.get("backend")
    if backend in OpenAILLM.PRESETS:
        var = preset.get("api_key_env") or OpenAILLM.PRESETS[backend][1]
        return bool(env.get(var))
    if backend == "gemini":
        return bool(env.get("GEMINI_API_KEY"))
    if backend == "anthropic":
        return bool(env.get("ANTHROPIC_API_KEY"))
    return True


def model_options(cfg: dict, ollama_models: list[str], env=os.environ) -> dict[str, dict]:
    """Label -> role config for the model pickers: the presets in config.yaml whose API keys
    are present, plus every model pulled into local Ollama. (Each role also gets a
    FROM_CONFIG option for whatever config.yaml/config.local.yaml chose.)"""
    opts: dict[str, dict] = {}
    for label, preset in (cfg.get("model_presets") or {}).items():
        if key_available(preset, env):
            opts[label] = dict(preset)
    known = {(p.get("backend"), p.get("model")) for p in opts.values()}
    for m in ollama_models:
        if ("ollama", m) not in known:
            opts[f"Local · {m}"] = {"backend": "ollama", "model": m, "num_ctx": 8192, "keep_alive": "30m"}
    return opts


def role_config(choice: dict, base: dict) -> dict:
    """The role config for a picked model: the preset, keeping the role's temperature and
    max_tokens unless the preset sets its own (thinking models need a larger max_tokens)."""
    out = {k: base[k] for k in ("temperature", "max_tokens") if k in base}
    out.update(choice)
    return out


def coerce(f: Field, value):
    """Validate a value from the control page; raise ValueError with a readable message."""
    if f.kind == "choice":
        if value is False and "off" in f.options:   # YAML/--set read a bare off as False
            value = "off"
        if value not in f.options:
            raise ValueError(f"{f.label}: choose one of {f.options}")
        return value
    if f.kind in ("int", "float"):
        try:
            v = int(value) if f.kind == "int" else float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{f.label}: not a number: {value!r}") from None
        if (f.min is not None and v < f.min) or (f.max is not None and v > f.max):
            raise ValueError(f"{f.label}: must be between {f.min:g} and {f.max:g}")
        return v
    if f.kind == "model":
        return str(value)
    raise ValueError(f"unknown field kind {f.kind}")


def describe(role_cfg: dict) -> str:
    """Short human label: 'qwen/qwen3.8-27b (groq)'."""
    b = role_cfg.get("backend", "?")
    where = "local" if b == "ollama" and not (role_cfg.get("host") or os.environ.get("OLLAMA_HOST")) else b
    return f"{role_cfg.get('model') or b} ({where})"
