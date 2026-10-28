import { useRef, useState } from "react";
import type { Api } from "../types";

const DEFAULT_MAX = 10 * 1024 * 1024;

/** Client-side checks are a convenience only; the server re-validates everything. */
export function validateFile(file: File, maxBytes: number): string | null {
  if (!file.name.toLowerCase().endsWith(".pdf") || (file.type && file.type !== "application/pdf")) {
    return "Please choose a PDF file.";
  }
  if (file.size === 0) return "That file is empty.";
  if (file.size > maxBytes) return `File is too large (max ${Math.round(maxBytes / 1024 / 1024)} MB).`;
  return null;
}

export function UploadPanel({ api, onUploaded, maxBytes = DEFAULT_MAX }: { api: Api; onUploaded: () => void; maxBytes?: number }) {
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    setError(null);
    setDone(null);
    if (!file) return;
    const problem = validateFile(file, maxBytes);
    if (problem) {
      setError(problem);
      e.target.value = "";
      return;
    }
    setProgress(0);
    try {
      const target = await api.createUpload(file.name);
      await api.uploadFile(target, file, setProgress);
      setDone(`Uploaded ${file.name}. Processing will start automatically.`);
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setProgress(null);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <section className="card" aria-labelledby="upload-title">
      <h2 id="upload-title">Upload a PDF</h2>
      <label htmlFor="pdf-input">PDF file (up to {Math.round(maxBytes / 1024 / 1024)} MB, text-based)</label>
      <input id="pdf-input" ref={input} type="file" accept="application/pdf,.pdf" onChange={onChange} disabled={progress !== null} />
      {progress !== null && (
        <p>
          <label htmlFor="upload-progress">Uploading… {Math.round(progress * 100)}%</label>
          <progress id="upload-progress" value={progress} max={1} />
        </p>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      {done && <p role="status" className="ok">{done}</p>}
    </section>
  );
}
