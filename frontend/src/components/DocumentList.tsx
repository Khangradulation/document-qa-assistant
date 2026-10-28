import type { DocumentInfo, DocStatus } from "../types";

const LABEL: Record<DocStatus, string> = {
  UPLOADED: "Waiting to process",
  PROCESSING: "Processing…",
  READY: "Ready",
  FAILED: "Failed",
  DELETING: "Deleting…",
};

interface Props {
  docs: DocumentInfo[] | null; // null = still loading
  loadError: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDelete: (doc: DocumentInfo) => void;
  onReprocess: (doc: DocumentInfo) => void;
}

export function DocumentList({ docs, loadError, selectedId, onSelect, onDelete, onReprocess }: Props) {
  return (
    <section className="card" aria-labelledby="docs-title">
      <h2 id="docs-title">Your documents</h2>
      {loadError && <p role="alert" className="error">{loadError}</p>}
      {docs === null && !loadError && <p role="status">Loading documents…</p>}
      {docs?.length === 0 && <p className="muted">No documents yet. Upload a PDF to get started.</p>}
      <ul className="docs">
        {docs?.map((d) => (
          <li key={d.document_id} className={d.document_id === selectedId ? "selected" : ""}>
            <div className="doc-head">
              <strong>{d.filename}</strong>
              <span className={`badge ${d.status.toLowerCase()}`}>{LABEL[d.status]}</span>
            </div>
            <div className="muted small">
              Uploaded {new Date(d.created_at).toLocaleString()}
              {d.page_count ? ` · ${d.page_count} pages` : ""}
            </div>
            {d.status === "FAILED" && (
              <p role="alert" className="error small">
                {d.error_detail ?? "Processing failed."} {d.error_code && <code>{d.error_code}</code>}
              </p>
            )}
            <div className="row">
              <button type="button" onClick={() => onSelect(d.document_id)} disabled={d.status !== "READY"}
                aria-pressed={d.document_id === selectedId} aria-label={`Ask questions about ${d.filename}`}>
                {d.document_id === selectedId ? "Selected" : "Ask"}
              </button>
              {(d.status === "READY" || d.status === "FAILED") && (
                <button type="button" className="secondary" onClick={() => onReprocess(d)} aria-label={`Reprocess ${d.filename}`}>Reprocess</button>
              )}
              <button type="button" className="danger" onClick={() => onDelete(d)} disabled={d.status === "DELETING"} aria-label={`Delete ${d.filename}`}>Delete</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
