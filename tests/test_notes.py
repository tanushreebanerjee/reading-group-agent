import asyncio
import re

from assistant.answer import Answerer
from assistant.notes import MeetingNotes
from assistant.triggers import TriggerChecker
from core.llm import make_llm
from core.transcript import Segment
from display.server import Hub

PLACEHOLDER = re.compile(r"\$[a-z_]+")


def segs():
    return [Segment(10, 14, "They found level 3 works best as the boundary."),
            Segment(15, 19, "How many source views do they use? No idea, let's move on.")]


def test_notes_update_keeps_old_notes_on_empty_output(cfg):
    llm = make_llm({"backend": "fake", "responses": ["Topics:\n- boundary level\nClaims:\n- level 3 is best", "   "]})
    n = MeetingNotes(cfg, llm, "BRIEF")
    assert n.for_prompt == "(nothing yet)"
    n.update(segs())
    assert "level 3 is best" in n.text and n.upto == 19
    n.update([Segment(20, 22, "ok")])          # model returned nothing: keep the previous notes
    assert "level 3 is best" in n.text and n.upto == 22
    system, user = n.build(segs())
    assert "Current notes" in user and "They found level 3" in user and "BRIEF" in system


def test_every_live_prompt_fills_notes_and_has_no_placeholders(cfg):
    llm = make_llm({"backend": "fake"})
    a = Answerer(cfg, llm, "BRIEF", None)
    for name, extra in [("answer", {}), ("interjection", {"trigger": "gap", "reason": "r"})]:
        system, user, _ = a.build(name, "q?", "[00:10] t", notes="- NOTE ONE", **extra)
        assert "- NOTE ONE" in user and not PLACEHOLDER.search(system + user), name
        system, user, _ = a.build(name, "q?", "[00:10] t", **extra)       # default when not given
        assert "(nothing yet)" in user and not PLACEHOLDER.search(user)
    for mode in ("engaged", "discuss"):
        system, user = TriggerChecker(cfg, llm, "BRIEF", mode=mode).build("[00:10] t", [], "- NOTE TWO")
        assert "- NOTE TWO" in user and not PLACEHOLDER.search(system + user)


def test_hub_replays_recent_transcript_and_forgets_it_when_turned_off():
    hub = Hub()
    for i in range(70):
        asyncio.run(hub.send({"type": "transcript", "lines": [{"start": i, "speaker": None, "text": f"l{i}"}]}))
    st = hub.state["transcript"]
    assert st["reset"] and len(st["lines"]) == 60 and st["lines"][-1]["text"] == "l69"
    asyncio.run(hub.send({"type": "transcript_off"}))
    assert "transcript" not in hub.state
