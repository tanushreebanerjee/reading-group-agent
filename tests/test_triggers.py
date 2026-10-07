from assistant.triggers import parse_trigger


def test_plain_json():
    r = parse_trigger('{"trigger": "gap", "confidence": 0.82, "reason": "asked about N"}')
    assert (r.trigger, r.confidence, r.reason, r.valid) == ("gap", 0.82, "asked about N", True)


def test_code_fence_and_prose():
    r = parse_trigger('Sure:\n```json\n{"trigger":"contradiction","confidence":0.9,"reason":"x"}\n```')
    assert r.trigger == "contradiction" and r.confidence == 0.9


def test_unknown_label_becomes_none():
    assert parse_trigger('{"trigger": "question", "confidence": 0.9}').trigger == "none"


def test_confidence_clamped_and_percent():
    assert parse_trigger('{"trigger": "gap", "confidence": 85}').confidence == 0.85
    assert parse_trigger('{"trigger": "gap", "confidence": -3}').confidence == 0.0
    assert parse_trigger('{"trigger": "gap", "confidence": "high"}').confidence == 0.0


def test_garbage_is_invalid():
    r = parse_trigger("I think nothing to flag")
    assert not r.valid and r.trigger == "none"


def test_quote_parsed():
    r = parse_trigger('{"trigger":"gap","quote":"How many source views?","confidence":0.8,"reason":"N=2 (5.1)"}')
    assert r.quote == "How many source views?"
