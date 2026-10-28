import { useState } from "react";
import type { Api, Citation, DocumentInfo, QueryResult } from "../types";

const MAX_Q = 1000;

export function AskPanel({ api, doc }: { api: Api; doc: DocumentInfo | null }) {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [citeError, setCiteError] = useState<string | null>(null);

  if (!doc) {
    return (
      <section className="card" aria-labelledby="ask-title">
        <h2 id="ask-title">Ask a question</h2>
        <p className="muted">Select a ready document on the left to start asking questions.</p>
      </section>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || !doc) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setCiteError(null);
    try {
      setResult(await api.query(doc.document_id, q));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  // Open the PDF at the cited page. The window is opened synchronously (inside the click)
  // so pop-up blockers allow it, then pointed at the short-lived presigned URL.
  async function openCitation(c: Citation) {
    if (!doc) return;
    setCiteError(null);
    const w = window.open("", "_blank");
    try {
      const url = await api.downloadUrl(doc.document_id);
      if (!w) throw new Error("Your browser blocked the new tab. Allow pop-ups and try again.");
      w.opener = null;
      w.location.href = `${url}#page=${c.page}`;
    } catch (err) {
      w?.close();
      setCiteError(err instanceof Error ? err.message : "Could not open the document");
    }
  }

  return (
    <section className="card" aria-labelledby="ask-title">
      <h2 id="ask-title">Ask about “{doc.filename}”</h2>
      <form onSubmit={submit}>
        <label htmlFor="question">Your question</label>
        <textarea id="question" rows={3} maxLength={MAX_Q} value={question} onChange={(e) => setQuestion(e.target.value)}
          aria-describedby="q-count" placeholder="e.g. What pH range should the nutrient solution be kept at?" />
        <div className="row spread">
          <span id="q-count" className="muted small">{question.length}/{MAX_Q}</span>
          <button type="submit" disabled={loading || !question.trim()}>{loading ? "Searching…" : "Ask"}</button>
        </div>
      </form>

      <div aria-live="polite">
        {loading && <p role="status">Searching the document and writing an answer…</p>}
        {error && <p role="alert" className="error">{error}</p>}
        {result && (
          <article className="answer" aria-label="Answer">
            <p className={result.answered ? "" : "muted"}>{result.answer}</p>
            {result.answered && result.citations.length > 0 && (
              <>
                <h3>Sources</h3>
                <ul className="citations">
                  {result.citations.map((c) => (
                    <li key={c.chunk_id}>
                      <button type="button" className="link" onClick={() => openCitation(c)}>
                        {c.filename} — page {c.page}
                      </button>
                      <blockquote>{c.excerpt}</blockquote>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className="muted small">Request ID: {result.request_id}</p>
          </article>
        )}
        {citeError && <p role="alert" className="error">{citeError}</p>}
      </div>
    </section>
  );
}
