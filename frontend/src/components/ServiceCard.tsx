import { motion } from "framer-motion";
import { ArrowRight, CheckCircle2, Lock } from "lucide-react";
import type { ServiceDef } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { useTransition } from "../context/TransitionContext";
import { cn } from "../lib/cn";

export default function ServiceCard({ service, featured = false }: { service: ServiceDef; featured?: boolean }) {
  const { isAuthed } = useAuth();
  const { goToService } = useTransition();
  const authed = isAuthed(service.id);

  return (
    <motion.button
      onClick={() => goToService(service.id)}
      whileHover={{ y: -6 }}
      whileTap={{ scale: 0.98 }}
      className={cn(
        "group relative flex w-full flex-col overflow-hidden rounded-3xl border border-line bg-white p-6 text-left shadow-soft transition-shadow hover:shadow-pop",
        featured && "ring-2 ring-offset-2"
      )}
      style={featured ? { ["--tw-ring-color" as string]: service.color, ["--tw-ring-offset-color" as string]: "var(--color-cloud)" } : undefined}
    >
      {featured && (
        <span
          className="absolute right-4 top-4 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-white"
          style={{ background: service.color }}
        >
          Default
        </span>
      )}

      <div
        className="absolute -right-10 -top-10 h-32 w-32 rounded-full opacity-20 blur-2xl transition-opacity group-hover:opacity-40"
        style={{ background: service.color }}
      />

      <span
        className="grid h-12 w-12 place-items-center rounded-2xl text-white shadow-soft transition-transform duration-300 group-hover:-rotate-3 group-hover:scale-105"
        style={{ background: service.color }}
      >
        <service.icon className="h-6 w-6" strokeWidth={2.25} />
      </span>

      <h3 className="mt-4 text-lg font-bold text-ink">{service.name}</h3>
      <p className="text-sm font-semibold" style={{ color: service.color }}>
        {service.tagline}
      </p>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">{service.description}</p>

      <ul className="mt-4 flex flex-col gap-1.5">
        {service.highlights.map((h) => (
          <li key={h.label} className="flex items-center gap-2 text-xs font-medium text-ink-soft">
            <h.icon className="h-3.5 w-3.5" style={{ color: service.color }} />
            {h.label}
          </li>
        ))}
      </ul>

      <div className="mt-5 flex items-center justify-between border-t border-line pt-4">
        <div>
          <p className="text-lg font-extrabold text-ink">{service.stat.value}</p>
          <p className="text-[11px] text-mist">{service.stat.label}</p>
        </div>

        <span
          className={cn(
            "flex items-center gap-1.5 rounded-full px-3.5 py-2 text-xs font-bold text-white transition-transform group-hover:translate-x-0.5"
          )}
          style={{ background: service.color }}
        >
          {authed ? (
            <>
              <CheckCircle2 className="h-3.5 w-3.5" /> Open
            </>
          ) : (
            <>
              <Lock className="h-3.5 w-3.5" /> Sign in
            </>
          )}
          <ArrowRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </motion.button>
  );
}
