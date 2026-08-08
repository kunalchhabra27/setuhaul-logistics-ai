import { useEffect, useMemo, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Clock, Loader2, RefreshCw, Send } from "lucide-react";
import { getService } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { ApiClientError } from "../services/api";
import { completeUnload, fetchCheckInStatus, gateCheckIn, markDocked, updateQueue } from "../services/checkinApi";
import { listShipments } from "../services/tmsApi";
import { suggestSlots } from "../services/dockSchedulerApi";
import { driverChatHealth } from "../services/driverChatApi";
import type { CheckInRecord, DockSuggestion, ShipmentSummary } from "../types/api";

export default function PortalWorkspace() {
  const { serviceId } = useParams();
  const service = getService(serviceId);
  const { isAuthed, sessions, logout } = useAuth();
  const [checkin, setCheckin] = useState<CheckInRecord | null>(null);
  const [shipments, setShipments] = useState<ShipmentSummary[]>([]);
  const [dockSuggestions, setDockSuggestions] = useState<DockSuggestion[]>([]);
  const [driverStatus, setDriverStatus] = useState<string>("Loading...");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const activeShipmentId = "SHP1006";

  if (!service) return <Navigate to="/" replace />;
  if (!isAuthed(service.id)) return <Navigate to={`/auth/${service.id}`} replace />;

  const name = sessions[service.id]?.name ?? "there";

  useEffect(() => {
    if (service.id !== "checkin") return;
    void refreshCheckin();
  }, [service.id]);

  useEffect(() => {
    if (service.id !== "tms") return;
    void (async () => {
      try {
        const items = await listShipments();
        setShipments(items);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load shipments.");
      }
    })();
  }, [service.id]);

  useEffect(() => {
    if (service.id !== "wms") return;
    void (async () => {
      try {
        const items = await suggestSlots({ shipment_id: activeShipmentId, limit: 4 });
        setDockSuggestions(items);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load slot suggestions.");
      }
    })();
  }, [service.id]);

  useEffect(() => {
    if (service.id !== "drivers") return;
    void (async () => {
      try {
        const res = await driverChatHealth();
        setDriverStatus(`${res.system} is ${res.status}`);
      } catch (err) {
        setDriverStatus(err instanceof Error ? err.message : "Driver chat unavailable");
      }
    })();
  }, [service.id]);

  async function refreshCheckin() {
    setError("");
    try {
      const current = await fetchCheckInStatus(activeShipmentId);
      setCheckin(current);
    } catch (err) {
      const apiError = err as ApiClientError;
      if (apiError.status === 404) {
        setCheckin(null);
      } else {
        setError(apiError.message);
      }
    }
  }

  async function mutateCheckin(action: () => Promise<CheckInRecord>, successText: string) {
    setBusy(successText);
    setError("");
    try {
      const record = await action();
      setCheckin(record);
      setMessage(successText);
      await refreshCheckin();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Check-in action failed.");
    } finally {
      setBusy(null);
    }
  }

  const checkinTimeline = useMemo(() => {
    if (!checkin) return [];
    return [
      { label: "Gate", value: checkin.gate_in_at ?? "Pending", status: checkin.arrival_status },
      { label: "Queue", value: checkin.queue_status, status: checkin.arrival_status },
      { label: "Dock", value: checkin.dock_in_at ?? "Pending", status: checkin.arrival_status },
      { label: "Complete", value: checkin.completed_at ?? "Pending", status: checkin.arrival_status },
    ];
  }, [checkin]);

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-wrap items-center justify-between gap-4 rounded-3xl p-6 text-white shadow-pop"
        style={{ background: `linear-gradient(120deg, ${service.color}, #12121a)` }}
      >
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-white/70">{service.name}</p>
          <h1 className="mt-1 text-2xl font-extrabold sm:text-3xl">Welcome back, {name} 👋</h1>
          <p className="mt-1 text-sm text-white/80">{service.tagline}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void refreshCheckin()}
            className="flex items-center gap-2 rounded-full bg-white/15 px-4 py-2.5 text-sm font-bold backdrop-blur-sm transition hover:bg-white/25"
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
          <button
            onClick={() => logout(service.id)}
            className="flex items-center gap-2 rounded-full bg-white/15 px-4 py-2.5 text-sm font-bold backdrop-blur-sm transition hover:bg-white/25"
          >
            Sign out
          </button>
        </div>
      </motion.div>

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {[service.stat, { value: "4", label: "open exceptions" }, { value: "98.2%", label: "on-time compliance" }].map(
          (s, i) => (
            <motion.div
              key={s.label}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.06 * i }}
              className="rounded-2xl border border-line bg-white p-5"
            >
              <p className="text-2xl font-extrabold text-ink">{s.value}</p>
              <p className="mt-1 text-xs font-semibold text-mist">{s.label}</p>
            </motion.div>
          )
        )}
      </div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.15 }}
        className="mt-6 rounded-3xl border border-line bg-white p-6"
      >
        {error && <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
        {message && <div className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
        {service.id === "tms" && <TmsPanel color={service.color} shipments={shipments} />}
        {service.id === "wms" && <WmsPanel color={service.color} suggestions={dockSuggestions} />}
        {service.id === "checkin" && (
          <CheckinPanel
            color={service.color}
            record={checkin}
            timeline={checkinTimeline}
            busy={busy}
            onGateIn={() =>
              mutateCheckin(
                () =>
                  gateCheckIn({
                    shipment_id: activeShipmentId,
                    facility_id: "FAC-JAI-01",
                    gate_in_at: new Date().toISOString(),
                  }),
                "Gate check-in saved"
              )
            }
            onQueue={() =>
              mutateCheckin(
                () =>
                  updateQueue({
                    shipment_id: activeShipmentId,
                    queue_status: "YARD_QUEUE",
                  }),
                "Queue updated"
              )
            }
            onDock={() =>
              mutateCheckin(
                () =>
                  markDocked({
                    shipment_id: activeShipmentId,
                    dock_in_at: new Date().toISOString(),
                  }),
                "Docked status saved"
              )
            }
            onComplete={() =>
              mutateCheckin(
                () =>
                  completeUnload({
                    shipment_id: activeShipmentId,
                    completed_at: new Date().toISOString(),
                  }),
                "Unload completed"
              )
            }
          />
        )}
        {service.id === "drivers" && <DriversPanel color={service.color} healthText={driverStatus} />}
      </motion.div>
    </div>
  );
}

function Badge({ status }: { status: string }) {
  const tone =
    status === "COMPLETED" || status === "CONFIRMED" ? "bg-emerald-100 text-emerald-700" :
    status === "DOCKED" ? "bg-blue-100 text-blue-700" :
    status === "WAITING" || status === "GATE_IN" ? "bg-amber-100 text-amber-700" :
    "bg-slate-100 text-slate-600";
  return <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${tone}`}>{status}</span>;
}

function TmsPanel({ color, shipments }: { color: string; shipments: ShipmentSummary[] }) {
  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink">Active shipments</h2>
      <p className="text-sm text-ink-soft">Live loads currently assigned across the fleet.</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[480px] border-collapse text-sm">
          <thead>
            <tr className="text-left text-xs font-bold uppercase tracking-wide text-mist">
              <th className="pb-2">Shipment</th>
              <th className="pb-2">Route</th>
              <th className="pb-2">ETA</th>
              <th className="pb-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {shipments.map((s) => (
              <tr key={s.shipment_id} className="border-t border-line">
                <td className="py-3 font-bold text-ink">{s.shipment_id}</td>
                <td className="py-3 text-ink-soft">{s.destination_id ?? "Unknown destination"}</td>
                <td className="py-3 text-ink-soft">{s.planned_eta ? new Date(s.planned_eta).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "TBD"}</td>
                <td className="py-3">
                  <Badge status={s.status ?? "planned"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button
        className="mt-5 rounded-xl px-4 py-2.5 text-sm font-bold text-white"
        style={{ background: color }}
      >
        Plan new shipment
      </button>
    </div>
  );
}

function WmsPanel({ color, suggestions }: { color: string; suggestions: DockSuggestion[] }) {
  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink">Dock & appointment slots</h2>
      <p className="text-sm text-ink-soft">Receiving capacity across facilities today.</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {suggestions.map((d) => (
          <div key={d.slot_id} className="flex items-center justify-between rounded-2xl border border-line p-4">
            <div>
              <p className="text-sm font-bold text-ink">
                {d.dock_code} · {d.slot_id}
              </p>
              <p className="mt-1 flex items-center gap-1.5 text-xs text-ink-soft">
                <Clock className="h-3.5 w-3.5" />
                {new Date(d.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} – {new Date(d.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </p>
            </div>
            <Badge status={d.lifecycle_stage} />
          </div>
        ))}
      </div>
      <button className="mt-5 rounded-xl px-4 py-2.5 text-sm font-bold text-white" style={{ background: color }}>
        Reserve a slot
      </button>
    </div>
  );
}

function CheckinPanel({
  color,
  record,
  timeline,
  busy,
  onGateIn,
  onQueue,
  onDock,
  onComplete,
}: {
  color: string;
  record: CheckInRecord | null;
  timeline: Array<{ label: string; value: string; status: string }>;
  busy: string | null;
  onGateIn: () => void;
  onQueue: () => void;
  onDock: () => void;
  onComplete: () => void;
}) {
  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink">Gate & yard activity</h2>
      <p className="text-sm text-ink-soft">Real-time arrivals and yard movement.</p>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-line p-4">
          <p className="text-sm font-bold text-ink">Shipment {record?.shipment_id ?? "SHP1006"}</p>
          <p className="mt-1 text-xs text-ink-soft">Backend validated status only. React never reimplements the state machine.</p>
          <div className="mt-4 grid gap-2">
            {timeline.map((item) => (
              <div key={item.label} className="flex items-center justify-between rounded-xl bg-cloud/70 px-3 py-2 text-sm">
                <span className="font-medium text-ink">{item.label}</span>
                <span className="text-ink-soft">{item.value}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-2xl border border-line p-4">
          <div className="flex flex-col gap-2">
            <button onClick={onGateIn} disabled={Boolean(busy)} className="rounded-xl px-4 py-2.5 text-sm font-bold text-white" style={{ background: color }}>
              {busy ? <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> : null}
              Gate check-in
            </button>
            <button onClick={onQueue} disabled={Boolean(busy)} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink">
              Queue update
            </button>
            <button onClick={onDock} disabled={Boolean(busy)} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink">
              Mark docked
            </button>
            <button onClick={onComplete} disabled={Boolean(busy)} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink">
              Complete unload
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function DriversPanel({ color, healthText }: { color: string; healthText: string }) {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <h2 className="text-lg font-extrabold text-ink">Your current trip</h2>
        <div className="mt-3 rounded-2xl border border-line p-4">
          <p className="text-sm font-bold text-ink">SHP-1042 · Neemrana → Jaipur DC</p>
          <p className="mt-1 flex items-center gap-1.5 text-xs text-ink-soft">
            <Clock className="h-3.5 w-3.5" /> Planned ETA 17:20 · Revised 19:10
          </p>
          <div className="mt-3 flex items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-700">
            <AlertTriangle className="h-4 w-4" />
            Tyre repair reported near Neemrana — dock slot after 19:00 requested.
          </div>
        </div>

        <div className="mt-4 flex items-center gap-2">
          <input placeholder="Declare a new ETA or report an issue…" className="flex-1 rounded-xl border border-line bg-cloud/60 px-3.5 py-2.5 text-sm outline-none focus:border-ink/30" />
          <button className="grid h-10 w-10 place-items-center rounded-xl text-white" style={{ background: color }}>
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-extrabold text-ink">Exception thread</h2>
        <div className="mt-3 flex flex-col gap-2.5">
          <ChatBubble from="driver" text="Tyre damaged near Neemrana. Repair may take 45 minutes." />
          <ChatBubble from="ops" text="Understood — checking dock availability at Jaipur DC now." color={color} />
          <ChatBubble from="driver" text="Can I get a slot after 7 PM? I must leave before 9 PM." />
          <ChatBubble from="ops" text="Slot 19:30–20:00 held for you — confirming with the warehouse." color={color} />
          <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-600">
            <CheckCircle2 className="h-3.5 w-3.5" /> Appointment updated to 19:30
          </div>
          <div className="rounded-2xl border border-line bg-cloud/70 px-3 py-2 text-xs text-ink-soft">{healthText}</div>
        </div>
      </div>
    </div>
  );
}

function ChatBubble({ from, text, color }: { from: "driver" | "ops"; text: string; color?: string }) {
  const isOps = from === "ops";
  return (
    <div className={`flex ${isOps ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-xs font-medium ${
          isOps ? "text-white" : "bg-cloud text-ink"
        }`}
        style={isOps ? { background: color } : undefined}
      >
        {text}
      </div>
    </div>
  );
}
