import { type FormEvent, useState } from "react";
import { Button } from "../ui/Button";
import { sentence, signIn } from "../lib/api";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
    } catch (failure) {
      setError(sentence(failure));
      setBusy(false);
    }
  }

  return (
    <main className="center-page">
      <form className="card" onSubmit={submit} noValidate>
        <div style={{ display: "grid", gap: 4 }}>
          <h1>Stratum</h1>
          <p className="muted">Sign in to edit your posts.</p>
        </div>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            autoFocus
          />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>
        {error ? (
          <p className="error-line" role="alert">
            {error}
          </p>
        ) : null}
        <Button variant="primary" type="submit" busy={busy} disabled={!email || !password}>
          Sign in
        </Button>
        <p className="subtle" style={{ fontSize: 13 }}>
          New to Stratum? <a href="/">Create your account here</a>.
        </p>
      </form>
    </main>
  );
}
