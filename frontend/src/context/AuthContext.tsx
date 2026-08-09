import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { ServiceId } from "../data/services";
import { ensureAuthState, getCurrentSession, isAuthConfigured, signInWithEmail, signOut, signUpWithEmail, subscribeAuthState, type SupabaseSession, type SupabaseUser } from "../auth/authService";

type ServiceSession = {
  name: string;
};

interface AuthContextValue {
  session: SupabaseSession | null;
  user: SupabaseUser | null;
  loading: boolean;
  serviceRole: ServiceId | null;
  sessions: Partial<Record<ServiceId, ServiceSession>>;
  isAuthed: (id: ServiceId) => boolean;
  canAccess: (id: ServiceId) => boolean;
  login: (id: ServiceId, email: string, password: string) => Promise<void>;
  register: (id: ServiceId, name: string, email: string, password: string) => Promise<void>;
  logout: (id: ServiceId) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SupabaseSession | null>(getCurrentSession());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    if (!isAuthConfigured) {
      setLoading(false);
      return () => {
        mounted = false;
      };
    }
    void ensureAuthState()
      .catch(() => null)
      .finally(() => {
        if (mounted) setLoading(false);
      });
    const unsubscribe = subscribeAuthState((next) => {
      setSession(next);
      setLoading(false);
    });
    return () => {
      mounted = false;
      unsubscribe();
    };
  }, []);

  const user = session?.user ?? null;
  const name = displayNameFor(user);
  const serviceRole = roleFor(user);

  const sessions = useMemo<Partial<Record<ServiceId, ServiceSession>>>(() => {
    const value: Partial<Record<ServiceId, ServiceSession>> = {};
    if (session && serviceRole) {
      ([serviceRole] as ServiceId[]).forEach((id) => {
        value[id] = { name };
      });
    }
    return value;
  }, [name, session, serviceRole]);

  const isAuthed = useCallback((id: ServiceId) => Boolean(session) && serviceRole === id, [session, serviceRole]);
  const canAccess = useCallback((id: ServiceId) => Boolean(session) && serviceRole === id, [session, serviceRole]);

  const login = useCallback(async (_id: ServiceId, email: string, password: string) => {
    await signInWithEmail(email, password);
  }, []);

  const register = useCallback(async (id: ServiceId, name: string, email: string, password: string) => {
    await signUpWithEmail(email, password, name, id);
  }, []);

  const logout = useCallback(async (_id: ServiceId) => {
    await signOut();
  }, []);

  const value = useMemo(
    () => ({ session, user, loading, serviceRole, sessions, isAuthed, canAccess, login, register, logout }),
    [session, user, loading, serviceRole, sessions, isAuthed, canAccess, login, register, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
