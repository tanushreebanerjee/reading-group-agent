"""Load config.yaml and .env. Nothing tunable should be hardcoded elsewhere."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
LOCAL_CONFIG = ROOT / "config.local.yaml"  # gitignored per-machine/group overrides (e.g. member names)


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict[str, Any]:
    """Read the YAML config, load .env into the environment, apply overrides."""
    load_dotenv(ROOT / ".env")
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    if path == DEFAULT_CONFIG and LOCAL_CONFIG.exists():
        with open(LOCAL_CONFIG) as f:
            cfg = deep_merge(cfg, yaml.safe_load(f) or {})
    if overrides:
        cfg = deep_merge(cfg, {k: v for k, v in overrides.items() if v is not None})
    # resolve relative paths against the repo root
    for key, val in cfg.get("paths", {}).items():
        p = Path(val)
        cfg["paths"][key] = str(p if p.is_absolute() else ROOT / p)
    return cfg
