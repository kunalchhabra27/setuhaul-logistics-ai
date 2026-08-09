import { supabase, type SupabaseSession, type SupabaseUser } from "./supabaseClient";

type AuthStateListener = (session: SupabaseSession | null) => void;

let currentSession: SupabaseSession | null = null;
const listeners = new Set<AuthStateListener>();
let initialized = false;

function readStoredSession() {
  try {
    const raw = localStorage.getItem("setuhaul.supabase.session");
    return raw ? (JSON.parse(raw) as SupabaseSession) : null;
  } catch {
    return null;
  }
}

function emit(session: SupabaseSession | null) {
  currentSession = session;
  listeners.forEach((listener) => listener(session));
}

export async function ensureAuthState() {
  if (initialized) return currentSession;
  initialized = true;
  const { data } = await supabase.auth.getSession();
  emit(data.session ?? null);
  supabase.auth.onAuthStateChange((_event, session) => {
    emit(session);
  });
  return currentSession;
}

export function subscribeAuthState(listener: AuthStateListener) {
  listeners.add(listener);
  listener(currentSession);
  return () => listeners.delete(listener);
}

export async function signUpWithEmail(email: string, password: string, name: string, serviceRole: string) {
  const { data, error } = await supabase.auth.signUp({
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
  emit(data.session);
  return data;
}

export async function signInWithEmail(email: string, password: string) {
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
  emit(data.session);
  return data;
}

export async function signOut() {
  const { error } = await supabase.auth.signOut();
  if (error) throw error;
  emit(null);
}

export function getCurrentSession() {
  return currentSession;
}

export function getCurrentUser(): SupabaseUser | null {
  return currentSession?.user ?? null;
}

export function getAccessToken() {
  return currentSession?.access_token ?? readStoredSession()?.access_token ?? null;
}

export const isAuthConfigured = supabase.isConfigured;

export type { SupabaseSession, SupabaseUser };
