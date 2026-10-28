import { useState } from "react";
import type { Auth } from "../types";

export function SignIn({ auth, onDone }: { auth: Auth; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [needsNew, setNeedsNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (needsNew) {
        await auth.completeNewPassword(newPassword);
        onDone();
      } else {
        const r = await auth.signIn(email.trim(), password);
        if (r.status === "new_password_required") setNeedsNew(true);
        else onDone();
      }
    } catch (err) {
      // Cognito's message is user-safe ("Incorrect username or password.", password policy, ...)
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card signin" onSubmit={submit} aria-labelledby="signin-title">
      <h2 id="signin-title">{needsNew ? "Choose a new password" : "Sign in"}</h2>
      {needsNew ? (
        <>
          <p className="muted">Your account was created by an administrator. Pick a password (12+ characters, upper, lower, number, symbol).</p>
          <label htmlFor="new-password">New password</label>
          <input id="new-password" type="password" autoComplete="new-password" required minLength={12}
            value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        </>
      ) : (
        <>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? "Please wait…" : needsNew ? "Set password" : "Sign in"}</button>
    </form>
  );
}
