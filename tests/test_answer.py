import asyncio

from assistant.answer import Answerer, has_citation, plain_text, trim_sentences
from core.llm import make_llm
from core.paper import Chunk, Paper


def paper():
    return Paper("T", ["p1"], [Chunk(7, "4.3 Boundary", "level 1 is the best synthesis boundary (Table 2)"),
                               Chunk(8, "5.1 Setup", "We use N=2 source views and 200 samples per dataset")])


def test_answer_builds_prompt_and_streams(cfg):
    llm = make_llm({"backend": "fake", "responses": "They use two source views (§5.1). Extra. More. Too many."})
    a = Answerer(cfg, llm, "BRIEF TEXT", paper())
    deltas = []

    async def on_delta(p):
        deltas.append(p)

    res = asyncio.run(a.answer("how many source views", "[00:01] hi", on_delta))
    system, user = llm.calls[0]
    assert "BRIEF TEXT" in system and "Sherlock" in system
    assert "N=2 source views" in user  # excerpts vary per question, so they live in the user turn
    assert "how many source views" in user and "[00:01] hi" in user
    assert deltas and res.cited
    assert res.text == "They use two source views (§5.1). Extra. More."  # capped at 3 sentences
    assert res.sources[0].startswith("[§5.1")


def test_interjection_prompt(cfg):
    llm = make_llm({"backend": "fake", "responses": "Not level 3: the paper picks k=1 (Table 2)."})
    a = Answerer(cfg, llm, "B", paper())
    res = asyncio.run(a.run("interjection", "level 3 is best", "t", trigger="contradiction",
                            reason="level 3 is best", max_sentences=2))
    assert "contradiction" in llm.calls[0][1] and res.cited


def test_citation_detection():
    assert has_citation("See Table 3.") and has_citation("(§4.4)") and has_citation("Fig. 2 shows")
    assert has_citation("Appendix D.2 reports") and has_citation("Eq. 1")
    assert not has_citation("The paper doesn't say.")


def test_trim_sentences():
    assert trim_sentences("A is 1.5 dB. B. C. D.", 3) == "A is 1.5 dB. B. C."


def test_live_prompts_share_cacheable_prefix(cfg):
    from assistant.triggers import TriggerChecker

    llm = make_llm({"backend": "fake"})
    ans_sys = Answerer(cfg, llm, "BRIEF TEXT", paper()).build("answer", "q", "t")[0]
    int_sys = Answerer(cfg, llm, "BRIEF TEXT", paper()).build("interjection", "q", "t", trigger="gap", reason="r")[0]
    trig_sys = TriggerChecker(cfg, llm, "BRIEF TEXT").build("t", [])[0]
    brief_end = ans_sys.index("BRIEF TEXT") + len("BRIEF TEXT")
    assert ans_sys[:brief_end] == trig_sys[:brief_end] == int_sys[:brief_end]


def test_plain_text_strips_latex():
    assert plain_text(r"a rate of \(2 \times 10^{-4}\) and **bold**") == "a rate of 2 × 10^-4 and bold"
    assert plain_text(r"model \(M1 \to 0\)") == "model M1 → 0"
