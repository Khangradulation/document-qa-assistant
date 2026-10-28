import pytest

from docqa.config import Settings
from docqa.models import (
    ALLOWED_TRANSITIONS,
    Chunk,
    DocumentRecord,
    DocumentStatus,
    QueryRequest,
    UploadUrlRequest,
    ValidationError,
    sanitize_filename,
)

DOC_ID = "a" * 32


def test_upload_request_ok():
    r = UploadUrlRequest.parse({"filename": "My Report.pdf", "content_type": "application/pdf"})
    assert r.filename == "My Report.pdf"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"filename": "x.txt", "content_type": "application/pdf"},
        {"filename": "a.pdf", "content_type": "text/html"},
        {"filename": 5, "content_type": "application/pdf"},
    ],
)
def test_upload_request_rejects_bad_input(body):
    with pytest.raises(ValidationError):
        UploadUrlRequest.parse(body)


def test_filename_path_traversal_is_neutralised():
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert "/" not in sanitize_filename("a/b\\c.pdf")


def test_query_request_validation():
    assert QueryRequest.parse({"document_id": DOC_ID, "question": " hi "}, 100).question == "hi"
    for body in (
        {"document_id": "nope", "question": "q"},
        {"document_id": DOC_ID, "question": "   "},
        {"document_id": DOC_ID, "question": "x" * 101},
    ):
        with pytest.raises(ValidationError):
            QueryRequest.parse(body, 100)


def test_chunk_vector_key_is_deterministic():
    c = Chunk(document_id=DOC_ID, chunk_id="p001-c000", page=1, text="t")
    assert c.vector_key == f"{DOC_ID}:p001-c000"


def test_public_record_hides_owner():
    rec = DocumentRecord(DOC_ID, "owner-1", "a.pdf", DocumentStatus.READY, "t", "t")
    pub = rec.to_public()
    assert "owner_id" not in pub and pub["status"] == "READY"


def test_status_machine_has_no_exit_from_deleting():
    assert ALLOWED_TRANSITIONS[DocumentStatus.DELETING] == set()
    assert DocumentStatus.PROCESSING in ALLOWED_TRANSITIONS[DocumentStatus.FAILED]


def test_settings_validation():
    Settings().validate()
    with pytest.raises(ValueError):
        Settings(chunk_tokens=100, chunk_overlap_tokens=100).validate()
    with pytest.raises(ValueError):
        Settings(embed_dimensions=999).validate()
