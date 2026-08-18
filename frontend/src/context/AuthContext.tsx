import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { services, type ServiceId } from "../data/services";
import {
  ensureAuthState,
  getCurrentSession,
  isAuthConfigured,
  signInWithEmail,
  signOut,
  signUpWithEmail,
  subscribeAuthState,
  type SupabaseSession,
  type SupabaseUser,
} from "../auth/authService";

type ServiceSession = {
  name: string;
};

// Sessions are tracked per portal (drivers/tms/wms/checkin) rather than as one
// shared value -- each portal can have a different person signed in at the same
// time, in the same browser, and none of them log the others out.
type SessionsByService = Partial<Record<ServiceId, SupabaseSession | null>>;

interface AuthContextValue {
  loading: boolean;
  sessions: Partial<Record<ServiceId, ServiceSession>>;
  hasSession: (id: ServiceId) => boolean;
  isAuthed: (id: ServiceId) => boolean;
  canAccess: (id: ServiceId) => boolean;
  login: (id: ServiceId, email: string, password: string) => Promise<void>;
  register: (id: ServiceId, name: string, email: string, password: string) => Promise<void>;
  logout: (id: ServiceId) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const ALL_SERVICE_IDS = services.map((s) => s.id);

function displayNameFor(user: SupabaseUser | null) {
  const fullName = user?.user_metadata?.full_name;
  if (typeof fullName === "string" && fullName.trim()) return fullName.trim();
  if (user?.email) return user.email.split("@")[0];
  return "Guest";
}

function roleFor(user: SupabaseUser | null): ServiceId | null {
  const role =
    (user?.user_metadata?.service_role as string | undefined) ??
    (user?.app_metadata?.service_role as string | undefined);
  return role === "drivers" || role === "tms" || role === "wms" || role === "checkin"
    ? role
    : null;
}

function initialSessions(): SessionsByService {
  const value: SessionsByService = {};
  ALL_SERVICE_IDS.forEach((id) => {
    value[id] = getCurrentSession(id);
  });
  return value;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [sessionsByService, setSessionsByService] = useState<SessionsByService>(initialSessions);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isAuthConfigured) {
      setLoading(false);
      return;
    }
    let mounted = true;
    let pending = ALL_SERVICE_IDS.length;
    const unsubscribes = ALL_SERVICE_IDS.map((id) => {
      void ensureAuthState(id)
        .catch(() => null)
        .finally(() => {
          pending -= 1;
          if (mounted && pending <= 0) setLoading(false);
        });
      return subscribeAuthState(id, (next) => {
        if (!mounted) return;
        setSessionsByService((prev) => ({ ...prev, [id]: next }));
      });
    });
    return () => {
      mounted = false;
      unsubscribes.forEach((unsubscribe) => unsubscribe());
    };
  }, []);

  const hasSession = useCallback((id: ServiceId) => Boolean(sessionsByService[id]), [sessionsByService]);

  const isAuthed = useCallback(
    (id: ServiceId) => {
      const session = sessionsByService[id];
      return Boolean(session) && roleFor(session!.user ?? null) === id;
    },
    [sessionsByService]
  );

  // canAccess mirrors isAuthed: a session for a portal is only usable for that
  // same portal's role. Kept as a distinct function since callers reach for it
  // semantically ("can this signed-in user open this portal?").
  const canAccess = isAuthed;

  const sessions = useMemo<Partial<Record<ServiceId, ServiceSession>>>(() => {
    const value: Partial<Record<ServiceId, ServiceSession>> = {};
    ALL_SERVICE_IDS.forEach((id) => {
      const session = sessionsByService[id];
      if (session && roleFor(session.user ?? null) === id) {
        value[id] = { name: displayNameFor(session.user ?? null) };
      }
    });
    return value;
  }, [sessionsByService]);

  const login = useCallback(async (id: ServiceId, email: string, password: string) => {
    await signInWithEmail(id, email, password);
  }, []);

  const register = useCallback(async (id: ServiceId, name: string, email: string, password: string) => {
    await signUpWithEmail(id, email, password, name, id);
  }, []);

  const logout = useCallback(async (id: ServiceId) => {
    await signOut(id);
  }, []);

  const value = useMemo(
    () => ({ loading, sessions, hasSession, isAuthed, canAccess, login, register, logout }),
    [loading, sessions, hasSession, isAuthed, canAccess, login, register, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
