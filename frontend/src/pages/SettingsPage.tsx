import type { AccessSettings } from "../api/types";
import { useApi } from "../api/useApi";
import { AsyncView } from "../components/AsyncView";
import { Card } from "../components/Card";

export function SettingsPage() {
  const access = useApi<AccessSettings>("/settings/access");
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Settings &amp; access</h1>
          <p>Roles, permissions, users and environment</p>
        </div>
      </header>
      <AsyncView state={access} skeletonRows={6}>
        {(a) => (
          <div className="stack">
            <div className="grid two">
              {a.roles.map((role) => (
                <Card key={role.name} title={role.name} subtitle={<code>{role.group}</code>}>
                  <p style={{ marginTop: 0, color: "var(--text-secondary)" }}>{role.description}</p>
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {role.permissions.map((p) => <li key={p}>{p}</li>)}
                  </ul>
                </Card>
              ))}
            </div>
            <div className="grid two">
              <Card title="Users" subtitle={a.users_note}>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th scope="col">Name</th><th scope="col">Email</th><th scope="col">Role</th></tr></thead>
                    <tbody>
                      {a.users.map((u) => <tr key={u.email}><td>{u.name}</td><td className="mono">{u.email}</td><td>{u.role}</td></tr>)}
                    </tbody>
                  </table>
                </div>
              </Card>
              <Card title="Environment" subtitle="Reported by the API (no secrets are ever returned)">
                <dl className="kv">
                  {Object.entries(a.environment).map(([key, value]) => (
                    <div key={key} style={{ display: "contents" }}>
                      <dt>{key.replace(/_/g, " ")}</dt>
                      <dd className="mono">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </Card>
            </div>
          </div>
        )}
      </AsyncView>
    </>
  );
}
