export type DocStatus = "UPLOADED" | "PROCESSING" | "READY" | "FAILED" | "DELETING";

export interface DocumentInfo {
  document_id: string;
  filename: string;
  status: DocStatus;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  error_detail: string | null;
  page_count: number | null;
  chunk_count: number | null;
}

export interface Citation {
  chunk_id: string;
  filename: string;
  page: number;
  excerpt: string;
}

export interface QueryResult {
  request_id: string;
  answer: string;
  answered: boolean;
  citations: Citation[];
}

export interface UploadTarget {
  document_id: string;
  upload_url: string;
  headers: Record<string, string>;
  max_bytes: number;
}

export interface AppConfig {
  demo?: boolean;
  apiUrl: string;
  region: string;
  userPoolId: string;
  clientId: string;
  maxUploadBytes?: number;
}

/** Everything the UI needs from the backend. The real and demo versions both implement it. */
export interface Api {
  createUpload(filename: string): Promise<UploadTarget>;
  uploadFile(target: UploadTarget, file: File, onProgress: (fraction: number) => void): Promise<void>;
  listDocuments(): Promise<DocumentInfo[]>;
  deleteDocument(id: string): Promise<void>;
  reprocess(id: string): Promise<void>;
  downloadUrl(id: string): Promise<string>;
  query(id: string, question: string): Promise<QueryResult>;
}

export type SignInResult = { status: "ok" } | { status: "new_password_required" };

export interface Auth {
  email(): string | null;
  restore(): Promise<boolean>;
  signIn(email: string, password: string): Promise<SignInResult>;
  completeNewPassword(newPassword: string): Promise<void>;
  signOut(): void;
}
