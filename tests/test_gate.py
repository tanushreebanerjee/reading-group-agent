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


# ---- discuss mode: "point" hands ----
from assistant.hands import Hand, HandQueue
from assistant.triggers import MODE_TYPES, TriggerChecker, TriggerResult

LINES = ["Sampling in a feature space with several levels might not be cheaper than a VAE at inference time.",
         "They do have a computational cost section in the appendix, I think, but I only skimmed it."]


def point(quote=LINES[0], reason="Table 15: GLD samples in 66.1 s vs 28.0 s for the VAE", conf=0.85):
    return TriggerResult("point", conf, reason, quote=quote)


def discuss_gate(**kw):
    return Gate({"contradiction": 0.7, "gap": 0.7, "point": 0.8}, cooldown_s=30,
                type_cooldown_s={"point": 300}, allowed=MODE_TYPES["discuss"], **kw)


def test_points_only_in_discuss_mode():
    engaged = Gate({"point": 0.8}, allowed=MODE_TYPES["engaged"])
    assert engaged.check(point(), 10, LINES, LINES) == "none"
    assert discuss_gate().check(point(), 10, LINES, LINES) == "raised"


def test_point_must_quote_the_live_thread():
    g = discuss_gate()
    assert g.check(point(), 10, LINES, recent_lines=[LINES[1]]) == "stale"   # quote is older than the window


def test_point_must_be_new():
    said = LINES + ["Table 15 says GLD samples in 66.1 s vs 28.0 s for the VAE, it's slower"]
    assert discuss_gate().check(point(), 10, said, said) == "already_said"


def test_point_threshold_and_own_cooldown():
    g = discuss_gate()
    assert g.check(point(conf=0.75), 10, LINES, LINES) == "below_threshold"
    assert g.check(point(), 10, LINES, LINES) == "raised"
    other = point(quote=LINES[1], reason="App. D.2 breaks down the 66.8 s: level-0 cascade is 28.4 s")
    assert g.check(other, 100, LINES, LINES) == "cooldown"          # past the 30 s global, inside 300 s
    gap = TriggerResult("gap", 0.9, "N = 2 source views (App. A.3)", quote=LINES[1])
    assert g.check(gap, 100, LINES, LINES) == "raised"               # corrections/gaps aren't held by it


def test_hand_priority_and_expiry():
    q = HandQueue()
    q.add(Hand("H1", 10, "T1", "point", 0.9, "r", "x"))
    q.add(Hand("H2", 20, "T2", "contradiction", 0.9, "r", "y"))
    assert q.head().id == "H2"                       # a correction outranks an older point
    gone = q.expire(140, {"point": 120, "contradiction": 0})
    assert [h.id for h in gone] == ["H1"] and q.hands[0].status == "expired"
    assert q.expire(10_000, {"point": 120, "contradiction": 0}) == []   # 0 = never


def test_trigger_prompt_fills_point_rule_only_in_discuss(cfg):
    from core.llm import make_llm
    llm = make_llm({"backend": "fake"})
    engaged = TriggerChecker(cfg, llm, "BRIEF", mode="engaged").build("t", [])[0]
    discuss = TriggerChecker(cfg, llm, "BRIEF", mode="discuss").build("t", [])[0]
    assert "$point_rule" not in engaged and "$types" not in engaged and '"point"' not in engaged
    assert '"point": the group is discussing' in discuss and '"point" | "none"' in discuss
