import type { Api, DocumentInfo } from "./types";

export function makeDoc(over: Partial<DocumentInfo> = {}): DocumentInfo {
  return {
    document_id: "a".repeat(32), filename: "guide.pdf", status: "READY",
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    error_code: null, error_detail: null, page_count: 4, chunk_count: 4, ...over,
  };
}

export function makeApi(over: Partial<Api> = {}): Api {
  return {
    createUpload: vi.fn().mockResolvedValue({ document_id: "b".repeat(32), upload_url: "https://s3/x", headers: {}, max_bytes: 1e7 }),
    uploadFile: vi.fn().mockResolvedValue(undefined),
    listDocuments: vi.fn().mockResolvedValue([]),
    deleteDocument: vi.fn().mockResolvedValue(undefined),
    reprocess: vi.fn().mockResolvedValue(undefined),
    downloadUrl: vi.fn().mockResolvedValue("https://s3.example/doc.pdf?sig=1"),
    query: vi.fn(),
    ...over,
  };
}

export const pdf = (name = "a.pdf", size = 100) =>
  new File([new Uint8Array(size)], name, { type: "application/pdf" });
