import { Lock } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import fnbLogo from "@/assets/fnb-logo.png";
import hero from "@/assets/login-hero.jpg";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { ErrorAlert, Field } from "@/components/ui";

export default function LoginPage() {
  const { me, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [devMode, setDevMode] = useState(false);

  useEffect(() => {
    api<{ mode: string }>("/auth/mode")
      .then((r) => setDevMode(r.mode === "dev"))
      .catch(() => setDevMode(false));
  }, []);

  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (me) return <Navigate to={from} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <section className="login-hero">
        <img
          className="login-hero-image"
          src={hero}
          alt="FNB Ghana Smart Email Assistance: we read your emails, so you get faster answers."
        />
      </section>

      <section className="login-side">
        <form className="card login-card" onSubmit={submit}>
          <img className="login-logo" src={fnbLogo} alt="First National Bank Ghana" />
          <div>
            <h1>Sign in to Smart Email Assistance</h1>
            <p className="card-sub">Use your F.Number and Password.</p>
          </div>
          {devMode && (
            <div className="alert alert-warn" role="note">
              Development sign-in is on: any F Number and password will work. Use “admin” to sign in as an
              administrator.
            </div>
          )}
          <ErrorAlert error={error} />
          <Field label="F Number">
            <input
              className="input"
              autoComplete="username"
              autoFocus
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </Field>
          <Field label="Password">
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <button className="btn btn-primary" disabled={busy}>
            <Lock size={16} /> {busy ? "Signing in…" : "Sign in"}
          </button>
          <p className="card-sub">For FNB staff only. All activity is recorded.</p>
        </form>
      </section>
    </div>
  );
}
