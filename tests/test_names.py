import pytest

from assistant.names import NameDetector
from core.config import load_config


@pytest.fixture
def det():
    cfg = load_config()
    return NameDetector(cfg["assistant_name"], cfg["name_aliases"])


@pytest.mark.parametrize("text,rest", [
    ("Sherlock, what PSNR does GLD get?", "what PSNR does GLD get?"),
    ("Not off the top of my head. Sherlock, what PSNR does it get?", "what PSNR does it get?"),
    ("Hey Sherlock why did they need the cascade?", "why did they need the cascade?"),
    ("Hey Sherlok, why the cascade?", "why the cascade?"),
    ("Shirlock, which table is that?", "which table is that?"),
    ("Hey sure lock, which table?", "which table?"),
    ("Okay Shylock, what is k?", "what is k?"),
])
def test_detects_name_variants(det, text, rest):
    hit = det.detect(text)
    assert hit is not None
    assert hit.rest == rest


@pytest.mark.parametrize("text", [
    "They mention a sure lock on the weights, which is odd.",
    "The method is called G L D, geometric latent diffusion.",
    "Let's talk about the evaluation setup.",
    "So the share of the work is mostly in the decoder, I think, and the lock-step training of the levels",
    "Surely they could have locked the encoder too.",
])
def test_ignores_non_addresses(det, text):
    assert det.detect(text) is None


def test_name_only_segment_gives_empty_rest(det):
    hit = det.detect("Sherlock?")
    assert hit is not None and hit.rest in ("", "?")
