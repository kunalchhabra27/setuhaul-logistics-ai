const KEY = "setuhaul.auth.v1";

export type ServiceSession = {
  name: string;
};

export function loadSessions<T>() {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as T) : ({} as T);
  } catch {
    return {} as T;
  }
}

export function saveSessions(value: unknown) {
  sessionStorage.setItem(KEY, JSON.stringify(value));
}
