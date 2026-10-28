import type { Api, DocumentInfo, QueryResult, UploadTarget } from "./types";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public requestId?: string,
  ) {
    super(message);
  }
}

/** Real client. The bearer token is the Cognito JWT; no AWS credentials ever reach the browser. */
export function createApi(baseUrl: string, getToken: () => Promise<string>): Api {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${await getToken()}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError("NETWORK_ERROR", "Could not reach the server. Check your connection.", 0);
    }
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
      const err = (data.error ?? {}) as { code?: string; message?: string };
      throw new ApiError(
        err.code ?? "ERROR",
        err.message ?? `Request failed (${res.status})`,
        res.status,
        data.request_id as string | undefined,
      );
    }
    return data as T;
  }

  return {
    createUpload: (filename) =>
      call<UploadTarget>("POST", "/documents/upload-url", { filename, content_type: "application/pdf" }),

    // Direct browser -> S3 upload with a presigned URL. XHR (not fetch) because it reports upload progress.
    uploadFile: (target, file, onProgress) =>
      new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", target.upload_url);
        for (const [k, v] of Object.entries(target.headers)) xhr.setRequestHeader(k, v);
        xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
        xhr.onload = () =>
          xhr.status >= 200 && xhr.status < 300
            ? resolve()
            : reject(new ApiError("UPLOAD_FAILED", `Upload failed (${xhr.status})`, xhr.status));
        xhr.onerror = () => reject(new ApiError("UPLOAD_FAILED", "Upload failed. Check your connection.", 0));
        xhr.send(file);
      }),

    listDocuments: async () => (await call<{ documents: DocumentInfo[] }>("GET", "/documents")).documents,
    deleteDocument: async (id) => void (await call("DELETE", `/documents/${id}`)),
    reprocess: async (id) => void (await call("POST", `/documents/${id}/reprocess`)),
    downloadUrl: async (id) => (await call<{ url: string }>("GET", `/documents/${id}/download-url`)).url,
    query: (id, question) => call<QueryResult>("POST", "/query", { document_id: id, question }),
  };
}
