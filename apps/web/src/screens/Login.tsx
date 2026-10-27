import { type FormEvent, useEffect, useState } from "react";
import { Button } from "../ui/Button";
import { demoAvailable, sentence, signIn, tryDemo } from "../lib/api";
import { CLASSIC } from "../lib/router";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [demo, setDemo] = useState(false);
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    void demoAvailable().then(setDemo);
  }, []);

  async function openDemo() {
    setOpening(true);
    setError("");
    try {
      await tryDemo();
    } catch (failure) {
      setError(sentence(failure));
      setOpening(false);
    }
  }

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
          <p className="muted">On-brand social posts from a brief, real photos and a brand kit.</p>
        </div>
        {demo ? (
          <div className="demo-entry">
            <Button variant="primary" type="button" busy={opening} onClick={() => void openDemo()}>
              Try the demo
            </Button>
            <p className="subtle" style={{ fontSize: 13 }}>
              No account needed: a shared demo agency with fictional clients. Change anything on its posts.
            </p>
            <div className="or" aria-hidden>
              <span>or sign in</span>
            </div>
          </div>
        ) : (
          <p className="muted">Sign in to edit your posts.</p>
        )}
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
            autoFocus={!demo}
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
        <Button
          variant={demo ? "secondary" : "primary"}
          type="submit"
          busy={busy}
          disabled={!email || !password}
        >
          Sign in
        </Button>
        <p className="subtle" style={{ fontSize: 13 }}>
          New to Stratum? <a href={CLASSIC}>Create your account here</a>.
        </p>
      </form>
    </main>
  );
}
