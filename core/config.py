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
PROFILES_DIR = ROOT / "profiles"            # named setups, e.g. --profile nexus


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None,
                profile: str | None = None) -> dict[str, Any]:
    """Read the YAML config, load .env into the environment, apply overrides.

    Layers, later wins: config.yaml < config.local.yaml < profiles/<profile>.yaml < overrides."""
    load_dotenv(ROOT / ".env")
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    if path == DEFAULT_CONFIG and LOCAL_CONFIG.exists():
        with open(LOCAL_CONFIG) as f:
            cfg = deep_merge(cfg, yaml.safe_load(f) or {})
    if profile:
        pf = Path(profile) if Path(profile).suffix else PROFILES_DIR / f"{profile}.yaml"
        if not pf.exists():
            known = sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))
            raise FileNotFoundError(f"no profile {profile!r} ({pf}); available: {known}")
        with open(pf) as f:
            cfg = deep_merge(cfg, yaml.safe_load(f) or {})
        cfg["profile"] = pf.stem
    if overrides:
        cfg = deep_merge(cfg, {k: v for k, v in overrides.items() if v is not None})
    # resolve relative paths against the repo root
    for key, val in cfg.get("paths", {}).items():
        p = Path(val)
        cfg["paths"][key] = str(p if p.is_absolute() else ROOT / p)
    return cfg
