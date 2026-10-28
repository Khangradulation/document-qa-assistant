import { useEffect, useState } from "react";
import { createApi } from "./api";
import { createAuth } from "./auth";
import { Workspace } from "./components/Workspace";
import { SignIn } from "./components/SignIn";
import { createDemo } from "./demo";
import type { Api, AppConfig, Auth } from "./types";

type Boot =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; config: AppConfig; api: Api; auth: Auth };

async function boot(): Promise<Boot> {
  try {
    const res = await fetch("/config.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`config.json: ${res.status}`);
    const config = (await res.json()) as AppConfig;
    if (config.demo) return { phase: "ready", config, ...createDemo() };
    if (!config.apiUrl || !config.userPoolId || !config.clientId) throw new Error("config.json is incomplete");
    const auth = createAuth(config.userPoolId, config.clientId);
    return { phase: "ready", config, auth, api: createApi(config.apiUrl, auth.getToken) };
  } catch (e) {
    return { phase: "error", message: e instanceof Error ? e.message : "Could not load configuration" };
  }
}

export function App() {
  const [state, setState] = useState<Boot>({ phase: "loading" });
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  useEffect(() => {
    void boot().then(async (b) => {
      setState(b);
      if (b.phase === "ready") setSignedIn(await b.auth.restore());
    });
  }, []);

  return (
    <>
      <header className="top">
        <h1>Document Q&amp;A Assistant</h1>
        {state.phase === "ready" && signedIn && (
          <div className="row">
            <span className="muted small">{state.auth.email()}</span>
            <button type="button" className="secondary" onClick={() => { state.auth.signOut(); setSignedIn(false); }}>Sign out</button>
          </div>
        )}
      </header>
      {state.phase === "ready" && state.config.demo && (
        <p className="banner" role="note">Demo mode: sample data in your browser only. No AWS, no real AI. Set <code>demo</code> to false in <code>config.json</code> for the real app.</p>
      )}
      <main>
        {state.phase === "loading" && <p role="status">Loading…</p>}
        {state.phase === "error" && <p role="alert" className="error">Configuration error: {state.message}</p>}
        {state.phase === "ready" && signedIn === false && <SignIn auth={state.auth} onDone={() => setSignedIn(true)} />}
        {state.phase === "ready" && signedIn && <Workspace api={state.api} maxBytes={state.config.maxUploadBytes} />}
      </main>
    </>
  );
}
