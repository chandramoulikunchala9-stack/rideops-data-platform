import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError, uploadFile } from "../../api/client";
import { useApi } from "../../api/useApi";
import { Card } from "../../components/Card";
import { useLens } from "../LensContext";
import type { Connection, EngineInfo, ParsedUrl, TestResult } from "../types";

// Add connection wizard (wireframes §6): 1 Engine → 2 Details → 3 Security & network → 4 Test & save.
// The password stays in component state only; it is sent to the API for the test and the save,
// never shown again, and never put back into a URL.

type Step = 1 | 2 | 3 | 4;
const STEPS = ["Engine", "Details", "Security & network", "Test & save"];

interface Form {
  engine: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  options: Record<string, string>;
  tls_mode: string;
  ca_cert_pem: string;
  name: string;
  environment: "prod" | "staging" | "dev";
  tags: string;
  check_interval_s: number;
}

const EMPTY: Form = {
  engine: "", host: "", port: "", database: "", username: "", password: "", options: {}, tls_mode: "require",
  ca_cert_pem: "", name: "", environment: "dev", tags: "", check_interval_s: 300,
};

const STAGE_LABEL: Record<string, string> = {
  DNS: "DNS", NETWORK_POLICY: "Network policy", TCP: "TCP", TLS: "TLS", AUTH: "Authentication",
  DB_SELECT: "Database", PRIVILEGES: "Privileges", CAPABILITIES: "Capabilities",
};
const ALL_STAGES = Object.keys(STAGE_LABEL);

function Stepper({ step }: { step: Step }) {
  return (
    <ol className="stepper" aria-label="Progress">
      {STEPS.map((label, i) => {
        const n = (i + 1) as Step;
        const state = n < step ? "done" : n === step ? "current" : "todo";
        return (
          <li key={label} data-state={state} aria-current={state === "current" ? "step" : undefined}>
            <span className="step-n">{state === "done" ? "✔" : n}</span> {label}
          </li>
        );
      })}
    </ol>
  );
}

function draftBody(f: Form) {
  if (f.engine === "sqlite") return { engine: "sqlite", database: f.database };
  return {
    engine: f.engine,
    host: f.host.trim(),
    port: f.port ? Number(f.port) : undefined,
    database: f.database.trim() || undefined,
    username: f.username.trim() || undefined,
    options: f.options,
    tls_mode: f.tls_mode,
    secret: { password: f.password || undefined, ca_cert_pem: f.ca_cert_pem || undefined },
  };
}

export function StageList({ result }: { result: TestResult }) {
  const seen = new Set(result.stages.map((s) => s.stage));
  return (
    <ul className="stage-list" aria-live="polite">
      {result.stages.map((s) => {
        const tone = s.ok ? (s.warning ? "warning" : "good") : s.warning ? "warning" : "critical";
        return (
          <li key={s.stage} data-tone={tone}>
            <span className="stage-icon" aria-hidden="true">{tone === "good" ? "✔" : tone === "warning" ? "⚠" : "✗"}</span>
            <span className="stage-name">{STAGE_LABEL[s.stage] ?? s.stage}</span>
            <span className="stage-msg">{s.code && !s.ok ? <code>{s.code}</code> : null} {s.message}</span>
            <span className="stage-ms mono">{s.ms} ms</span>
          </li>
        );
      })}
      {ALL_STAGES.filter((s) => !seen.has(s)).map((s) => (
        <li key={s} data-tone="skipped"><span className="stage-icon" aria-hidden="true">◌</span>
          <span className="stage-name">{STAGE_LABEL[s]}</span><span className="stage-msg muted">not run</span><span /></li>
      ))}
    </ul>
  );
}

export function AddConnectionPage() {
  const { workspace, me, can } = useLens();
  const navigate = useNavigate();
  const ws = workspace?.workspace_id ?? "";
  const engines = useApi<{ engines: EngineInfo[]; egress_ips: string[] }>("/meta/engines");
  const settings = useApi<{ settings: { allow_insecure_connections: boolean } }>(ws ? `/workspaces/${ws}` : null);
  const [step, setStep] = useState<Step>(1);
  const [form, setForm] = useState<Form>(EMPTY);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [forceReason, setForceReason] = useState("");

  const set = <K extends keyof Form>(key: K, value: Form[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    if (key !== "name" && key !== "environment" && key !== "tags" && key !== "check_interval_s") setTest(null);
  };

  async function parseUrl(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const parsed = await api.post<ParsedUrl>(`/workspaces/${ws}/connections/parse-url`, { url });
      // The URL may contain the password: take it into the secret field, then forget the URL.
      const password = (() => {
        try {
          return decodeURIComponent(new URL(url.replace(/^[\w+]+:/, "http:")).password);
        } catch {
          return "";
        }
      })();
      setForm((f) => ({
        ...f, engine: parsed.engine, host: parsed.host, port: String(parsed.port), database: parsed.database ?? "",
        username: parsed.username ?? "", password: parsed.has_password ? password : f.password, options: parsed.options,
        tls_mode: parsed.tls_mode, name: f.name || parsed.database || parsed.host,
      }));
      setUrl("");
      setTest(null);
      setStep(2);
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  async function runTest() {
    setBusy(true);
    setError(null);
    try {
      setTest(await api.post<TestResult>(`/workspaces/${ws}/connections/test`, draftBody(form)));
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!test) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<Connection>(`/workspaces/${ws}/connections`, {
        test_id: test.test_id,
        draft: draftBody(form),
        name: form.name.trim(),
        environment: form.environment,
        tags: form.tags.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean),
        check_interval_s: form.check_interval_s,
        force_reason: !test.ok && forceReason ? forceReason : undefined,
      });
      navigate(`/lens/connections/${created.id}`);
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  if (!can("editor")) {
    return <div className="state">Your role ({workspace?.role}) can view connections but not add them.</div>;
  }
  const insecureAllowed = settings.data?.settings?.allow_insecure_connections ?? false;
  const isFile = form.engine === "sqlite";
  const detailsValid = isFile ? Boolean(form.name.trim() && form.database)
    : form.engine && form.host.trim() && form.name.trim() && form.username.trim();

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const r = await uploadFile<{ upload_id: string; filename: string; bytes: number }>(`/workspaces/${ws}/uploads/sqlite`, file);
      setForm((f) => ({ ...f, database: r.upload_id, host: "upload", name: f.name || r.filename.replace(/\.(sqlite3?|db)$/i, "") }));
      setTest(null);
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Add connection</h1>
          <p>Lens connects read-only, encrypts the password at rest and never shows it again.</p>
        </div>
        <Link className="btn" to="/lens">Cancel</Link>
      </header>
      {me && !me.user.email_verified && (
        <p className="notice" role="note">Verify your email first: saving a connection needs a verified address. Check your inbox.</p>
      )}
      <Stepper step={step} />
      {error && <p className="form-error" role="alert">{error}</p>}

      {step === 1 && (
        <Card title="Paste a connection URL" subtitle="Fastest. Your password moves to a secure field and is never stored in the URL.">
          <form onSubmit={parseUrl} className="stack">
            <div className="field">
              <label htmlFor="url">Connection URL</label>
              <input id="url" className="mono" value={url} onChange={(e) => setUrl(e.target.value)} autoComplete="off"
                     spellCheck={false} placeholder="postgresql://lens_ro@db.example.com:5432/shop?sslmode=verify-full" />
            </div>
            <div><button className="btn primary" type="submit" disabled={!url.trim()}>Continue with URL</button></div>
          </form>
          <p className="divider-text">or choose an engine</p>
          <div className="engine-grid">
            {(engines.data?.engines ?? []).map((e) => (
              <button key={e.engine} type="button" className="engine-tile" disabled={!e.available}
                      onClick={() => { setForm({ ...EMPTY, engine: e.engine, port: String(e.default_port ?? "") }); setStep(2); }}>
                <strong>{e.label}</strong>
                <span className="muted">{e.available ? "Available" : "Coming soon"}</span>
              </button>
            ))}
          </div>
        </Card>
      )}

      {step === 2 && (
        <Card title="Connection details" subtitle={`Engine: ${form.engine}`}>
          {isFile ? (
            <form className="form-grid" onSubmit={(e) => { e.preventDefault(); setStep(4); }}>
              <div className="field span-2">
                <label htmlFor="sqlite-file">SQLite file (.sqlite, .db, up to 200 MB)</label>
                <input id="sqlite-file" type="file" accept=".sqlite,.sqlite3,.db" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
                <p className="hint">{busy ? "Uploading…" : form.database ? "Uploaded. Lens opens it read-only." : "The file is stored for this workspace only."}</p>
              </div>
              <div className="field span-2"><label htmlFor="name">Display name *</label>
                <input id="name" value={form.name} onChange={(e) => set("name", e.target.value)} required maxLength={80} /></div>
              <div className="toolbar span-2">
                <button type="button" className="btn" onClick={() => setStep(1)}>← Back</button>
                <button type="submit" className="btn primary" disabled={!detailsValid || busy}>Next →</button>
              </div>
            </form>
          ) : (
          <form className="form-grid" onSubmit={(e) => { e.preventDefault(); setStep(3); }}>
            <div className="field span-2">
              <label htmlFor="name">Display name *</label>
              <input id="name" value={form.name} onChange={(e) => set("name", e.target.value)} required maxLength={80} />
            </div>
            <fieldset className="field span-2">
              <legend>Environment</legend>
              <div className="segmented">
                {(["prod", "staging", "dev"] as const).map((env) => (
                  <label key={env}><input type="radio" name="env" checked={form.environment === env}
                                          onChange={() => set("environment", env)} />
                    {env === "prod" ? "Production" : env === "staging" ? "Staging" : "Development"}</label>
                ))}
              </div>
            </fieldset>
            <div className="field"><label htmlFor="host">Host *</label>
              <input id="host" value={form.host} onChange={(e) => set("host", e.target.value)} required autoComplete="off" /></div>
            <div className="field"><label htmlFor="port">Port</label>
              <input id="port" inputMode="numeric" value={form.port} onChange={(e) => set("port", e.target.value.replace(/\D/g, ""))} /></div>
            <div className="field"><label htmlFor="database">Database</label>
              <input id="database" value={form.database} onChange={(e) => set("database", e.target.value)} /></div>
            <div className="field"><label htmlFor="username">Username *</label>
              <input id="username" value={form.username} onChange={(e) => set("username", e.target.value)} required autoComplete="off" /></div>
            <div className="field span-2"><label htmlFor="password">Password</label>
              <input id="password" type="password" value={form.password} onChange={(e) => set("password", e.target.value)}
                     autoComplete="new-password" aria-describedby="password-hint" />
              <p id="password-hint" className="hint">Encrypted at rest, never shown again.</p></div>
            <div className="field"><label htmlFor="tags">Tags (comma-separated)</label>
              <input id="tags" value={form.tags} onChange={(e) => set("tags", e.target.value)} /></div>
            <div className="field"><label htmlFor="interval">Check every</label>
              <select id="interval" value={form.check_interval_s} onChange={(e) => set("check_interval_s", Number(e.target.value))}>
                <option value={60}>1 minute</option><option value={300}>5 minutes</option>
                <option value={900}>15 minutes</option><option value={3600}>60 minutes</option>
              </select></div>
            <p className="notice span-2">A read-only user is recommended. After the test, Lens shows the exact SQL to create one.</p>
            <div className="toolbar span-2">
              <button type="button" className="btn" onClick={() => setStep(1)}>← Back</button>
              <button type="submit" className="btn primary" disabled={!detailsValid}>Next →</button>
            </div>
          </form>
          )}
        </Card>
      )}

      {step === 3 && (
        <Card title="Security & network">
          <form className="stack" onSubmit={(e) => { e.preventDefault(); setStep(4); }}>
            <fieldset className="field">
              <legend>Encryption (TLS)</legend>
              {[
                ["require", "Require: encrypt, don't verify the certificate"],
                ["verify-ca", "Verify CA: encrypt and trust only a known CA"],
                ["verify-full", "Verify full: CA and host name (recommended for production)"],
                ["disable", insecureAllowed ? "Disabled (insecure)" : "Disabled (blocked by workspace policy)"],
              ].map(([mode = "", label]) => (
                <label key={mode} className="radio-row">
                  <input type="radio" name="tls" checked={form.tls_mode === mode} disabled={mode === "disable" && !insecureAllowed}
                         onChange={() => set("tls_mode", mode)} /> {label}
                </label>
              ))}
            </fieldset>
            <div className="field">
              <label htmlFor="ca">CA certificate (optional, PEM)</label>
              <textarea id="ca" rows={4} className="mono" value={form.ca_cert_pem} onChange={(e) => set("ca_cert_pem", e.target.value)}
                        placeholder="-----BEGIN CERTIFICATE-----" />
              <input type="file" accept=".pem,.crt" aria-label="Upload CA certificate file"
                     onChange={(e) => { const file = e.target.files?.[0]; if (file) void file.text().then((t) => set("ca_cert_pem", t)); }} />
            </div>
            <div className="notice">
              <strong>How Lens reaches the database:</strong> directly over the internet.{" "}
              {engines.data?.egress_ips.length
                ? <>Allowlist these IPs in your firewall: <code>{engines.data.egress_ips.join(", ")}</code></>
                : "This server has no fixed egress IPs configured."}{" "}
              SSH tunnels (v1.x) and the Lens agent (v2) are planned.
            </div>
            <div className="toolbar">
              <button type="button" className="btn" onClick={() => setStep(2)}>← Back</button>
              <button type="submit" className="btn primary">Next →</button>
            </div>
          </form>
        </Card>
      )}

      {step === 4 && (
        <Card title="Test & save" subtitle={isFile ? `${form.name} · uploaded SQLite file` : `${form.name} · ${form.host}${form.port ? `:${form.port}` : ""}${form.database ? `/${form.database}` : ""}`}>
          <div className="stack">
            <div><button type="button" className="btn primary" onClick={runTest} disabled={busy}>
              {busy && !test ? "Testing…" : test ? "Re-run test" : "▶ Test connection"}</button></div>
            {test && <StageList result={test} />}
            {test?.grant_script && (
              <details className="grant">
                <summary>This role can write. Show the SQL for a read-only role</summary>
                <pre className="mono">{test.grant_script}</pre>
                <button type="button" className="btn small" onClick={() => void navigator.clipboard?.writeText(test.grant_script ?? "")}>Copy</button>
              </details>
            )}
            {test && !test.ok && can("admin") && (
              <div className="field">
                <label htmlFor="force">Save anyway (admins only): reason, logged in the audit trail</label>
                <input id="force" value={forceReason} onChange={(e) => setForceReason(e.target.value)} maxLength={500} />
              </div>
            )}
            {test && (
              <p className="notice" role="status">
                {test.ok ? `Ready to save. Monitoring every ${form.check_interval_s / 60} min.` : "The test failed. Fix the details and retry."}
              </p>
            )}
            <div className="toolbar">
              <button type="button" className="btn" onClick={() => setStep(isFile ? 2 : 3)}>← Back</button>
              <button type="button" className="btn primary" onClick={save}
                      disabled={busy || !test || (!test.ok && !(can("admin") && forceReason.trim()))}>Save connection</button>
            </div>
          </div>
        </Card>
      )}
    </>
  );
}
