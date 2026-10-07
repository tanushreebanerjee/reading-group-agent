from prep.related import cited_counts, parse_bibliography
from prep.verify import check_rows, parse_key_numbers, report

BRIEF = """# T

## Key numbers
| Number | What it measures | Location | Page |
|---|---|---|---|
| 16.362 | PSNR | Table 3 | 2 |
| 4.4× | speedup | Fig. 1 | 1 |
| 99.9 | made up | Table 9 | 1 |

## Method in brief
x
"""


def test_parse_and_check_numbers():
    rows = parse_key_numbers(BRIEF)
    assert [r.number for r in rows] == ["16.362", "4.4×", "99.9"]
    pages = ["converges 4.4 × faster", "GLD 16.362 0.630"]
    rows = check_rows(rows, pages)
    assert rows[0].found_on_page and rows[1].found_on_page
    assert not rows[2].found_anywhere
    _, ok = report(rows)
    assert not ok


def test_bibliography_fallback_parsing():
    text = ("Body cites [2] and [1, 2] and [3-4].\nReferences\n"
            "1. Smith, A.: A great title about things. In: CVPR (2020)\n"
            "2. Doe, B.: Another title here. arXiv preprint arXiv:1234 (2021)\n")
    bib = parse_bibliography(text)
    assert bib[1] == "A great title about things"
    assert bib[2] == "Another title here"
    c = cited_counts(text)
    assert c[2] == 2 and c[1] == 1 and c[4] == 1
