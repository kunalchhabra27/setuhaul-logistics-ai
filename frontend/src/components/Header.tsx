import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { Lock, Menu, Truck as TruckLogo, LogOut, CheckCircle2 } from "lucide-react";
import { services, type ServiceId, defaultServiceId } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { useTransition } from "../context/TransitionContext";
import { cn } from "../lib/cn";

interface HeaderProps {
  onToggleSidebar: () => void;
}

export default function Header({ onToggleSidebar }: HeaderProps) {
  const { serviceId } = useParams();
  const activeId = (serviceId as ServiceId) || defaultServiceId;
  const { isAuthed, logout, sessions } = useAuth();
  const { goToService } = useTransition();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-white/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
        <button
          onClick={onToggleSidebar}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-ink-soft transition hover:bg-cloud lg:hidden"
          aria-label="Toggle navigation"
        >
          <Menu className="h-5 w-5" />
        </button>

        <Link to="/" className="flex shrink-0 items-center gap-2">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-brand-pink via-brand-purple to-brand-orange text-white shadow-soft">
            <TruckLogo className="h-5 w-5" strokeWidth={2.25} />
          </span>
          <div className="leading-tight">
            <p className="text-[15px] font-bold tracking-tight text-ink">SetuHaul</p>
            <p className="-mt-0.5 text-[11px] font-medium text-mist">Transportation Portal</p>
          </div>
        </Link>

        <nav className="ml-4 hidden flex-1 items-center gap-1 lg:flex">
          {services.map((s) => {
            const active = s.id === activeId;
            const authed = isAuthed(s.id);
            return (
              <button
                key={s.id}
                onClick={() => goToService(s.id)}
                className={cn(
                  "group relative flex items-center gap-2 rounded-full px-3.5 py-2 text-sm font-semibold transition-all",
                  active ? "text-white shadow-soft" : "text-ink-soft hover:bg-cloud hover:text-ink"
                )}
                style={active ? { background: s.color } : undefined}
              >
                <s.icon className="h-4 w-4" strokeWidth={2.25} />
                {s.shortName}
                {authed ? (
                  <CheckCircle2 className={cn("h-3.5 w-3.5", active ? "text-white" : "text-emerald-500")} />
                ) : (
                  <Lock className={cn("h-3 w-3 opacity-60", active ? "text-white" : "text-mist")} />
                )}
                {active && (
                  <motion.span
                    layoutId="header-active-pill"
                    className="absolute inset-0 -z-10 rounded-full"
                    style={{ background: s.color }}
                    transition={{ type: "spring", stiffness: 380, damping: 32 }}
                  />
                )}
              </button>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <div className="relative">
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="flex items-center gap-2 rounded-full border border-line bg-white py-1.5 pl-1.5 pr-3 text-sm font-semibold text-ink shadow-sm transition hover:border-drivers/40"
            >
              <span
                className="grid h-7 w-7 place-items-center rounded-full text-xs font-bold text-white"
                style={{ background: "var(--color-drivers)" }}
              >
                {sessions[activeId]?.name?.[0]?.toUpperCase() ?? "?"}
              </span>
              <span className="hidden sm:inline">
                {sessions[activeId]?.name ?? "Guest"}
              </span>
            </button>

            {menuOpen && (
              <motion.div
                initial={{ opacity: 0, y: -6, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                className="absolute right-0 mt-2 w-64 overflow-hidden rounded-2xl border border-line bg-white p-2 shadow-pop"
              >
                <p className="px-3 pb-2 pt-1 text-xs font-semibold uppercase tracking-wide text-mist">
                  Signed-in sessions
                </p>
                {services.filter((s) => isAuthed(s.id)).length === 0 && (
                  <p className="px-3 pb-2 text-sm text-ink-soft">You're not signed in anywhere yet.</p>
                )}
                {services
                  .filter((s) => isAuthed(s.id))
                  .map((s) => (
                    <div
                      key={s.id}
                      className="flex items-center justify-between rounded-xl px-3 py-2 text-sm hover:bg-cloud"
                    >
                      <span className="flex items-center gap-2 font-medium text-ink">
                        <s.icon className="h-4 w-4" style={{ color: s.color }} />
                        {s.shortName}
                      </span>
                      <button
                        onClick={() => logout(s.id)}
                        className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-semibold text-ink-soft hover:bg-white hover:text-red-500"
                      >
                        <LogOut className="h-3.5 w-3.5" />
                        Sign out
                      </button>
                    </div>
                  ))}
              </motion.div>
            )}
          </div>
        </div>
      </div>

      <div className="flex gap-1 overflow-x-auto border-t border-line px-3 py-2 lg:hidden">
        {services.map((s) => {
          const active = s.id === activeId;
          return (
            <button
              key={s.id}
              onClick={() => goToService(s.id)}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition",
                active ? "text-white" : "bg-cloud text-ink-soft"
              )}
              style={active ? { background: s.color } : undefined}
            >
              <s.icon className="h-3.5 w-3.5" />
              {s.shortName}
            </button>
          );
        })}
      </div>
    </header>
  );
}
