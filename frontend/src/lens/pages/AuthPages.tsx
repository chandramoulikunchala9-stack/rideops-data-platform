import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../../api/client";
import { useLens } from "../LensContext";
import type { Me, Workspace } from "../types";

// Auth screens (wireframes §3). Errors are shown inline and announced (role="alert").

function AuthShell({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <main className="auth-shell" id="main">
      <div className="auth-card card">
        <p className="auth-brand">RideOps Lens</p>
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
        {children}
      </div>
    </main>
  );
}

function ErrorText({ error }: { error: string | null }) {
  return error ? <p className="form-error" role="alert">{error}</p> : null;
}

function safeNext(raw: string | null): string {
  // Only same-app paths: never redirect to another origin after login.
  return raw && raw.startsWith("/") && !raw.startsWith("//") ? raw : "/lens";
}

function strength(password: string): { score: number; label: string } {
  let score = 0;
  if (password.length >= 12) score += 2;
  if (password.length >= 16) score += 1;
  if (/[A-Z]/.test(password) && /[a-z]/.test(password)) score += 1;
  if (/\d/.test(password) || /[^\w]/.test(password)) score += 1;
  const label = password.length < 12 ? "Too short (12+ characters)" : score >= 4 ? "Strong" : "Fair";
  return { score, label };
}

function PasswordField({ id, label, value, onChange, autoComplete, meter = false }: {
  id: string; label: string; value: string; onChange: (v: string) => void; autoComplete: string; meter?: boolean;
}) {
  const [show, setShow] = useState(false);
  const s = strength(value);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="input-row">
        <input id={id} type={show ? "text" : "password"} value={value} autoComplete={autoComplete} required
               onChange={(e) => onChange(e.target.value)} aria-describedby={meter ? `${id}-strength` : undefined} />
        <button type="button" className="btn small" onClick={() => setShow((v) => !v)} aria-pressed={show}>
          {show ? "Hide" : "Show"}
        </button>
      </div>
      {meter && value && (
        <p id={`${id}-strength`} className="hint">
          <meter min={0} max={5} value={s.score} aria-hidden="true" /> {s.label}
        </p>
      )}
    </div>
  );
}

export function LoginPage() {
  const { setMe } = useLens();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMe(await api.post<Me>("/auth/login", { email, password }));
      navigate(safeNext(params.get("next")), { replace: true });
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Log in" subtitle="See the health and analytics of any database.">
      <form onSubmit={submit} className="stack">
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </div>
        <PasswordField id="password" label="Password" value={password} onChange={setPassword} autoComplete="current-password" />
        <ErrorText error={error} />
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Logging in…" : "Log in"}</button>
      </form>
      <p className="auth-links">
        <Link to="/forgot-password">Forgot password?</Link> · No account? <Link to="/signup">Sign up</Link>
      </p>
    </AuthShell>
  );
}

export function SignupPage() {
  const { setMe } = useLens();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password.length < 12) {
      setError("Use at least 12 characters.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setMe(await api.post<Me>("/auth/signup", { email, password, display_name: name }));
      navigate("/lens", { replace: true });
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Create your account" subtitle="Connect a database by URL and see its status in seconds.">
      <form onSubmit={submit} className="stack">
        <div className="field">
          <label htmlFor="name">Name</label>
          <input id="name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required maxLength={80} />
        </div>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </div>
        <PasswordField id="password" label="Password" value={password} onChange={setPassword} autoComplete="new-password" meter />
        <ErrorText error={error} />
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      </form>
      <p className="auth-links">Already have an account? <Link to="/login">Log in</Link></p>
    </AuthShell>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.post("/auth/password/forgot", { email });
      setSent(true);
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  return (
    <AuthShell title="Reset password">
      {sent ? (
        <p role="status">If an account exists for <strong>{email}</strong>, we sent a reset link. It works for 30 minutes.</p>
      ) : (
        <form onSubmit={submit} className="stack">
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <ErrorText error={error} />
          <button className="btn primary" type="submit">Send reset link</button>
        </form>
      )}
      <p className="auth-links"><Link to="/login">Back to log in</Link></p>
    </AuthShell>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("The passwords don't match.");
      return;
    }
    try {
      await api.post("/auth/password/reset", { token: params.get("token") ?? "", password });
      setDone(true);
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  return (
    <AuthShell title="Set a new password">
      {done ? (
        <p role="status">Password changed. You were signed out everywhere. <Link to="/login">Log in</Link></p>
      ) : (
        <form onSubmit={submit} className="stack">
          <PasswordField id="password" label="New password" value={password} onChange={setPassword} autoComplete="new-password" meter />
          <PasswordField id="confirm" label="Confirm" value={confirm} onChange={setConfirm} autoComplete="new-password" />
          <ErrorText error={error} />
          <button className="btn primary" type="submit">Set new password</button>
        </form>
      )}
    </AuthShell>
  );
}

/** Runs a one-time token action once on mount (StrictMode-safe). */
function useTokenAction(action: (token: string) => Promise<void>) {
  const [params] = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<{ status: "working" | "done" | "error"; message?: string }>(() =>
    token ? { status: "working" } : { status: "error", message: "This link is missing its token." },
  );
  const ran = useRef(false);
  useEffect(() => {
    if (ran.current || !token) return;
    ran.current = true;
    action(token)
      .then(() => setState({ status: "done" }))
      .catch((err: unknown) => setState({ status: "error", message: (err as ApiError).message }));
  }, [action, token]);
  return state;
}

export function VerifyEmailPage() {
  const { refresh } = useLens();
  const [action] = useState(() => async (token: string) => {
    await api.post("/auth/verify-email", { token });
    await refresh();
  });
  const state = useTokenAction(action);
  return (
    <AuthShell title="Verify email">
      {state.status === "working" && <p role="status">Verifying…</p>}
      {state.status === "done" && <p role="status">Email verified. <Link to="/lens">Continue to Lens</Link></p>}
      {state.status === "error" && <p className="form-error" role="alert">{state.message}</p>}
    </AuthShell>
  );
}

export function AcceptInvitePage() {
  const { status, refresh, setWorkspace } = useLens();
  const [params] = useSearchParams();
  const [workspace, setJoined] = useState<Workspace | null>(null);
  const [action] = useState(() => async (token: string) => {
    const ws = await api.post<Workspace>(`/invitations/${encodeURIComponent(token)}/accept`);
    setJoined(ws);
    setWorkspace(ws.id);
    await refresh();
  });
  if (status === "signed_out") {
    const next = encodeURIComponent(`/invite?token=${params.get("token") ?? ""}`);
    return (
      <AuthShell title="You're invited">
        <p>Log in or sign up with the email address the invitation was sent to.</p>
        <p className="auth-links"><Link to={`/login?next=${next}`}>Log in</Link> · <Link to="/signup">Sign up</Link></p>
      </AuthShell>
    );
  }
  if (status === "loading") return <AuthShell title="You're invited"><p role="status">Loading…</p></AuthShell>;
  return <AcceptInvite action={action} workspace={workspace} />;
}

function AcceptInvite({ action, workspace }: { action: (t: string) => Promise<void>; workspace: Workspace | null }) {
  const state = useTokenAction(action);
  return (
    <AuthShell title="You're invited">
      {state.status === "working" && <p role="status">Joining…</p>}
      {state.status === "done" && workspace && (
        <p role="status">You joined <strong>{workspace.name}</strong> as {workspace.role}. <Link to="/lens">Open Lens</Link></p>
      )}
      {state.status === "error" && <p className="form-error" role="alert">{state.message}</p>}
    </AuthShell>
  );
}
