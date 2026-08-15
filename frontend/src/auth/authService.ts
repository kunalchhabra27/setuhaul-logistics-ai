import { supabase, type SupabaseSession, type SupabaseUser } from "./supabaseClient";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "") + "/api/v1";

type AuthStateListener = (session: SupabaseSession | null) => void;

// Every piece of session state here is keyed by serviceId (drivers/tms/wms/checkin)
// so the four portals can each hold their own signed-in user at the same time, in
// the same browser, without one portal's login/logout affecting another's.
const currentSessions = new Map<string, SupabaseSession | null>();
const listenersByService = new Map<string, Set<AuthStateListener>>();
const initializedServices = new Set<string>();

function listenersFor(serviceId: string) {
  let set = listenersByService.get(serviceId);
  if (!set) {
    set = new Set();
    listenersByService.set(serviceId, set);
  }
  return set;
}

function emit(serviceId: string, session: SupabaseSession | null) {
  currentSessions.set(serviceId, session);
  listenersFor(serviceId).forEach((listener) => listener(session));
}

export async function ensureAuthState(serviceId: string) {
  if (initializedServices.has(serviceId)) return currentSessions.get(serviceId) ?? null;
  initializedServices.add(serviceId);
  const { data } = await supabase.auth.getSession(serviceId);
  emit(serviceId, data.session ?? null);
  supabase.auth.onAuthStateChange(serviceId, (_event, session) => {
    emit(serviceId, session);
  });
  return currentSessions.get(serviceId) ?? null;
}

export function subscribeAuthState(serviceId: string, listener: AuthStateListener) {
  listenersFor(serviceId).add(listener);
  listener(currentSessions.get(serviceId) ?? null);
  return () => listenersFor(serviceId).delete(listener);
}

export async function signUpWithEmail(serviceId: string, email: string, password: string, name: string, serviceRole: string) {
  const { data, error } = await supabase.auth.signUp(serviceId, {
    email,
    password,
    options: {
      data: {
        full_name: name,
        service_role: serviceRole,
      },
    },
  });
  if (error) throw error;
  emit(serviceId, data.session);
  return data;
}

export async function signInWithEmail(serviceId: string, email: string, password: string) {
  const { data, error } = await supabase.auth.signInWithPassword(serviceId, { email, password });
  if (error) throw error;
  emit(serviceId, data.session);
  return data;
}

export async function signOut(serviceId: string) {
  // Revoke our own Redis-cached session first, while the access token is
  // still available -- this is what makes logout take effect immediately
  // for the fast (cache-hit) auth path server-side, rather than waiting up
  // to SESSION_TTL_SECONDS for it to expire on its own. Best-effort: a
  // failure here (network blip, backend down) must never block sign-out
  // itself, since Supabase's own signOut() below is what's actually
  // authoritative for the session.
  const token = getAccessToken(serviceId);
  if (token) {
    try {
      await fetch(`${apiBaseUrl}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      // Ignored -- see comment above.
    }
  }

  const { error } = await supabase.auth.signOut(serviceId);
  if (error) throw error;
  emit(serviceId, null);
}

export function getCurrentSession(serviceId: string) {
  return currentSessions.get(serviceId) ?? null;
}

export function getCurrentUser(serviceId: string): SupabaseUser | null {
  return currentSessions.get(serviceId)?.user ?? null;
}

export function getAccessToken(serviceId: string) {
  return currentSessions.get(serviceId)?.access_token ?? null;
}

// Fallback for when the proactive refresh timer in supabaseClient.ts missed
// its window (e.g. a background tab whose timers were throttled by the
// browser while asleep). api.ts calls this once on a 401 before giving up,
// so a request made right after a laptop wakes from sleep self-heals
// instead of surfacing as "the chatbot isn't responding".
export async function refreshAccessToken(serviceId: string): Promise<boolean> {
  const { data, error } = await supabase.auth.refreshSession(serviceId);
  return !error && Boolean(data.session);
}

export const isAuthConfigured = supabase.isConfigured;

export type { SupabaseSession, SupabaseUser };
