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

// Session storage is scoped per portal (drivers/tms/wms/checkin) rather than one
// shared key -- this lets a different person be signed in to each portal at the
// same time, in the same browser, without one login evicting another. Each
// portal's own key is only ever read/written by code acting on that portal's
// behalf (see serviceId threaded through every function below).
function storageKey(serviceId: string) {
  return `setuhaul.supabase.session.${serviceId}`;
}

function loadSession(serviceId: string): SupabaseSession | null {
  try {
    const raw = localStorage.getItem(storageKey(serviceId));
    return raw ? (JSON.parse(raw) as SupabaseSession) : null;
  } catch {
    return null;
  }
}

function saveSession(serviceId: string, session: SupabaseSession | null) {
  try {
    if (session) localStorage.setItem(storageKey(serviceId), JSON.stringify(session));
    else localStorage.removeItem(storageKey(serviceId));
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

// listeners are scoped per serviceId so an auth event on one portal's session
// (e.g. TMS signing in) never notifies a subscriber listening for a different
// portal (e.g. Drivers), and therefore never overwrites that portal's state.
const listenersByService = new Map<string, Set<AuthListener>>();

function listenersFor(serviceId: string) {
  let set = listenersByService.get(serviceId);
  if (!set) {
    set = new Set();
    listenersByService.set(serviceId, set);
  }
  return set;
}

function emit(serviceId: string, event: "SIGNED_IN" | "SIGNED_OUT" | "TOKEN_REFRESHED" | "USER_UPDATED", session: SupabaseSession | null) {
  saveSession(serviceId, session);
  listenersFor(serviceId).forEach((listener) => listener(event, session));
}

export const supabase = {
  isConfigured,
  auth: {
    async signUp(
      serviceId: string,
      { email, password, options }: { email: string; password: string; options?: { data?: Record<string, unknown> } }
    ) {
      const data = await request<{ user: SupabaseUser; session: SupabaseSession | null }>("/auth/v1/signup", {
        method: "POST",
        body: JSON.stringify({ email, password, data: options?.data ?? {} }),
      });
      if (data.session) emit(serviceId, "SIGNED_IN", data.session);
      return { data, error: null };
    },
    async signInWithPassword(serviceId: string, { email, password }: { email: string; password: string }) {
      const data = await request<{ access_token: string; refresh_token: string; expires_in: number; token_type: string; user: SupabaseUser }>("/auth/v1/token?grant_type=password", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const session: SupabaseSession = { ...data };
      emit(serviceId, "SIGNED_IN", session);
      return { data: { session, user: data.user }, error: null };
    },
    async signOut(serviceId: string) {
      const session = loadSession(serviceId);
      if (session?.access_token) {
        await fetch(`${supabaseUrl}/auth/v1/logout`, {
          method: "POST",
          headers: {
            apikey: supabaseKey,
            Authorization: `Bearer ${session.access_token}`,
          },
        }).catch(() => null);
      }
      emit(serviceId, "SIGNED_OUT", null);
      return { error: null };
    },
    async getSession(serviceId: string) {
      return { data: { session: loadSession(serviceId) }, error: null };
    },
    onAuthStateChange(serviceId: string, listener: AuthListener) {
      const set = listenersFor(serviceId);
      set.add(listener);
      listener("TOKEN_REFRESHED", loadSession(serviceId));
      return {
        data: {
          subscription: {
            unsubscribe() {
              set.delete(listener);
            },
          },
        },
      };
    },
  },
};

export type { SupabaseSession, SupabaseUser };
