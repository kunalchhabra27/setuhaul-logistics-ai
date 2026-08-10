import { useEffect, useMemo, useState } from "react";
import type { ComponentType, Dispatch, ReactNode, SetStateAction } from "react";
import { Navigate, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Clock, Loader2, RefreshCw, ShieldCheck, TimerReset, Truck, Warehouse } from "lucide-react";
import { getService } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { ApiClientError } from "../services/api";
import { completeUnload, fetchCheckInStatus, gateCheckIn, markDocked, updateQueue } from "../services/checkinApi";
import { createShipment, getShipment, listShipments } from "../services/tmsApi";
import { cancelHold, confirmBooking, holdSlot, requestConfirmation, suggestSlots } from "../services/dockSchedulerApi";
import { driverChatHealth } from "../services/driverChatApi";
import type { CheckInRecord, DockSuggestion, HoldResponse, ShipmentRecord, ShipmentSummary } from "../types/api";

export default function PortalWorkspace() {
  const { serviceId } = useParams();
  const service = getService(serviceId);
  const { isAuthed, sessions, logout } = useAuth();
  const [allShipments, setAllShipments] = useState<ShipmentSummary[]>([]);
  const [checkin, setCheckin] = useState<CheckInRecord | null>(null);
  const [checkinShipmentId, setCheckinShipmentId] = useState("");
  const [checkinFacilityId, setCheckinFacilityId] = useState("");
  const [shipments, setShipments] = useState<ShipmentSummary[]>([]);
  const [selectedShipment, setSelectedShipment] = useState<ShipmentRecord | null>(null);
  const [showCreateShipment, setShowCreateShipment] = useState(false);
  const [createForm, setCreateForm] = useState({
    driver_id: "",
    vehicle_id: "",
    destination_id: "",
    product_class: "",
    origin_id: "",
    priority: "",
    planned_eta: "",
    expected_unload_minutes: "",
    status: "",
  });
  const [dockSuggestions, setDockSuggestions] = useState<DockSuggestion[]>([]);
  const [holdsBySlot, setHoldsBySlot] = useState<Record<string, HoldResponse>>({});
  const [driverStatus, setDriverStatus] = useState<string>("Loading...");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");

  if (!service) return <Navigate to="/" replace />;
  if (!isAuthed(service.id)) return <Navigate to={`/auth/${service.id}`} replace />;

  const name = sessions[service.id]?.name ?? "there";

  useEffect(() => {
    if (service.id === "drivers") return;
    void (async () => {
      try {
        const items = await listShipments();
        setAllShipments(items);
        if (service.id === "tms") {
          setShipments(items);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load shipments.");
      }
    })();
  }, [service.id, checkinShipmentId]);

  useEffect(() => {
    if (service.id !== "checkin") return;
    void refreshCheckin();
  }, [service.id, checkinShipmentId]);

  useEffect(() => {
    if (service.id !== "wms") return;
    void (async () => {
      try {
        if (!checkinShipmentId) {
          setDockSuggestions([]);
          return;
        }
        const items = await suggestSlots({ shipment_id: checkinShipmentId, limit: 4 });
        setDockSuggestions(items);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load slot suggestions.");
      }
    })();
  }, [service.id, checkinShipmentId, allShipments]);

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
    if (!checkinShipmentId) {
      setCheckin(null);
      return;
    }
    try {
      const current = await fetchCheckInStatus(checkinShipmentId);
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

  async function openShipment(shipmentId: string) {
    setError("");
    try {
      const record = await getShipment(shipmentId);
      setSelectedShipment(record);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load shipment details.");
    }
  }

  async function createNewShipment() {
    setBusy("creating-shipment");
    setError("");
    try {
      const record = await createShipment({
        driver_id: createForm.driver_id,
        vehicle_id: createForm.vehicle_id,
        destination_id: createForm.destination_id,
        product_class: createForm.product_class,
        priority: statusPriorityToNumeric(createForm.priority),
        expected_unload_minutes: Number(createForm.expected_unload_minutes || 0),
        origin_id: createForm.origin_id || undefined,
        planned_eta: createForm.planned_eta || undefined,
        status: createForm.status ? createForm.status.toLowerCase() : undefined,
      });
      setMessage(`Shipment ${record.shipment_id} created`);
      setSelectedShipment(record);
      setShipments(await listShipments());
      setShowCreateShipment(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Shipment creation failed.");
    } finally {
      setBusy(null);
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

  async function submitCheckinLookup() {
    if (!checkinShipmentId) {
      setError("Select a shipment first.");
      return;
    }
    setBusy("lookup-checkin");
    await refreshCheckin();
    setBusy(null);
  }

  async function mutateSlot(action: () => Promise<unknown>, successText: string, slotId?: string) {
    setBusy(slotId ? `${successText}:${slotId}` : successText);
    setError("");
    try {
      await action();
      setMessage(successText);
      if (checkinShipmentId) {
        const items = await suggestSlots({ shipment_id: checkinShipmentId, limit: 4 });
        setDockSuggestions(items);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Slot action failed.");
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
        {service.id === "tms" && (
          <TmsPanel
            color={service.color}
            shipments={shipments}
            selectedShipment={selectedShipment}
            showCreateShipment={showCreateShipment}
            createForm={createForm}
            busy={busy}
            onSelectShipment={openShipment}
            onToggleCreate={() => setShowCreateShipment((value) => !value)}
            onCreateFormChange={setCreateForm}
            onCreateShipment={createNewShipment}
          />
        )}
        {service.id === "wms" && (
          <WmsPanel
            color={service.color}
            shipmentId={checkinShipmentId}
            allShipments={allShipments.filter((s) => s.status && !["completed", "cancelled"].includes(s.status))}
            suggestions={dockSuggestions}
            holdsBySlot={holdsBySlot}
            busy={busy}
            onShipmentIdChange={setCheckinShipmentId}
            onRefresh={() => void refreshCheckin()}
            onHold={(slotId) =>
              mutateSlot(
                async () => {
                  const hold = await holdSlot({ shipment_id: checkinShipmentId, slot_id: slotId, ttl_minutes: 15 });
                  setHoldsBySlot((current) => ({ ...current, [slotId]: hold }));
                  return hold;
                },
                "Slot held",
                slotId
              )
            }
            onRequestConfirmation={(slotId) =>
              mutateSlot(
                async () => {
                  const hold = await requestConfirmation({ shipment_id: checkinShipmentId, slot_id: slotId });
                  setHoldsBySlot((current) => ({ ...current, [slotId]: hold }));
                  return hold;
                },
                "Confirmation requested",
                slotId
              )
            }
            onConfirm={(slotId) =>
              mutateSlot(
                () => confirmBooking({ shipment_id: checkinShipmentId, slot_id: slotId, accepted: true }),
                "Booking confirmed",
                slotId
              )
            }
            onCancelHold={(slotId) => {
              const hold = holdsBySlot[slotId];
              if (!hold) {
                setError("Select or create a hold first.");
                return;
              }
              void mutateSlot(
                async () => cancelHold({ hold_id: hold.hold_id }),
                "Hold released",
                slotId
              );
            }}
          />
        )}
        {service.id === "checkin" && (
          <CheckinPanel
            color={service.color}
            record={checkin}
            timeline={checkinTimeline}
            busy={busy}
            shipmentId={checkinShipmentId}
            facilityId={checkinFacilityId}
            onShipmentIdChange={setCheckinShipmentId}
            onFacilityIdChange={setCheckinFacilityId}
            activeShipments={allShipments.filter((s) => s.status && !["completed", "cancelled"].includes(s.status))}
            onActiveShipmentsLoaded={setAllShipments}
            onLookup={() => void submitCheckinLookup()}
            onGateIn={() =>
              mutateCheckin(
                () =>
                  gateCheckIn({
                    shipment_id: checkinShipmentId,
                    facility_id: checkinFacilityId,
                    gate_in_at: new Date().toISOString(),
                  }),
                "Gate check-in saved"
              )
            }
            onQueue={() =>
              mutateCheckin(
                () =>
                  updateQueue({
                    shipment_id: checkinShipmentId,
                    queue_status: "YARD_QUEUE",
                  }),
                "Queue updated"
              )
            }
            onDock={() =>
              mutateCheckin(
                () =>
                  markDocked({
                    shipment_id: checkinShipmentId,
                    dock_in_at: new Date().toISOString(),
                  }),
                "Docked status saved"
              )
            }
            onComplete={() =>
              mutateCheckin(
                () =>
                  completeUnload({
                    shipment_id: checkinShipmentId,
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

function TmsPanel({
  color,
  shipments,
  selectedShipment,
  showCreateShipment,
  createForm,
  busy,
  onSelectShipment,
  onToggleCreate,
  onCreateFormChange,
  onCreateShipment,
}: {
  color: string;
  shipments: ShipmentSummary[];
  selectedShipment: ShipmentRecord | null;
  showCreateShipment: boolean;
  createForm: {
    driver_id: string;
    vehicle_id: string;
    destination_id: string;
    product_class: string;
    origin_id: string;
    priority: string;
    planned_eta: string;
    expected_unload_minutes: string;
    status: string;
  };
  busy: string | null;
  onSelectShipment: (shipmentId: string) => Promise<void>;
  onToggleCreate: () => void;
  onCreateFormChange: Dispatch<SetStateAction<{
    driver_id: string;
    vehicle_id: string;
    destination_id: string;
    product_class: string;
    origin_id: string;
    priority: string;
    planned_eta: string;
    expected_unload_minutes: string;
    status: string;
  }>>;
  onCreateShipment: () => Promise<void>;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-extrabold text-ink">Active shipments</h2>
          <p className="text-sm text-ink-soft">Live loads currently assigned across the fleet.</p>
        </div>
        <button className="rounded-xl px-4 py-2.5 text-sm font-bold text-white" style={{ background: color }} onClick={onToggleCreate}>
          {showCreateShipment ? "Close create form" : "Plan new shipment"}
        </button>
      </div>
      <div className="mt-4 rounded-2xl border border-line bg-cloud/40 p-4">
        <p className="text-sm font-bold text-ink">Create shipment</p>
        <p className="mt-1 text-xs text-ink-soft">Enter only the backend payload fields. No defaults are prefilled, and the service layer maps these values to the database schema.</p>
      </div>
      {showCreateShipment && (
        <div className="mt-4 rounded-2xl border border-line bg-cloud/40 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="driver_id">
              <input value={createForm.driver_id} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, driver_id: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="vehicle_id">
              <input value={createForm.vehicle_id} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, vehicle_id: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="destination_id">
              <input value={createForm.destination_id} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, destination_id: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="product_class">
              <input value={createForm.product_class} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, product_class: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="origin_id">
              <input value={createForm.origin_id} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, origin_id: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="priority">
              <input value={createForm.priority} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, priority: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="planned_eta">
              <input value={createForm.planned_eta} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, planned_eta: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="expected_unload_minutes">
              <input type="number" min={1} value={createForm.expected_unload_minutes} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, expected_unload_minutes: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
            <Field label="status">
              <input value={createForm.status} onChange={(e) => onCreateFormChange((prev) => ({ ...prev, status: e.target.value }))} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
            </Field>
          </div>
          <button disabled={busy === "creating-shipment"} onClick={() => void onCreateShipment()} className="mt-4 rounded-xl px-4 py-2.5 text-sm font-bold text-white" style={{ background: color }}>
            {busy === "creating-shipment" ? "Creating..." : "Create shipment"}
          </button>
        </div>
      )}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[480px] border-collapse text-sm">
          <thead>
            <tr className="text-left text-xs font-bold uppercase tracking-wide text-mist">
              <th className="pb-2">Shipment</th>
              <th className="pb-2">destination_facility_id</th>
              <th className="pb-2">original_eta_ts</th>
              <th className="pb-2">current_status</th>
            </tr>
          </thead>
          <tbody>
            {shipments.map((s) => (
              <tr key={s.shipment_id} className="cursor-pointer border-t border-line hover:bg-cloud/40" onClick={() => void onSelectShipment(s.shipment_id)}>
                <td className="py-3 font-bold text-ink">{s.shipment_id}</td>
                <td className="py-3 text-ink-soft">{s.destination_id ?? "Unknown destination"}</td>
                <td className="py-3 text-ink-soft">{s.planned_eta ? new Date(s.planned_eta).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "TBD"}</td>
                <td className="py-3">
                  <Badge status={(s.status ?? "planned").toUpperCase()} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedShipment && (
        <div className="mt-5 rounded-2xl border border-line bg-cloud/40 p-4">
          <p className="text-sm font-bold text-ink">Selected shipment: {selectedShipment.shipment_id}</p>
          <p className="mt-1 text-xs text-ink-soft">
            driver_id {selectedShipment.driver_id} · vehicle_id {selectedShipment.vehicle_id} · destination_facility_id {selectedShipment.destination_id}
          </p>
        </div>
      )}
    </div>
  );
}

function WmsPanel({
  color,
  shipmentId,
  allShipments,
  suggestions,
  holdsBySlot,
  busy,
  onShipmentIdChange,
  onRefresh,
  onHold,
  onRequestConfirmation,
  onConfirm,
  onCancelHold,
}: {
  color: string;
  shipmentId: string;
  allShipments: ShipmentSummary[];
  suggestions: DockSuggestion[];
  holdsBySlot: Record<string, HoldResponse>;
  busy: string | null;
  onShipmentIdChange: (value: string) => void;
  onRefresh: () => void;
  onHold: (slotId: string) => void;
  onRequestConfirmation: (slotId: string) => void;
  onConfirm: (slotId: string) => void;
  onCancelHold: (slotId: string) => void;
}) {
  const activeSuggestion = suggestions[0];
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-extrabold text-ink">Dock scheduler</h2>
          <p className="text-sm text-ink-soft">Deterministic slot ranking, holds, confirmation, and conflict prevention.</p>
        </div>
        <button className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink" onClick={onRefresh}>
          Refresh suggestions
        </button>
      </div>
      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <div className="rounded-2xl border border-line bg-cloud/40 p-4">
          <p className="text-xs font-bold uppercase tracking-wide text-mist">Shipment under review</p>
          <input value={shipmentId} onChange={(e) => onShipmentIdChange(e.target.value)} className="mt-2 w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
        </div>
        <div className="rounded-2xl border border-line bg-cloud/40 p-4">
          <p className="text-xs font-bold uppercase tracking-wide text-mist">Workflow</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink-soft">Suggest</span>
            <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink-soft">Hold</span>
            <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink-soft">Confirm</span>
            <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink-soft">Protect</span>
          </div>
        </div>
        <div className="rounded-2xl border border-line bg-cloud/40 p-4">
          <p className="text-xs font-bold uppercase tracking-wide text-mist">Protected work</p>
          <p className="mt-2 text-sm text-ink-soft">Appointments stay transactional and cannot double-book held or in-progress capacity.</p>
        </div>
      </div>
      <div className="mt-4 rounded-2xl border border-line bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-bold text-ink">Deterministic scheduling summary</p>
            <p className="text-xs text-ink-soft">The next suggestion is always derived from backend constraints and current dock state.</p>
          </div>
          <span className="rounded-full bg-ink px-3 py-1 text-xs font-bold text-white">
            {suggestions.length} suggestion{suggestions.length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Metric icon={Truck} label="Shipment" value={shipmentId || "Select one"} />
          <Metric icon={Warehouse} label="Slots" value={String(suggestions.length)} />
          <Metric icon={TimerReset} label="Holds" value={String(Object.keys(holdsBySlot).length)} />
          <Metric icon={ShieldCheck} label="State" value={activeSuggestion?.lifecycle_stage ?? "PROPOSED"} />
        </div>
      </div>
      <div className="mt-4 rounded-2xl border border-line bg-cloud/40 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-bold text-ink">Active shipments</p>
            <p className="text-xs text-ink-soft">Pick a live shipment to rank slots and keep traffic moving.</p>
          </div>
          <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink-soft">{allShipments.length} active</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {allShipments.map((item) => (
            <button
              key={item.shipment_id}
              onClick={() => onShipmentIdChange(item.shipment_id)}
              className={`rounded-full px-3 py-1.5 text-xs font-bold transition ${
                item.shipment_id === shipmentId ? "bg-ink text-white" : "bg-white text-ink-soft hover:bg-white/80"
              }`}
            >
              {item.shipment_id} · {item.status ?? "planned"}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-4 flex items-center gap-3 rounded-2xl border border-line bg-cloud/40 p-4">
        <div className="flex-1">
          <p className="text-xs font-bold uppercase tracking-wide text-mist">Action ready state</p>
          <p className="mt-1 text-sm text-ink-soft">Choose a shipment above, then use the ranked slots below to hold or confirm capacity.</p>
        </div>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {suggestions.map((d) => (
          <div key={d.slot_id} className="rounded-2xl border border-line p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-bold text-ink">
                  #{d.rank} · {d.dock_code} · {d.slot_id}
                </p>
                <p className="mt-1 flex items-center gap-1.5 text-xs text-ink-soft">
                  <Clock className="h-3.5 w-3.5" />
                  {new Date(d.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} – {new Date(d.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </p>
              </div>
              <Badge status={d.lifecycle_stage} />
            </div>
            <p className="mt-3 text-sm text-ink-soft">{d.reason}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button disabled={Boolean(busy)} onClick={() => onHold(d.slot_id)} className="rounded-full border border-line px-3 py-1.5 text-xs font-bold text-ink">
                Hold
              </button>
              <button disabled={Boolean(busy)} onClick={() => onRequestConfirmation(d.slot_id)} className="rounded-full border border-line px-3 py-1.5 text-xs font-bold text-ink">
                Request confirm
              </button>
              <button disabled={Boolean(busy)} onClick={() => onConfirm(d.slot_id)} className="rounded-full px-3 py-1.5 text-xs font-bold text-white" style={{ background: color }}>
                Confirm
              </button>
              <button disabled={Boolean(busy) || !holdsBySlot[d.slot_id]} onClick={() => onCancelHold(d.slot_id)} className="rounded-full border border-line px-3 py-1.5 text-xs font-bold text-ink">
                Cancel hold
              </button>
            </div>
            {holdsBySlot[d.slot_id] && <p className="mt-3 text-xs text-ink-soft">Hold {holdsBySlot[d.slot_id].hold_id} active until {new Date(holdsBySlot[d.slot_id].expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</p>}
          </div>
        ))}
      </div>
      {!suggestions.length && <EmptyState title="No suggestions yet" text="Choose a live shipment to generate ranked slot options." />}
    </div>
  );
}

function CheckinPanel({
  color,
  record,
  timeline,
  busy,
  shipmentId,
  facilityId,
  onShipmentIdChange,
  onFacilityIdChange,
  activeShipments,
  onActiveShipmentsLoaded,
  onLookup,
  onGateIn,
  onQueue,
  onDock,
  onComplete,
}: {
  color: string;
  record: CheckInRecord | null;
  timeline: Array<{ label: string; value: string; status: string }>;
  busy: string | null;
  shipmentId: string;
  facilityId: string;
  onShipmentIdChange: (value: string) => void;
  onFacilityIdChange: (value: string) => void;
  activeShipments: ShipmentSummary[];
  onActiveShipmentsLoaded: (items: ShipmentSummary[]) => void;
  onLookup: () => void;
  onGateIn: () => void;
  onQueue: () => void;
  onDock: () => void;
  onComplete: () => void;
}) {
  useEffect(() => {
    onActiveShipmentsLoaded(activeShipments);
  }, [activeShipments, onActiveShipmentsLoaded]);

  const selected = activeShipments.find((item) => item.shipment_id === shipmentId) ?? null;
  const recordShipmentId = record?.shipment_id ?? shipmentId;
  const recordArrivalStatus = record?.arrival_status ?? "NOT_CHECKED_IN";
  const currentArrival = record?.arrival_status ?? null;
  const canGate = currentArrival === null;
  const canQueue = currentArrival === "GATE_IN";
  const canDock = currentArrival === "WAITING" || currentArrival === "GATE_IN";
  const canComplete = currentArrival === "DOCKED";
  const activeStatus = selected?.status ?? "planned";

  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink">Gate & yard activity</h2>
      <p className="text-sm text-ink-soft">Real-time arrivals and yard movement.</p>
      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <Field label="Shipment ID">
          <select value={shipmentId} onChange={(e) => onShipmentIdChange(e.target.value)} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm">
            <option value="">Select an active shipment</option>
            {activeShipments.map((item) => (
              <option key={item.shipment_id} value={item.shipment_id}>
                {item.shipment_id} · {item.destination_id ?? "Unknown"} · {(item.status ?? "planned").toUpperCase()}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Facility ID">
          <input value={facilityId} onChange={(e) => onFacilityIdChange(e.target.value)} className="w-full rounded-xl border border-line bg-white px-3 py-2 text-sm" />
        </Field>
        <div className="flex items-end">
          <button onClick={onLookup} disabled={Boolean(busy) || !shipmentId} className="w-full rounded-xl px-4 py-2.5 text-sm font-bold text-white" style={{ background: color }}>
            {busy === "lookup-checkin" ? "Loading..." : "Load shipment"}
          </button>
        </div>
      </div>
      {selected && (
        <div className="mt-4 rounded-2xl border border-line bg-cloud/40 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-bold text-ink">Selected shipment summary</p>
              <p className="text-xs text-ink-soft">
                {selected.shipment_id} · {selected.destination_id ?? "Unknown destination"} · {selected.status ?? "planned"}
              </p>
            </div>
            <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink-soft">
              driver_id {selected.driver_id ?? "—"} · vehicle_id {selected.vehicle_id ?? "—"}
            </span>
          </div>
        </div>
      )}
      {!shipmentId && <EmptyState title="Select an active shipment" text="Choose from shipments that are still in progress so gate and dock transitions remain valid." />}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-line p-4">
          <p className="text-sm font-bold text-ink">Shipment {recordShipmentId || "Not selected"}</p>
          <p className="mt-1 text-xs text-ink-soft">Backend validated status only. React never reimplements the state machine.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge status={recordArrivalStatus} />
              <span className="rounded-full bg-cloud px-3 py-1 text-xs font-semibold text-ink-soft">{activeStatus.toUpperCase()}</span>
            </div>
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
            <button onClick={onGateIn} disabled={Boolean(busy) || !shipmentId || !canGate} className="rounded-xl px-4 py-2.5 text-sm font-bold text-white" style={{ background: color }}>
              {busy ? <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> : null}
              Gate check-in
            </button>
            <button onClick={onQueue} disabled={Boolean(busy) || !shipmentId || !canQueue} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink">
              Queue update
            </button>
            <button onClick={onDock} disabled={Boolean(busy) || !shipmentId || !canDock} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink">
              Mark docked
            </button>
            <button onClick={onComplete} disabled={Boolean(busy) || !shipmentId || !canComplete} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink">
              Complete unload
            </button>
          </div>
          <p className="mt-3 text-xs text-ink-soft">
            Gate is available first. Queue requires gate-in. Dock requires gate-in or waiting. Complete requires docked status.
          </p>
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

        <div className="mt-4 rounded-2xl border border-dashed border-line bg-cloud/40 px-4 py-3 text-sm text-ink-soft">
          Driver messaging and ETA mutations are not exposed by the current backend route set. This panel is read-only until the corresponding API is available.
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

function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-bold uppercase tracking-wide text-mist">{label}</span>
      {children}
      {help && <span className="mt-1.5 block text-[11px] leading-relaxed text-ink-soft">{help}</span>}
    </label>
  );
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return (
    <div className="mt-4 rounded-2xl border border-dashed border-line bg-cloud/30 px-4 py-5 text-sm">
      <p className="font-bold text-ink">{title}</p>
      <p className="mt-1 text-ink-soft">{text}</p>
    </div>
  );
}

function statusPriorityToNumeric(priorityCode: string) {
  switch (priorityCode.toUpperCase()) {
    case "LOW":
      return 1;
    case "NORMAL":
      return 2;
    case "HIGH":
      return 3;
    case "CRITICAL":
      return 4;
    default:
      return 2;
  }
}

function Metric({ icon: Icon, label, value }: { icon: ComponentType<{ className?: string }>; label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-line bg-cloud/40 p-4">
      <div className="flex items-center gap-2">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-white text-ink-soft">
          <Icon className="h-4 w-4" />
        </span>
        <div>
          <p className="text-xs font-bold uppercase tracking-wide text-mist">{label}</p>
          <p className="text-sm font-extrabold text-ink">{value}</p>
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
