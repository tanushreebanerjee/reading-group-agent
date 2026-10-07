from assistant.questions import QuestionCollector
from core.transcript import Segment


def test_collects_until_pause():
    qc = QuestionCollector(pause_s=1.5)
    qc.start(Segment(10.0, 12.0, "Sherlock, what PSNR"), "what PSNR")
    assert qc.poll(12.5) is None
    qc.add(Segment(12.6, 14.0, "does GLD get on RealEstate?"))
    assert qc.poll(15.0) is None          # only 1.0 s since last word
    q = qc.poll(15.6)
    assert q is not None
    assert q.text == "what PSNR does GLD get on RealEstate?"
    assert q.end == 14.0 and not qc.collecting


def test_busy_source_delays_close():
    qc = QuestionCollector(pause_s=1.5)
    qc.start(Segment(0, 2, "Sherlock, why"), "why")
    assert qc.poll(5.0, busy=True) is None
    assert qc.poll(5.0, busy=False) is not None


def test_max_length_forces_close():
    qc = QuestionCollector(pause_s=1.5, max_s=10)
    qc.start(Segment(0, 1, "Sherlock"), "")
    for i in range(1, 12):
        qc.add(Segment(i, i + 1, "and"))
    assert qc.poll(11.5) is not None
