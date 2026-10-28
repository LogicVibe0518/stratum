import { type FormEvent, useEffect, useState } from "react";
import { Button } from "../ui/Button";
import { Logo } from "../ui/Logo";
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
    <main className="landing">
      <header className="landing-hero">
        <h1>
          <Logo size={64} />
        </h1>
        <p className="landing-pitch">
          On-brand social posts from a brief, real photos and a brand kit, in minutes. Change anything by
          hand, like in Canva, or in plain English.
        </p>
      </header>
      <form className="card" onSubmit={submit} noValidate>
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
      <footer className="landing-foot subtle">Built by Rushil Shandil</footer>
    </main>
  );
}
