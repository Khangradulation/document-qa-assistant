import { useCallback, useEffect, useState } from "react";
import type { Api, DocumentInfo } from "../types";
import { AskPanel } from "./AskPanel";
import { DocumentList } from "./DocumentList";
import { UploadPanel } from "./UploadPanel";

const POLL_MS = 3000;
const msg = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");

export function Workspace({ api, maxBytes, pollMs = POLL_MS }: { api: Api; maxBytes?: number; pollMs?: number }) {
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await api.listDocuments());
      setLoadError(null);
    } catch (e) {
      setLoadError(msg(e));
    }
  }, [api]);

  // Initial load. `cancelled` stops a late response from updating an unmounted component.
  useEffect(() => {
    let cancelled = false;
    api
      .listDocuments()
      .then((d) => !cancelled && (setDocs(d), setLoadError(null)))
      .catch((e) => !cancelled && setLoadError(msg(e)));
    return () => {
      cancelled = true;
    };
  }, [api]);

  // Poll only while something is still being processed.
  const busy = docs?.some((d) => d.status === "UPLOADED" || d.status === "PROCESSING") ?? false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(t);
  }, [busy, refresh, pollMs]);

  async function act(fn: () => Promise<void>) {
    setActionError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setActionError(msg(e));
    }
  }

  const selected = docs?.find((d) => d.document_id === selectedId && d.status === "READY") ?? null;

  return (
    <div className="grid">
      <div className="col">
        <UploadPanel api={api} onUploaded={() => void refresh()} maxBytes={maxBytes} />
        {actionError && <p role="alert" className="error">{actionError}</p>}
        <DocumentList
          docs={docs}
          loadError={loadError}
          selectedId={selected?.document_id ?? null}
          onSelect={setSelectedId}
          onReprocess={(d) => act(() => api.reprocess(d.document_id))}
          onDelete={(d) => {
            if (!window.confirm(`Delete “${d.filename}”? This removes the PDF and its search index.`)) return;
            if (selectedId === d.document_id) setSelectedId(null);
            void act(() => api.deleteDocument(d.document_id));
          }}
        />
      </div>
      <div className="col">
        <AskPanel key={selected?.document_id ?? "none"} api={api} doc={selected} />
      </div>
    </div>
  );
}
