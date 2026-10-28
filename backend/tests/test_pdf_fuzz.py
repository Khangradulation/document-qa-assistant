"""Corrupted PDFs are untrusted input. Whatever we feed the parser, the ONLY acceptable
outcomes are: pages come back, or a PdfError (which becomes a FAILED document with a safe
code). Any other exception type would be an unhandled crash in the ingest Lambda."""

import random
from pathlib import Path

from docqa.pdf import PageText, PdfError, extract_pages

SAMPLE = (
    Path(__file__).resolve().parents[2] / "data" / "greenfield_hydroponics_guide.pdf"
).read_bytes()


def outcome(data: bytes) -> str:
    try:
        pages = extract_pages(data, 10_000_000, 50)
    except PdfError:
        return "rejected"
    assert pages and all(isinstance(p, PageText) and p.page >= 1 for p in pages)
    return "parsed"


def test_random_byte_corruption_never_escapes_as_an_unexpected_exception():
    rng = random.Random(1234)  # noqa: S311 (fuzzing, not crypto; fixed seed => reproducible)
    outcomes = set()
    for _ in range(300):
        data = bytearray(SAMPLE)
        for _ in range(rng.randint(1, 40)):
            data[rng.randrange(5, len(data))] = rng.randrange(256)  # keep the %PDF- header
        outcomes.add(outcome(bytes(data)))
    assert outcomes <= {"parsed", "rejected"}


def test_truncation_at_every_tenth_offset():
    for cut in range(5, len(SAMPLE), max(1, len(SAMPLE) // 100)):
        outcome(SAMPLE[:cut])


def test_hostile_structures():
    for blob in (
        b"%PDF-1.4\n" + b"[" * 5000,  # deep nesting
        # a page tree that points at itself
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 1 0 R >>\nendobj\ntrailer\n<< /Root 1 0 R >>",
        b"%PDF-" + b"\x00" * 4096,
        b"%PDF-1.7\n%%EOF",
    ):
        assert outcome(blob) == "rejected"


def test_parser_never_writes_document_bytes_to_the_logs(caplog, capsys):
    """pypdf's own warnings quote raw document bytes; they must not reach any log."""
    marker = b"SECRET-PAYLOAD-12345"
    broken = bytearray(SAMPLE)
    broken[600:640] = b"\x83" * 40  # corrupt the page tree so pypdf complains
    broken[1200 : 1200 + len(marker)] = marker
    with caplog.at_level("DEBUG"):
        for cut in (len(broken), len(broken) // 2):
            outcome(bytes(broken[:cut]))
    out = capsys.readouterr()
    assert "SECRET-PAYLOAD" not in caplog.text + out.out + out.err
    assert not [r for r in caplog.records if r.name.startswith("pypdf")]
