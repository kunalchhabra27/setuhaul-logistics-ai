export type AuthUser = {
  email: string;
  phone?: string;
  displayName: string;
};

export function getAuthBootstrap() {
  return {
    supabaseUrl: import.meta.env.VITE_SUPABASE_URL ?? "",
    supabasePublishableKey: import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ?? "",
  };
}
