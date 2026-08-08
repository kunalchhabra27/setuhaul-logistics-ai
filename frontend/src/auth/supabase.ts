export type AuthUser = {
  email: string;
  phone?: string;
  displayName: string;
};

export function getAuthBootstrap() {
  return {
    supabaseUrl: import.meta.env.VITE_SUPABASE_URL ?? "",
    supabaseAnonKey: import.meta.env.VITE_SUPABASE_ANON_KEY ?? "",
  };
}
