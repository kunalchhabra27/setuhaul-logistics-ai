type SupabaseUser = {
  id: string;
  email?: string;
  user_metadata?: Record<string, unknown>;
  app_metadata?: Record<string, unknown>;
};

type SupabaseSession = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
  user: SupabaseUser;
};

type AuthListener = (event: "SIGNED_IN" | "SIGNED_OUT" | "TOKEN_REFRESHED" | "USER_UPDATED", session: SupabaseSession | null) => void;

const supabaseUrl = (import.meta.env.VITE_SUPABASE_URL ?? "").replace(/\/$/, "");
const supabaseKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ?? "";
const isConfigured = Boolean(supabaseUrl && supabaseKey);

const STORAGE_KEY = "setuhaul.supabase.session";

function loadSession(): SupabaseSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SupabaseSession) : null;
  } catch {
    return null;
  }
}

function saveSession(session: SupabaseSession | null) {
  try {
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

async function request<T>(path: string, init: RequestInit = {}) {
  if (!isConfigured) {
    throw new Error("Supabase Auth is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY.");
  }
  const response = await fetch(`${supabaseUrl}${path}`, {
    ...init,
    headers: {
      apikey: supabaseKey,
      Authorization: `Bearer ${supabaseKey}`,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error((payload as { msg?: string; error_description?: string; message?: string }).msg ?? (payload as { error_description?: string }).error_description ?? (payload as { message?: string }).message ?? "Supabase Auth request failed.");
  }
  return payload as T;
}

export const supabase = {
  isConfigured,
  auth: {
    async signUp({ email, password, options }: { email: string; password: string; options?: { data?: Record<string, unknown> } }) {
      const data = await request<{ user: SupabaseUser; session: SupabaseSession | null }>("/auth/v1/signup", {
        method: "POST",
        body: JSON.stringify({ email, password, data: options?.data ?? {} }),
      });
      if (data.session) saveSession(data.session);
      return { data, error: null };
    },
    async signInWithPassword({ email, password }: { email: string; password: string }) {
      const data = await request<{ access_token: string; refresh_token: string; expires_in: number; token_type: string; user: SupabaseUser }>("/auth/v1/token?grant_type=password", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const session: SupabaseSession = { ...data };
      saveSession(session);
      return { data: { session, user: data.user }, error: null };
    },
    async signOut() {
      const session = loadSession();
      if (session?.access_token) {
        await fetch(`${supabaseUrl}/auth/v1/logout`, {
          method: "POST",
          headers: {
            apikey: supabaseKey,
            Authorization: `Bearer ${session.access_token}`,
          },
        }).catch(() => null);
      }
      saveSession(null);
      emit("SIGNED_OUT", null);
      return { error: null };
    },
    async getSession() {
      return { data: { session: loadSession() }, error: null };
    },
    onAuthStateChange(listener: AuthListener) {
      listeners.add(listener);
      listener("TOKEN_REFRESHED", loadSession());
      return {
        data: {
          subscription: {
            unsubscribe() {
              listeners.delete(listener);
            },
          },
        },
      };
    },
  },
};

const listeners = new Set<AuthListener>();

function emit(event: "SIGNED_IN" | "SIGNED_OUT" | "TOKEN_REFRESHED" | "USER_UPDATED", session: SupabaseSession | null) {
  saveSession(session);
  listeners.forEach((listener) => listener(event, session));
}

export type { SupabaseSession, SupabaseUser };
