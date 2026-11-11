import { render, screen } from "@testing-library/react";
import { App } from "./App";

const config = (body: unknown) =>
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) =>
    String(url).endsWith("/config.json")
      ? new Response(JSON.stringify(body))
      : new Response(JSON.stringify({ documents: [] })),
  ));

describe("App boot", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("goes straight to the workspace, with no sign-in, on a no-login demo stack", async () => {
    config({ demo: false, noAuth: true, apiUrl: "https://api.example", region: "us-east-2" });
    render(<App />);
    expect(await screen.findByText(/Public demo: no sign-in/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sign out/i })).not.toBeInTheDocument();
  });

  it("still asks for a sign-in on a normal stack", async () => {
    config({ demo: false, apiUrl: "https://api.example", region: "us-east-2", userPoolId: "us-east-2_x", clientId: "c" });
    render(<App />);
    expect(await screen.findByLabelText(/password/i)).toBeInTheDocument();
  });
});
