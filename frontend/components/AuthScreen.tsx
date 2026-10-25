"use client";

import { useState } from "react";
import { api, type Me } from "@/lib/api";

export function AuthScreen({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") await api.signup(email, password, name);
      else await api.login(email, password);
      onSignedIn(await api.me());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center p-4">
      <form onSubmit={submit} className="panel flex w-full max-w-sm flex-col gap-3 p-6">
        <h1 className="mono text-xl font-bold text-[var(--accent)]">DataForge</h1>
        <p className="text-sm muted">{mode === "login" ? "Sign in to your workspace." : "Create an account."}</p>
        {mode === "signup" && (
          <input className="input py-2 text-sm" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
        )}
        <input className="input py-2 text-sm" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        <input
          className="input py-2 text-sm"
          type="password"
          placeholder={mode === "signup" ? "Password (10+ characters)" : "Password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
          minLength={mode === "signup" ? 10 : undefined}
          required
        />
        {error && <p className="text-xs text-red-400">{error}</p>}
        <button type="submit" className="btn btn-primary py-2" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        <button type="button" className="text-xs muted hover:text-[var(--accent)]" onClick={() => setMode(mode === "login" ? "signup" : "login")}>
          {mode === "login" ? "No account? Create one" : "Have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}
