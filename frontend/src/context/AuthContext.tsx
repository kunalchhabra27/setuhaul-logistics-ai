import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { ServiceId } from "../data/services";
import { loadSessions, saveSessions, type ServiceSession } from "../auth/authStore";

interface AuthContextValue {
  sessions: Partial<Record<ServiceId, ServiceSession>>;
  isAuthed: (id: ServiceId) => boolean;
  login: (id: ServiceId, name: string) => void;
  logout: (id: ServiceId) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<Partial<Record<ServiceId, ServiceSession>>>(loadSessions);

  const persist = useCallback((next: Partial<Record<ServiceId, ServiceSession>>) => {
    setSessions(next);
    try {
      saveSessions(next);
    } catch {
      /* ignore */
    }
  }, []);

  const login = useCallback(
    (id: ServiceId, name: string) => {
      persist({ ...sessions, [id]: { name: name || "Guest" } });
    },
    [sessions, persist]
  );

  const logout = useCallback(
    (id: ServiceId) => {
      const next = { ...sessions };
      delete next[id];
      persist(next);
    },
    [sessions, persist]
  );

  const isAuthed = useCallback((id: ServiceId) => Boolean(sessions[id]), [sessions]);

  const value = useMemo(() => ({ sessions, isAuthed, login, logout }), [sessions, isAuthed, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
