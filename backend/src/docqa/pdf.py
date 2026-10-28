"""PDF validation and page-by-page text extraction.

The page number is attached HERE, at the very first step, and travels with the
text through chunking, vector metadata and citations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader

# pypdf logs warnings that quote raw bytes from the document ("Invalid Elementary Object
# ... b'<document text>'"). In Lambda those lines would land in CloudWatch, so we mute it.
# Failures still surface as PdfError with a safe code.
logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)


class PdfError(Exception):
    """Safe, user-presentable failure. `code` is stored in DynamoDB."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PageText:
    page: int  # 1-based
    text: str


def extract_pages(data: bytes, max_bytes: int, max_pages: int) -> list[PageText]:
    # 1. Cheap checks first, before running a parser on untrusted bytes.
    if len(data) > max_bytes:
        raise PdfError("FILE_TOO_LARGE", f"PDF exceeds {max_bytes} bytes")
    if not data.startswith(b"%PDF-"):
        raise PdfError("NOT_A_PDF", "File is not a PDF (bad signature)")

    # 2. Parse.
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise PdfError("ENCRYPTED_PDF", "Password-protected PDFs are not supported")
        page_count = len(reader.pages)
    except PdfError:
        raise
    except Exception as exc:  # pypdf raises many exception types for bad files
        raise PdfError("MALFORMED_PDF", "PDF could not be parsed") from exc

    if page_count == 0:
        raise PdfError("EMPTY_PDF", "PDF has no pages")
    if page_count > max_pages:
        raise PdfError("TOO_MANY_PAGES", f"PDF has more than {max_pages} pages")

    # 3. Extract each page independently; one bad page must not lose the rest.
    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        pages.append(PageText(page=index, text=text))

    if not any(p.text for p in pages):
        raise PdfError("NO_TEXT_FOUND", "No extractable text (scanned image PDF?)")
    return pages
