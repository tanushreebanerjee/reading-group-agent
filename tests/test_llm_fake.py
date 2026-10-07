import json

import pytest

from core.llm import FakeLLM, make_llm


def test_make_llm_unknown_backend():
    with pytest.raises(ValueError):
        make_llm({"backend": "nope"})


def test_fake_sequence_and_stream():
    llm = make_llm({"backend": "fake", "responses": ["one two", {"trigger": "none"}]})
    assert isinstance(llm, FakeLLM)
    assert "".join(llm.stream("s", "u")).strip() == "one two"
    assert json.loads(llm.complete("s", "u")) == {"trigger": "none"}
    assert json.loads(llm.complete("s", "u")) == {"trigger": "none"}  # last repeats
