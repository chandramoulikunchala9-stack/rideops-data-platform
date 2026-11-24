import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError } from "../api/client";
import type { Me, Membership, Role } from "./types";

// Who is signed in and which workspace they are looking at. `status` distinguishes
// "Lens is switched off on this server" (404 on /auth/me) from "not signed in" (401).
type Status = "loading" | "signed_in" | "signed_out" | "disabled" | "error";

interface LensState {
  status: Status;
  me: Me | null;
  workspace: Membership | null;
  setWorkspace: (id: string) => void;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  setMe: (me: Me) => void;
  can: (minimum: Role) => boolean;
}

const RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2, owner: 3 };
const STORAGE_KEY = "lens-workspace";

const LensContext = createContext<LensState | null>(null);

async function loadMe(): Promise<{ status: Status; me: Me | null }> {
  try {
    const data = await api.get<Me>("/auth/me");
    if (!data || typeof data !== "object" || !("user" in data)) {
      return { status: "disabled", me: null }; // something answered, but not Lens
    }
    return { status: "signed_in", me: data };
  } catch (err) {
    const e = err as ApiError;
    return { status: e.status === 401 ? "signed_out" : e.status === 404 ? "disabled" : "error", me: null };
  }
}

function storedWorkspace(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function LensProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMeState] = useState<Me | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(storedWorkspace);

  const setMe = useCallback((value: Me) => {
    setMeState(value);
    setStatus("signed_in");
  }, []);

  const apply = useCallback((result: { status: Status; me: Me | null }) => {
    setMeState(result.me);
    setStatus(result.status);
  }, []);

  const refresh = useCallback(async () => apply(await loadMe()), [apply]);

  useEffect(() => {
    let cancelled = false;
    void loadMe().then((result) => {
      if (!cancelled) apply(result);
    });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  const setWorkspace = useCallback((id: string) => {
    setWorkspaceId(id);
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* the choice just won't persist */
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setMeState(null);
      setStatus("signed_out");
    }
  }, []);

  const workspace = useMemo(() => {
    if (!me) return null;
    return me.memberships.find((m) => m.workspace_id === workspaceId)
      ?? me.memberships.find((m) => m.workspace_id === me.current_workspace_id)
      ?? me.memberships[0]
      ?? null;
  }, [me, workspaceId]);

  const can = useCallback((minimum: Role) => (workspace ? RANK[workspace.role] >= RANK[minimum] : false), [workspace]);

  const value = useMemo(
    () => ({ status, me, workspace, setWorkspace, refresh, signOut, setMe, can }),
    [status, me, workspace, setWorkspace, refresh, signOut, setMe, can],
  );
  return <LensContext.Provider value={value}>{children}</LensContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useLens(): LensState {
  const ctx = useContext(LensContext);
  if (!ctx) throw new Error("useLens must be used inside <LensProvider>");
  return ctx;
}
