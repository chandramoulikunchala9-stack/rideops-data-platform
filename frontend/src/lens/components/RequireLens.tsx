import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useLens } from "../LensContext";

/** Gate for signed-in Lens pages: shows why Lens is unavailable, or sends visitors to log in. */
export function RequireLens({ children }: { children: ReactNode }) {
  const { status, workspace } = useLens();
  const location = useLocation();
  if (status === "loading") {
    return <div role="status" className="state">Loading…</div>;
  }
  if (status === "disabled") {
    return (
      <div className="state">
        <h1>Lens is not enabled</h1>
        <p>This server runs the demo warehouse reports only. Set <code>LENS_ENABLED=true</code> on the API to connect your own databases.</p>
      </div>
    );
  }
  if (status === "error") {
    return <div className="state error" role="alert">Cannot reach the API. Is the backend running?</div>;
  }
  if (status === "signed_out") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (!workspace) {
    return <div className="state">You are not a member of any workspace yet.</div>;
  }
  return <>{children}</>;
}
