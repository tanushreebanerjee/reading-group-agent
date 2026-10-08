import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def cfg():
    from core.config import load_config

    return load_config()


@pytest.fixture(autouse=True)
def no_local_config(monkeypatch, tmp_path):
    """Tests see the committed config.yaml only, not this machine's config.local.yaml."""
    import core.config

    monkeypatch.setattr(core.config, "LOCAL_CONFIG", tmp_path / "absent.yaml")
