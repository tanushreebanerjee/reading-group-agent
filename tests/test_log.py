import json

from core.transcript import Segment, write_jsonl
from log.build import build_log
from log.events import EventLog, new_meeting_dir
from log.tune import parse_log, report


def make_meeting(tmp_path, cfg):
    d = tmp_path / "2026-10-07"
    d.mkdir()
    write_jsonl(d / "transcript.jsonl", [
        Segment(0, 4, "Kate said level three is the best boundary."),
        Segment(5, 9, "Sherlock, what PSNR on RealEstate?"),
        Segment(40, 44, "How many source views? No idea, moving on."),
    ])
    ev = EventLog(d / "events.jsonl")
    ev.write("meeting_start", mode="engaged", source="x.wav", paper="GLD", stt_model="small.en",
             answer_llm="<fake>", trigger_llm="<fake>")
    ev.write("answer", id="A1", t=12, question="what PSNR on RealEstate?", q_start=5, q_end=9,
             text="16.362 vs 15.656 (Table 3).", latency_s=2.1, llm_s=1.5, cited=True, sources=[])
    ev.write("trigger", id="T1", t=15, trigger="contradiction", confidence=0.9, reason="level three", outcome="raised")
    ev.write("hand", id="H1", t=15, trigger_id="T1", trigger="contradiction", confidence=0.9,
             reason="level three", text="The paper picks k=1 (Table 2).", status="pending")
    ev.write("hand_status", id="H1", status="revealed", t=20)
    ev.write("trigger", id="T2", t=45, trigger="gap", confidence=0.55, reason="source views", outcome="below_threshold")
    ev.write("trigger", id="T3", t=60, trigger="gap", confidence=0.8, reason="views", outcome="cooldown")
    ev.write("meeting_end", t=70)
    ev.close()
    return d


def test_log_md_has_all_entries_with_context_and_labels(tmp_path, cfg):
    d = make_meeting(tmp_path, cfg)
    text = build_log(cfg, d, with_summary=False).read_text()
    assert "### A1" in text and "16.362" in text
    assert "### H1" in text and "revealed" in text
    assert "### T2" in text and "below_threshold" in text
    assert "### T3" in text and "cooldown" in text
    assert "### T1" not in text  # raised triggers appear as hands, not twice
    assert text.count("helpful: ") == 4
    assert "[00:05] Sherlock, what PSNR on RealEstate?" in text


def test_tune_reads_labels(tmp_path, cfg):
    d = make_meeting(tmp_path, cfg)
    path = build_log(cfg, d, with_summary=False)
    text = path.read_text()
    # label: H1 yes, T2 yes, T3 no
    parts = text.split("helpful: ")
    labels = iter(["", "yes", "yes", "no"])  # A1 unlabelled
    text = parts[0] + "".join("helpful: " + next(labels) + p for p in parts[1:])
    path.write_text(text)
    entries = {e.id: e for e in parse_log(path)}
    assert entries["H1"].helpful is True and entries["H1"].conf == 0.9
    assert entries["T2"].helpful is True and entries["T3"].helpful is False
    assert "A1" not in entries
    rep = report(list(entries.values()), [0.5, 0.7, 0.9])
    gap = rep.split("## gap")[1]
    assert "0.50      2        1        50%" in gap
    assert "0.70      1        0         0%" in gap


def test_new_meeting_dir_increments(tmp_path):
    import datetime as dt

    a = new_meeting_dir(tmp_path, dt.date(2026, 10, 7))
    (a / "x").write_text("1")
    b = new_meeting_dir(tmp_path, dt.date(2026, 10, 7))
    assert a.name == "2026-10-07" and b.name == "2026-10-07_2"
