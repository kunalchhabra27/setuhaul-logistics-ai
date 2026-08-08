import { useParams } from "react-router-dom";
import { LifeBuoy, BookOpen, Activity, Sparkles, X, ChevronRight } from "lucide-react";
import { services, type ServiceId, defaultServiceId } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { useTransition } from "../context/TransitionContext";
import { cn } from "../lib/cn";

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

const resources = [
  { icon: BookOpen, label: "Documentation" },
  { icon: Activity, label: "System status" },
  { icon: LifeBuoy, label: "Support desk" },
];

export default function Sidebar({ open, onClose }: SidebarProps) {
  const { serviceId } = useParams();
  const activeId = (serviceId as ServiceId) || defaultServiceId;
  const { isAuthed } = useAuth();
  const { goToService } = useTransition();

  const content = (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-5 pb-2 pt-5 lg:pt-6">
        <p className="text-xs font-bold uppercase tracking-wider text-mist">Services</p>
        <button onClick={onClose} className="grid h-7 w-7 place-items-center rounded-lg hover:bg-cloud lg:hidden">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-6">
        <div className="flex flex-col gap-1.5">
          {services.map((s) => {
            const active = s.id === activeId;
            const authed = isAuthed(s.id);
            return (
              <button
                key={s.id}
                onClick={() => {
                  goToService(s.id);
                  onClose();
                }}
                className={cn(
                  "group flex items-start gap-3 rounded-2xl border px-3.5 py-3 text-left transition-all",
                  active
                    ? "border-transparent shadow-soft"
                    : "border-transparent hover:border-line hover:bg-cloud"
                )}
                style={active ? { background: s.colorSoft } : undefined}
              >
                <span
                  className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl text-white transition-transform group-hover:scale-105"
                  style={{ background: s.color }}
                >
                  <s.icon className="h-4.5 w-4.5" strokeWidth={2.25} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-sm font-bold text-ink">{s.shortName}</span>
                    {active && (
                      <ChevronRight className="h-3.5 w-3.5" style={{ color: s.color }} />
                    )}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-ink-soft">{s.tagline}</span>
                  <span
                    className={cn(
                      "mt-1.5 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide",
                      authed ? "bg-emerald-100 text-emerald-700" : "bg-black/5 text-mist"
                    )}
                  >
                    {authed ? "Signed in" : "Locked"}
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        <p className="mb-2 mt-8 px-1 text-xs font-bold uppercase tracking-wider text-mist">Resources</p>
        <div className="flex flex-col gap-1">
          {resources.map((r) => (
            <button
              key={r.label}
              className="flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-ink-soft transition hover:bg-cloud hover:text-ink"
            >
              <r.icon className="h-4 w-4" />
              {r.label}
            </button>
          ))}
        </div>

        <div className="mt-8 rounded-2xl bg-gradient-to-br from-[#12121a] to-[#2b2b3d] p-4 text-white">
          <Sparkles className="h-5 w-5 text-brand-orange" />
          <p className="mt-2 text-sm font-bold">Built for coordinators</p>
          <p className="mt-1 text-xs text-white/70">
            One portal for TMS, WMS, gate check-in and driver conversations — so nothing falls
            through the cracks during a disruption spike.
          </p>
        </div>
      </div>
    </div>
  );

  return (
    <>
      <aside className="sticky top-16 hidden h-[calc(100svh-4rem)] w-[280px] shrink-0 border-r border-line bg-white lg:block">
        {content}
      </aside>

      {open && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={onClose} />
          <aside className="absolute left-0 top-0 h-full w-[300px] bg-white shadow-pop">{content}</aside>
        </div>
      )}
    </>
  );
}
