import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeApi, makeDoc, pdf } from "../testutils";
import type { Api, Auth, QueryResult } from "../types";
import { AskPanel } from "./AskPanel";
import { DocumentList } from "./DocumentList";
import { SignIn } from "./SignIn";
import { UploadPanel } from "./UploadPanel";

const user = () => userEvent.setup({ applyAccept: false }); // so OUR validation is what gets tested

describe("UploadPanel", () => {
  it("rejects non-PDF files without calling the API", async () => {
    const api = makeApi();
    render(<UploadPanel api={api} onUploaded={vi.fn()} />);
    await user().upload(screen.getByLabelText(/PDF file/), new File(["x"], "notes.txt", { type: "text/plain" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Please choose a PDF");
    expect(api.createUpload).not.toHaveBeenCalled();
  });

  it("rejects oversized and empty files", async () => {
    const api = makeApi();
    render(<UploadPanel api={api} onUploaded={vi.fn()} maxBytes={50} />);
    await user().upload(screen.getByLabelText(/PDF file/), pdf("big.pdf", 51));
    expect(await screen.findByRole("alert")).toHaveTextContent("too large");
    await user().upload(screen.getByLabelText(/PDF file/), pdf("empty.pdf", 0));
    expect(await screen.findByRole("alert")).toHaveTextContent("empty");
    expect(api.createUpload).not.toHaveBeenCalled();
  });

  it("shows progress while uploading, then a success message", async () => {
    let finish!: () => void;
    const api = makeApi({
      uploadFile: vi.fn((_t, _f, onProgress) => {
        onProgress(0.5);
        return new Promise<void>((r) => (finish = r));
      }),
    });
    const onUploaded = vi.fn();
    render(<UploadPanel api={api} onUploaded={onUploaded} />);
    await user().upload(screen.getByLabelText(/PDF file/), pdf("report.pdf"));
    const bar = await screen.findByRole("progressbar");
    expect(bar).toHaveValue(0.5);
    expect(screen.getByText(/Uploading… 50%/)).toBeInTheDocument();
    expect(api.createUpload).toHaveBeenCalledWith("report.pdf");
    finish();
    expect(await screen.findByRole("status")).toHaveTextContent("Uploaded report.pdf");
    expect(onUploaded).toHaveBeenCalled();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("shows an error when the upload fails", async () => {
    const api = makeApi({ uploadFile: vi.fn().mockRejectedValue(new Error("Upload failed (403)")) });
    render(<UploadPanel api={api} onUploaded={vi.fn()} />);
    await user().upload(screen.getByLabelText(/PDF file/), pdf());
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload failed (403)");
  });
});

describe("DocumentList", () => {
  const noop = vi.fn();
  const props = { loadError: null, selectedId: null, onSelect: noop, onDelete: noop, onReprocess: noop };

  it("shows loading, empty and error states", () => {
    const { rerender } = render(<DocumentList {...props} docs={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading documents");
    rerender(<DocumentList {...props} docs={[]} />);
    expect(screen.getByText(/No documents yet/)).toBeInTheDocument();
    rerender(<DocumentList {...props} docs={null} loadError="Could not reach the server." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server.");
  });

  it("shows status text and safe error details; only READY docs can be asked", async () => {
    const onSelect = vi.fn();
    const docs = [
      makeDoc({ document_id: "1".repeat(32), filename: "ok.pdf" }),
      makeDoc({ document_id: "2".repeat(32), filename: "bad.pdf", status: "FAILED", error_code: "NOT_A_PDF", error_detail: "File is not a PDF (bad signature)" }),
      makeDoc({ document_id: "3".repeat(32), filename: "busy.pdf", status: "PROCESSING" }),
    ];
    render(<DocumentList {...props} docs={docs} onSelect={onSelect} />);
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Processing…")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("File is not a PDF");
    expect(screen.getByRole("alert")).toHaveTextContent("NOT_A_PDF");
    expect(screen.getByRole("button", { name: "Ask questions about bad.pdf" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ask questions about busy.pdf" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Ask questions about ok.pdf" }));
    expect(onSelect).toHaveBeenCalledWith("1".repeat(32));
    expect(screen.queryByRole("button", { name: "Reprocess busy.pdf" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reprocess bad.pdf" })).toBeInTheDocument();
  });
});

const ANSWER: QueryResult = {
  request_id: "req-123", answered: true, answer: "Keep pH between 5.8 and 6.2.",
  citations: [{ chunk_id: "p002-c000", filename: "guide.pdf", page: 2, excerpt: "Keep the nutrient solution at a pH between 5.8 and 6.2." }],
};

describe("AskPanel", () => {
  it("asks the user to pick a document first", () => {
    render(<AskPanel api={makeApi()} doc={null} />);
    expect(screen.getByText(/Select a ready document/)).toBeInTheDocument();
  });

  it("disables Ask until there is text, shows loading, then the answer with citations", async () => {
    let resolve!: (r: QueryResult) => void;
    const api = makeApi({ query: vi.fn(() => new Promise<QueryResult>((r) => (resolve = r))) });
    render(<AskPanel api={api} doc={makeDoc()} />);
    const ask = screen.getByRole("button", { name: "Ask" });
    expect(ask).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Your question"), "What pH?");
    expect(ask).toBeEnabled();
    await userEvent.click(ask);
    expect(await screen.findByText(/Searching the document/)).toBeInTheDocument();
    expect(api.query).toHaveBeenCalledWith("a".repeat(32), "What pH?");
    resolve(ANSWER);
    expect(await screen.findByText("Keep pH between 5.8 and 6.2.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "guide.pdf — page 2" })).toBeInTheDocument();
    expect(screen.getByText(/nutrient solution at a pH/)).toBeInTheDocument();
    expect(screen.getByText(/req-123/)).toBeInTheDocument();
  });

  it("clicking a citation opens the PDF at the cited page", async () => {
    const win = { location: { href: "" }, close: vi.fn(), opener: {} };
    vi.spyOn(window, "open").mockReturnValue(win as unknown as Window);
    const api = makeApi({ query: vi.fn().mockResolvedValue(ANSWER) });
    render(<AskPanel api={api} doc={makeDoc()} />);
    await userEvent.type(screen.getByLabelText("Your question"), "pH?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await userEvent.click(await screen.findByRole("button", { name: "guide.pdf — page 2" }));
    await waitFor(() => expect(win.location.href).toBe("https://s3.example/doc.pdf?sig=1#page=2"));
    expect(win.opener).toBeNull();
    expect(api.downloadUrl).toHaveBeenCalledWith("a".repeat(32));
  });

  it("reports a blocked pop-up instead of failing silently", async () => {
    vi.spyOn(window, "open").mockReturnValue(null);
    const api = makeApi({ query: vi.fn().mockResolvedValue(ANSWER) });
    render(<AskPanel api={api} doc={makeDoc()} />);
    await userEvent.type(screen.getByLabelText("Your question"), "pH?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await userEvent.click(await screen.findByRole("button", { name: "guide.pdf — page 2" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/blocked/);
  });

  it("shows the not-found answer without a sources section", async () => {
    const api: Api = makeApi({
      query: vi.fn().mockResolvedValue({ request_id: "r2", answered: false, citations: [], answer: "I could not find the answer to that in the provided document." }),
    });
    render(<AskPanel api={api} doc={makeDoc()} />);
    await userEvent.type(screen.getByLabelText("Your question"), "price?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/could not find the answer/)).toBeInTheDocument();
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
  });

  it("shows a server error message", async () => {
    const api = makeApi({ query: vi.fn().mockRejectedValue(new Error("The answer service failed. Please try again.")) });
    render(<AskPanel api={api} doc={makeDoc()} />);
    await userEvent.type(screen.getByLabelText("Your question"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("answer service failed");
  });
});

describe("SignIn", () => {
  const authWith = (over: Partial<Auth>): Auth => ({
    email: () => null, restore: async () => false, signOut: vi.fn(),
    signIn: vi.fn().mockResolvedValue({ status: "ok" }), completeNewPassword: vi.fn().mockResolvedValue(undefined), ...over,
  });

  it("signs in and reports wrong passwords", async () => {
    const onDone = vi.fn();
    const auth = authWith({ signIn: vi.fn().mockRejectedValueOnce(new Error("Incorrect username or password.")).mockResolvedValue({ status: "ok" }) });
    render(<SignIn auth={auth} onDone={onDone} />);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect username or password.");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("handles the first-login new-password challenge", async () => {
    const onDone = vi.fn();
    const auth = authWith({ signIn: vi.fn().mockResolvedValue({ status: "new_password_required" }) });
    render(<SignIn auth={auth} onDone={onDone} />);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "temp");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await userEvent.type(await screen.findByLabelText("New password"), "A-long-new-Passw0rd!");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(auth.completeNewPassword).toHaveBeenCalledWith("A-long-new-Passw0rd!");
  });
});
