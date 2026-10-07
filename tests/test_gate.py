from assistant.triggers import Gate, TriggerResult


def R(trig, conf, reason="r"):
    return TriggerResult(trig, conf, reason)


def gate():
    return Gate({"contradiction": 0.7, "gap": 0.7}, cooldown_s=180, dedup_ratio=70)


def test_threshold_per_type():
    g = Gate({"contradiction": 0.9, "gap": 0.5})
    assert g.check(R("contradiction", 0.8), 0) == "below_threshold"
    assert g.check(R("gap", 0.6, "how many views"), 0) == "raised"


def test_cooldown():
    g = gate()
    assert g.check(R("gap", 0.9, "how many source views"), 100) == "raised"
    assert g.check(R("contradiction", 0.9, "level three is best boundary"), 200) == "cooldown"
    assert g.check(R("contradiction", 0.9, "level three is best boundary"), 281) == "raised"


def test_dedup_even_after_cooldown():
    g = gate()
    assert g.check(R("contradiction", 0.9, "Kate said level three works best as the boundary"), 0) == "raised"
    out = g.check(R("contradiction", 0.95, "level three works best as the boundary, Kate said"), 1000)
    assert out == "duplicate"


def test_none_and_invalid():
    g = gate()
    assert g.check(R("none", 0.99), 0) == "none"
    assert g.check(TriggerResult("none", 0, "", valid=False), 0) == "invalid"


def test_ungrounded_quote_is_rejected():
    from assistant.triggers import quote_is_grounded

    lines = ["And they found that the deepest level, level 3, works best as the synthesis boundary."]
    assert quote_is_grounded("the deepest level, level 3, works best", lines)
    assert not quote_is_grounded("How much of GLD's lead over DINO would remain", lines)
    g = gate()
    r = TriggerResult("gap", 0.95, "brief open question", quote="How much of GLD's lead over DINO would remain")
    assert g.check(r, 0, lines) == "ungrounded"
