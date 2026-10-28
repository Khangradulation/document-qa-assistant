import { ApiError, createApi } from "./api";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

describe("createApi", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the bearer token and JSON body, and trims the trailing slash", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(200, { request_id: "r", answered: true, answer: "a", citations: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi("https://api.example/", async () => "TOKEN");
    await api.query("d".repeat(32), "hi");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.example/query");
    expect(init.headers.Authorization).toBe("Bearer TOKEN");
    expect(JSON.parse(init.body)).toEqual({ document_id: "d".repeat(32), question: "hi" });
  });

  it("turns the backend error shape into an ApiError with a request id", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(409, { error: { code: "DOCUMENT_NOT_READY", message: "Document is PROCESSING, not READY" }, request_id: "rid" })));
    const err = await createApi("https://x", async () => "t").query("d".repeat(32), "q").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ code: "DOCUMENT_NOT_READY", status: 409, requestId: "rid", message: "Document is PROCESSING, not READY" });
  });

  it("reports network failures with a friendly message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(createApi("https://x", async () => "t").listDocuments()).rejects.toMatchObject({ code: "NETWORK_ERROR" });
  });

  it("lists documents from the wrapped response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(200, { documents: [{ document_id: "1" }], request_id: "r" })));
    expect(await createApi("https://x", async () => "t").listDocuments()).toHaveLength(1);
  });
});
