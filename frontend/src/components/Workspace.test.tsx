import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { makeApi, makeDoc } from "../testutils";
import { Workspace } from "./Workspace";

afterEach(() => vi.restoreAllMocks());

describe("Workspace", () => {
  it("polls while a document is processing and enables Ask once it is READY", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce([makeDoc({ status: "PROCESSING" })])
      .mockResolvedValue([makeDoc({ status: "READY" })]);
    render(<Workspace api={makeApi({ listDocuments: list })} pollMs={20} />);
    expect(await screen.findByText("Processing…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ask questions about/ })).toBeDisabled();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ask questions about/ })).toBeEnabled();
    const calls = list.mock.calls.length;
    await new Promise((r) => setTimeout(r, 80)); // nothing left processing => polling stops
    expect(list.mock.calls.length).toBe(calls);
  });

  it("selecting a document opens the question panel", async () => {
    render(<Workspace api={makeApi({ listDocuments: vi.fn().mockResolvedValue([makeDoc()]) })} />);
    await userEvent.click(await screen.findByRole("button", { name: /Ask questions about guide.pdf/ }));
    expect(screen.getByRole("heading", { name: /Ask about “guide.pdf”/ })).toBeInTheDocument();
  });

  it("deletes only after confirmation", async () => {
    const api = makeApi({ listDocuments: vi.fn().mockResolvedValue([makeDoc()]) });
    render(<Workspace api={api} />);
    const del = await screen.findByRole("button", { name: "Delete guide.pdf" });
    vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    await userEvent.click(del);
    expect(api.deleteDocument).not.toHaveBeenCalled();
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    await userEvent.click(del);
    await waitFor(() => expect(api.deleteDocument).toHaveBeenCalledWith("a".repeat(32)));
  });

  it("shows a friendly error if the list cannot load", async () => {
    const api = makeApi({ listDocuments: vi.fn().mockRejectedValue(new Error("Could not reach the server.")) });
    render(<Workspace api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server.");
  });

  it("surfaces action errors such as reprocess failures", async () => {
    const api = makeApi({
      listDocuments: vi.fn().mockResolvedValue([makeDoc({ status: "FAILED", error_detail: "x", error_code: "Y" })]),
      reprocess: vi.fn().mockRejectedValue(new Error("Cannot reprocess a PROCESSING document")),
    });
    render(<Workspace api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: "Reprocess guide.pdf" }));
    expect(await screen.findAllByRole("alert")).toEqual(expect.arrayContaining([expect.objectContaining({ textContent: "Cannot reprocess a PROCESSING document" })]));
  });
});
