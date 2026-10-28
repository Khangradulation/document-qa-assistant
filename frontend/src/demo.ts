import type { Api, Auth, Citation, DocumentInfo, QueryResult } from "./types";

// Demo mode: everything runs in the browser with fake data so the UI can be tried
// without AWS. The "retrieval" below is crude keyword overlap, NOT real RAG.
const FILENAME = "greenfield_hydroponics_guide.pdf";
const PAGES = [
  "Lettuce is usually ready to harvest 28 to 35 days after transplanting. We recommend starting with DWC when growing lettuce. Wear gloves when handling pH Down.",
  "Keep the nutrient solution at a pH between 5.8 and 6.2. For lettuce the target EC is 1.2 to 1.8 mS/cm. Replace the whole reservoir solution every 14 days. Water temperature should stay between 18 and 22 degrees Celsius.",
  "Lettuce needs 14 to 16 hours of light per day. Tomatoes need 16 to 18 hours of light per day. Place full-spectrum LED lights 30 cm above the plant canopy.",
  "Yellow lower leaves usually indicate a nitrogen deficiency. Control aphids by spraying neem oil once a week. Check pH daily and check EC every 2 days.",
];
const STOP = new Set(["the", "a", "an", "of", "to", "is", "are", "how", "what", "do", "i", "my", "for", "in", "and", "should", "does"]);
const words = (s: string) => s.toLowerCase().match(/[a-z0-9.]+/g)?.filter((w) => !STOP.has(w)) ?? [];

export function createDemo(): { api: Api; auth: Auth } {
  const now = () => new Date().toISOString();
  const docs: DocumentInfo[] = [
    {
      document_id: "d".repeat(32), filename: FILENAME, status: "READY", created_at: now(), updated_at: now(),
      error_code: null, error_detail: null, page_count: 4, chunk_count: 4,
    },
  ];
  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

  const api: Api = {
    async createUpload(filename) {
      const id = crypto.randomUUID().replaceAll("-", "");
      docs.unshift({
        document_id: id, filename, status: "UPLOADED", created_at: now(), updated_at: now(),
        error_code: null, error_detail: null, page_count: null, chunk_count: null,
      });
      return { document_id: id, upload_url: "demo://upload", headers: {}, max_bytes: 10 * 1024 * 1024 };
    },
    async uploadFile(target, _file, onProgress) {
      for (let i = 1; i <= 5; i++) { await wait(150); onProgress(i / 5); }
      const doc = docs.find((d) => d.document_id === target.document_id)!;
      doc.status = "PROCESSING";
      setTimeout(() => Object.assign(doc, { status: "READY", page_count: 4, chunk_count: 4 }), 2500);
    },
    async listDocuments() { return docs.map((d) => ({ ...d })); },
    async deleteDocument(id) { docs.splice(docs.findIndex((d) => d.document_id === id), 1); },
    async reprocess() { await wait(100); },
    async downloadUrl() { return `${location.origin}/sample.pdf`; },
    async query(_id, question): Promise<QueryResult> {
      await wait(700);
      const q = new Set(words(question));
      const scored = PAGES.map((text, i) => ({ i, text, score: words(text).filter((w) => q.has(w)).length }));
      const best = scored.sort((a, b) => b.score - a.score)[0];
      const request_id = `demo-${Math.random().toString(16).slice(2, 10)}`;
      if (best.score < 2) {
        return { request_id, answered: false, citations: [], answer: "I could not find the answer to that in the provided document." };
      }
      const sentence = best.text.split(". ").sort((a, b) => words(b).filter((w) => q.has(w)).length - words(a).filter((w) => q.has(w)).length)[0];
      const citation: Citation = { chunk_id: `p00${best.i + 1}-c000`, filename: FILENAME, page: best.i + 1, excerpt: best.text };
      return { request_id, answered: true, answer: sentence.endsWith(".") ? sentence : `${sentence}.`, citations: [citation] };
    },
  };

  let email: string | null = null;
  const auth: Auth = {
    email: () => email,
    restore: async () => { email = "demo@example.com"; return true; },
    signIn: async (e) => { email = e; return { status: "ok" }; },
    completeNewPassword: async () => undefined,
    signOut: () => { email = null; },
  };
  return { api, auth };
}
