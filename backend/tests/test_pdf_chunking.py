import pytest
from fakes import make_pdf

from docqa.chunking import chunk_pages, chunks_per_page, keys_from_counts
from docqa.pdf import PageText, PdfError, extract_pages

DOC = "d" * 32


def test_pages_keep_their_numbers_including_blank_pages():
    pdf = make_pdf(["alpha on one", "", "gamma on three"])
    pages = extract_pages(pdf, 10_000_000, 50)
    assert [p.page for p in pages] == [1, 2, 3]
    assert "alpha" in pages[0].text and pages[1].text == "" and "gamma" in pages[2].text


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"hello world", "NOT_A_PDF"),
        (b"%PDF-1.4 this is garbage", "MALFORMED_PDF"),
        (b"%PDF-" + b"0" * 2000, "FILE_TOO_LARGE"),
        (make_pdf(["", ""]), "NO_TEXT_FOUND"),
    ],
)
def test_bad_pdfs_get_safe_error_codes(data, code):
    limit = 1000 if code == "FILE_TOO_LARGE" else 10_000_000
    with pytest.raises(PdfError) as e:
        extract_pages(data, limit, 50)
    assert e.value.code == code


def test_too_many_pages():
    with pytest.raises(PdfError) as e:
        extract_pages(make_pdf(["a", "b", "c"]), 10_000_000, 2)
    assert e.value.code == "TOO_MANY_PAGES"


def test_chunks_never_cross_pages_and_overlap():
    words = " ".join(f"w{i}" for i in range(100))
    pages = [PageText(1, words), PageText(2, ""), PageText(3, "short page")]
    # 40 tokens -> 30 words per chunk; 10 tokens -> 7 words overlap; step 23
    chunks = chunk_pages(DOC, pages, chunk_tokens=40, overlap_tokens=10)
    p1 = [c for c in chunks if c.page == 1]
    assert [c.chunk_id for c in p1][:2] == ["p001-c000", "p001-c001"]
    a, b = p1[0].text.split(), p1[1].text.split()
    assert len(a) == 30 and a[-7:] == b[:7]  # overlap is real
    assert " ".join(p1[-1].text.split()[-1:]) == "w99"  # nothing dropped at the end
    assert not [c for c in chunks if c.page == 2]  # empty page skipped
    assert [c.page for c in chunks if c.page == 3] == [3]


def test_chunk_ids_are_deterministic():
    pages = [PageText(1, "one two three " * 50)]
    a = chunk_pages(DOC, pages, 40, 10)
    b = chunk_pages(DOC, pages, 40, 10)
    assert [c.vector_key for c in a] == [c.vector_key for c in b]


def test_overlap_must_be_smaller_than_chunk():
    with pytest.raises(ValueError):
        chunk_pages(DOC, [PageText(1, "x")], 10, 10)


def test_counts_roundtrip_to_keys():
    pages = [PageText(1, "a " * 100), PageText(2, ""), PageText(3, "b")]
    chunks = chunk_pages(DOC, pages, 40, 10)
    counts = chunks_per_page(chunks)
    assert sorted(keys_from_counts(DOC, counts)) == sorted(c.vector_key for c in chunks)
