from core.paper import BM25, chunk_pages, tokenize


def test_tokenize_unglues_pdf_text():
    assert "source" in tokenize("We useN= 2source views")
    assert "2" in tokenize("We useN= 2source views")


def test_chunk_sections_and_captions():
    pages = ["1 Introduction\nWe study things in detail here for a while.\nTable 1: Results on data.\n"
             "a 1.0 2.0 3.0 more text to fill the chunk up nicely"]
    chunks = chunk_pages(pages, chunk_chars=50, overlap=0)
    assert chunks[0].section == "1 Introduction"
    assert any(c.text.startswith("Table 1") for c in chunks)


def test_bm25_ranks_relevant_doc_first():
    docs = ["the boundary level ablation table two", "training uses eight gpus", "unrelated words here"]
    assert BM25(docs).top("which boundary level", 1) == [0]
