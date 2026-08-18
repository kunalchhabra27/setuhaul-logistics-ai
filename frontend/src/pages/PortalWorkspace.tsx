import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Navigate, useParams } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowRightLeft,
  Ban,
  Bell,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  Clock,
  Download,
  Loader2,
  MapPin,
  PackageCheck,
  Phone,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { getService } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { ApiClientError } from "../services/api";
import {
  approveGateCheckin,
  completeUnload,
  fetchCheckInStatus,
  gateCheckIn,
  getMyCheckinFacility,
  listFacilitiesForRegistration as listFacilitiesForCheckinRegistration,
  listShipmentsForMyFacilityCheckin,
  markDocked,
  registerMyCheckinFacility,
  updateQueue,
} from "../services/checkinApi";
import {
  archiveShipment,
  assignShipmentDriver,
  cancelShipment,
  createShipment,
  downloadShipmentsExport,
  getDockBoardForShipment as getTmsDockBoardForShipment,
  getShipmentContext,
  getShipmentReferenceData,
  listDrivers,
  listFacilities,
  listShipments,
  listVehicles,
  requestDockSlotChange,
} from "../services/tmsApi";
import {
  confirmBooking,
  decideChangeRequest,
  getDockBoard,
  getDockBoardUnavailableReason,
  getMyWmsFacility,
  holdSlot,
  listFacilitiesForRegistration as listFacilitiesForWmsRegistration,
  listPendingChangeRequests,
  listShipmentsForMyFacility,
  registerMyWmsFacility,
} from "../services/dockSchedulerApi";
import type {
  ChangeRequest,
  CheckInRecord,
  DockSlot,
  FacilityStaffAssignment,
  ShipmentContext,
  ShipmentCreateInput,
  ShipmentReferenceData,
  ShipmentSummary,
  TmsDriver,
  TmsFacility,
  TmsVehicle,
} from "../types/api";
import DriversPortal from "../components/drivers/DriversPortal";
import FacilitySetupForm from "../components/facility/FacilitySetupForm";

export default function PortalWorkspace() {
  const { serviceId } = useParams();
  const service = getService(serviceId);
  const { isAuthed, sessions, logout } = useAuth();
  const [checkin, setCheckin] = useState<CheckInRecord | null>(null);
  const [shipments, setShipments] = useState<ShipmentSummary[]>([]);
  const [tmsDrivers, setTmsDrivers] = useState<TmsDriver[]>([]);
  const [tmsVehicles, setTmsVehicles] = useState<TmsVehicle[]>([]);
  const [tmsFacilities, setTmsFacilities] = useState<TmsFacility[]>([]);
  const [shipmentReferenceData, setShipmentReferenceData] = useState<ShipmentReferenceData>({
    origins: [],
    product_categories: [],
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [dockBoard, setDockBoard] = useState<DockSlot[]>([]);
  const [selectedSlotId, setSelectedSlotId] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [wmsToast, setWmsToast] = useState<{ text: string; tone: "success" | "error" } | null>(null);
  const [wmsShipments, setWmsShipments] = useState<ShipmentSummary[]>([]);
  const [checkinShipments, setCheckinShipments] = useState<ShipmentSummary[]>([]);
  const [activeShipmentId, setActiveShipmentId] = useState<string>("");
  const [wmsFacility, setWmsFacility] = useState<FacilityStaffAssignment | null>(null);
  const [checkinFacility, setCheckinFacility] = useState<FacilityStaffAssignment | null>(null);
  const [facilityLoading, setFacilityLoading] = useState(true);

  if (!service) return <Navigate to="/" replace />;
  if (!isAuthed(service.id)) return <Navigate to={`/auth/${service.id}`} replace />;

  const name = sessions[service.id]?.name ?? "there";

  // Action-result toasts are scoped to whichever portal produced them --
  // without this, switching from e.g. Check-in to WMS (React Router keeps
  // this same component instance mounted across /portal/:serviceId
  // navigations, it doesn't remount) would leave a stale "Gate check-in
  // approved" toast floating on a portal it has nothing to do with.
  useEffect(() => {
    setMessage("");
    setError("");
    setWmsToast(null);
  }, [service?.id]);

  // WMS/Check-in staff must register which single warehouse facility they
  // work at before they can see any shipments -- this is what makes the
  // "another facility's staff can't see these shipments" isolation actually
  // enforceable server-side (see /tms/facility-staff/shipments).
  useEffect(() => {
    if (service.id !== "wms" && service.id !== "checkin") return;
    setFacilityLoading(true);
    void (async () => {
      try {
        if (service.id === "wms") {
          setWmsFacility(await getMyWmsFacility());
        } else {
          setCheckinFacility(await getMyCheckinFacility());
        }
      } catch (err) {
        if (!(err instanceof ApiClientError && err.status === 404)) {
          setError(err instanceof Error ? err.message : "Unable to load your facility assignment.");
        }
      } finally {
        setFacilityLoading(false);
      }
    })();
  }, [service.id]);

  useEffect(() => {
    if (service.id !== "checkin" || !checkinFacility) return;
    void (async () => {
      try {
        const items = await listShipmentsForMyFacilityCheckin();
        const relevant = items.filter((s) => s.current_status !== "CANCELLED" && s.current_status !== "COMPLETED");
        setCheckinShipments(relevant);
        if (relevant.length > 0) setActiveShipmentId(relevant[0].shipment_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load shipments.");
      }
    })();
  }, [service.id, checkinFacility]);

  useEffect(() => {
    if (service.id !== "checkin" || !activeShipmentId) return;
    void refreshCheckin();
  }, [service.id, activeShipmentId]);

  useEffect(() => {
    if (service.id !== "tms") return;
    void refreshTms();
    // Same polling pattern as the WMS change-requests header below --
    // without this, a status change from another portal (a driver
    // checking in, WMS approving a dock-slot change) only shows up here
    // after a dispatcher leaves and re-enters the TMS tab, since refreshTms
    // was previously only called on tab-entry and after this panel's own
    // mutations.
    const interval = setInterval(() => void refreshTms(), 15_000);
    return () => clearInterval(interval);
  }, [service.id]);

  async function refreshTms() {
    try {
      const [items, drivers, vehicles, facilities, referenceData] = await Promise.all([
        listShipments(),
        listDrivers(),
        listVehicles(),
        listFacilities(),
        getShipmentReferenceData(),
      ]);
      setShipments(items);
      setTmsDrivers(drivers);
      setTmsVehicles(vehicles);
      setTmsFacilities(facilities);
      setShipmentReferenceData(referenceData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load shipments.");
    }
  }

  async function handleAssignDriver(shipmentId: string, driverId: string) {
    setBusy(`assign-${shipmentId}`);
    setError("");
    try {
      await assignShipmentDriver(shipmentId, driverId);
      setMessage(`Assigned ${driverId} to ${shipmentId}.`);
      await refreshTms();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to assign driver.");
    } finally {
      setBusy(null);
    }
  }

  async function handleCreateShipment(input: ShipmentCreateInput) {
    setBusy("create-shipment");
    setError("");
    try {
      const created = await createShipment(input);
      setMessage(`Shipment ${created.shipment_id} created.`);
      setCreateOpen(false);
      await refreshTms();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create shipment.");
    } finally {
      setBusy(null);
    }
  }

  async function handleArchive(shipmentId: string) {
    setBusy(`archive-${shipmentId}`);
    setError("");
    try {
      await archiveShipment(shipmentId);
      setMessage(`Shipment ${shipmentId} archived.`);
      await refreshTms();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to archive shipment.");
    } finally {
      setBusy(null);
    }
  }

  async function handleCancelShipment(shipmentId: string, reason?: string) {
    setBusy(`cancel-${shipmentId}`);
    setError("");
    try {
      await cancelShipment(shipmentId, reason);
      setMessage(`Shipment ${shipmentId} cancelled.`);
      await refreshTms();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to cancel shipment.");
    } finally {
      setBusy(null);
    }
  }

  async function handleRequestSlotChange(shipmentId: string, slotId: string, reason?: string) {
    setBusy(`slot-change-${shipmentId}`);
    setError("");
    try {
      await requestDockSlotChange(shipmentId, slotId, reason);
      setMessage(`Dock slot change requested for ${shipmentId} -- awaiting WMS approval.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to request a dock slot change.");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (service.id !== "wms" || !wmsFacility) return;
    void (async () => {
      try {
        const items = await listShipmentsForMyFacility();
        const relevant = items.filter((s) =>
          ["PLANNED", "ASSIGNED", "IN_TRANSIT", "AT_GATE", "WAITING"].includes(s.current_status ?? "")
        );
        setWmsShipments(relevant);
        if (relevant.length > 0) setActiveShipmentId(relevant[0].shipment_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load shipments.");
      }
    })();
  }, [service.id, wmsFacility]);

  useEffect(() => {
    if (service.id !== "wms" || !activeShipmentId) return;
    void refreshDockBoard();
  }, [service.id, activeShipmentId]);

  async function refreshDockBoard() {
    setError("");
    setSelectedSlotId("");
    try {
      const items = await getDockBoard(activeShipmentId);
      setDockBoard(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load the dock board.");
    }
  }

  async function handleReserveSlot() {
    if (!activeShipmentId || !selectedSlotId) return;
    setBusy("reserve-slot");
    setWmsToast(null);
    try {
      await holdSlot({ shipment_id: activeShipmentId, slot_id: selectedSlotId });
      await confirmBooking({ shipment_id: activeShipmentId, slot_id: selectedSlotId, accepted: true });
      setWmsToast({ text: `Slot ${selectedSlotId} reserved for ${activeShipmentId}.`, tone: "success" });
      await refreshDockBoard();
    } catch (err) {
      setWmsToast({
        text: err instanceof Error ? err.message : "Unable to reserve that slot.",
        tone: "error",
      });
    } finally {
      setBusy(null);
    }
  }

  async function refreshCheckin() {
    if (!activeShipmentId) return;
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


  // Real TMS stats derived from the shipments already fetched via GET /tms/shipments.
  // Note: the backend model has no actual-arrival timestamp and TMS doesn't expose an
  // exceptions table, so there is no honest way to compute an "on-time %" or an
  // "open exceptions" count here -- instead we surface "unassigned loads" (driver_id
  // is null), which is real and directly actionable via the assign-driver control below.
  const tmsStats = useMemo(() => {
    if (service.id !== "tms") return null;
    const total = shipments.length;
    const unassigned = shipments.filter((s) => !s.driver_id).length;
    const onTrack = shipments.filter((s) => s.current_status && s.current_status !== "CANCELLED").length;
    const onTrackPct = total > 0 ? Math.round((onTrack / total) * 1000) / 10 : 0;
    return [
      { value: String(total), label: "loads coordinated" },
      { value: String(unassigned), label: "unassigned loads" },
      { value: total > 0 ? `${onTrackPct}%` : "—", label: "shipments on track" },
    ];
  }, [service.id, shipments]);

  // Real WMS stats derived from the full dock board already fetched via
  // GET /dock-scheduler/board for the selected shipment. There's no
  // portal-wide "all slots" endpoint, so these are scoped to the shipment
  // currently selected above rather than a facility-wide summary.
  const wmsStats = useMemo(() => {
    if (service.id !== "wms") return null;
    const available = dockBoard.filter((s) => s.availability_status === "AVAILABLE");
    const docks = new Set(dockBoard.map((s) => s.dock_code)).size;
    const earliest = [...available].sort((a, b) => a.start.localeCompare(b.start))[0]?.start;
    return [
      { value: String(available.length), label: "slots available" },
      { value: String(docks), label: "docks offered" },
      {
        value: earliest ? new Date(earliest).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—",
        label: "earliest option",
      },
    ];
  }, [service.id, dockBoard]);

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
            onClick={() => {
              if (service.id === "checkin") void refreshCheckin();
              else if (service.id === "wms") void refreshDockBoard();
              else if (service.id === "tms") void refreshTms();
            }}
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

      {/* Drivers renders its own real, driver-scoped stats (see DriversPortal),
          computed from the authenticated driver's own snapshot. TMS and WMS
          render real stats computed from data already fetched from their own
          backends (see tmsStats/wmsStats above). Check-in has no portal-wide
          aggregate available (no "list all checkins" endpoint), so rather than
          show fabricated numbers it renders no stat row at all -- the per-shipment
          timeline below is the real, honest source of status for that portal. */}
      {(service.id === "tms" || service.id === "wms") && (
        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          {(service.id === "tms" ? tmsStats : wmsStats)?.map((s, i) => (
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
          ))}
        </div>
      )}

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.15 }}
        className="mt-6 rounded-3xl border border-line bg-white p-6"
      >
        {error && <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
        <AnimatePresence>
          {message && service.id !== "wms" && (
            <FloatingToast key="portal-toast" text={message} tone="success" onDismiss={() => setMessage("")} />
          )}
          {wmsToast && service.id === "wms" && (
            <FloatingToast
              key="wms-toast"
              text={wmsToast.text}
              tone={wmsToast.tone}
              onDismiss={() => setWmsToast(null)}
            />
          )}
        </AnimatePresence>
        {service.id === "tms" && (
          <TmsPanel
            color={service.color}
            shipments={shipments}
            drivers={tmsDrivers}
            vehicles={tmsVehicles}
            facilities={tmsFacilities}
            referenceData={shipmentReferenceData}
            busy={busy}
            onAssign={handleAssignDriver}
            onArchive={handleArchive}
            onCancel={handleCancelShipment}
            onRequestSlotChange={handleRequestSlotChange}
            createOpen={createOpen}
            onOpenCreate={() => setCreateOpen(true)}
            onCloseCreate={() => setCreateOpen(false)}
            onCreate={handleCreateShipment}
          />
        )}
        {service.id === "wms" && facilityLoading && (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-soft">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading your facility assignment...
          </div>
        )}
        {service.id === "wms" && !facilityLoading && !wmsFacility && (
          <FacilitySetupForm
            color={service.color}
            listFacilities={listFacilitiesForWmsRegistration}
            registerFacility={registerMyWmsFacility}
            onComplete={setWmsFacility}
          />
        )}
        {service.id === "wms" && !facilityLoading && wmsFacility && (
          <WmsPanel
            color={service.color}
            board={dockBoard}
            shipments={wmsShipments}
            selectedShipmentId={activeShipmentId}
            onSelectShipment={setActiveShipmentId}
            selectedSlotId={selectedSlotId}
            onSelectSlot={setSelectedSlotId}
            onReserve={handleReserveSlot}
            reserving={busy === "reserve-slot"}
            onDecided={refreshDockBoard}
            onMessage={(text, tone) => setWmsToast({ text, tone })}
          />
        )}
        {service.id === "checkin" && facilityLoading && (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-soft">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading your facility assignment...
          </div>
        )}
        {service.id === "checkin" && !facilityLoading && !checkinFacility && (
          <FacilitySetupForm
            color={service.color}
            listFacilities={listFacilitiesForCheckinRegistration}
            registerFacility={registerMyCheckinFacility}
            onComplete={setCheckinFacility}
          />
        )}
        {service.id === "checkin" && !facilityLoading && checkinFacility && (
          <CheckinPanel
            color={service.color}
            record={checkin}
            busy={busy}
            shipments={checkinShipments}
            selectedShipmentId={activeShipmentId}
            onSelectShipment={setActiveShipmentId}
            onGateIn={() =>
              mutateCheckin(
                () =>
                  gateCheckIn({
                    shipment_id: activeShipmentId,
                    facility_id:
                      checkinShipments.find((s) => s.shipment_id === activeShipmentId)?.destination_facility_id ??
                      "",
                    gate_in_at: new Date().toISOString(),
                  }),
                `Gate check-in saved for ${activeShipmentId}`
              )
            }
            onApproveGate={() =>
              mutateCheckin(
                () => approveGateCheckin(activeShipmentId),
                `Gate check-in approved for ${activeShipmentId} -- now visible on TMS`
              )
            }
            onQueue={() =>
              mutateCheckin(
                () =>
                  updateQueue({
                    shipment_id: activeShipmentId,
                    queue_status: "YARD_QUEUE",
                  }),
                `Queue updated for ${activeShipmentId}`
              )
            }
            onDock={() =>
              mutateCheckin(
                () =>
                  markDocked({
                    shipment_id: activeShipmentId,
                    dock_in_at: new Date().toISOString(),
                  }),
                `Docked status saved for ${activeShipmentId}`
              )
            }
            onComplete={() =>
              mutateCheckin(
                () =>
                  completeUnload({
                    shipment_id: activeShipmentId,
                    completed_at: new Date().toISOString(),
                  }),
                `Unload completed for ${activeShipmentId}`
              )
            }
          />
        )}
        {service.id === "drivers" && <DriversPortal color={service.color} />}
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

// Floating, self-dismissing confirmation for an action just taken --
// portal-scoped (see the reset-on-service-switch effect in PortalWorkspace)
// so a message from one portal never lingers into another, and time-boxed
// so it never just sits there forever like the old inline banner did.
function FloatingToast({
  text,
  tone,
  onDismiss,
  durationMs = 120_000,
}: {
  text: string;
  tone: "success" | "error";
  onDismiss: () => void;
  durationMs?: number;
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, durationMs);
    return () => clearTimeout(timer);
    // Intentionally keyed only on the message text -- onDismiss is a fresh
    // closure every parent render, and including it would restart the
    // 2-minute timer on every unrelated re-render (e.g. WMS's 15s poll).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, durationMs]);

  return createPortal(
    <motion.div
      initial={{ opacity: 0, y: -12, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -12, scale: 0.97 }}
      transition={{ duration: 0.18 }}
      className="fixed right-5 top-5 z-[9999] flex max-w-sm items-start gap-2.5 rounded-2xl border px-4 py-3 text-sm font-semibold shadow-lg"
      style={
        tone === "success"
          ? { background: "#ecfdf5", borderColor: "#a7f3d0", color: "#047857" }
          : { background: "#fef2f2", borderColor: "#fecaca", color: "#b91c1c" }
      }
    >
      <span className="flex-1">{text}</span>
      <button onClick={onDismiss} className="shrink-0 opacity-60 transition hover:opacity-100" aria-label="Dismiss">
        <X className="h-4 w-4" />
      </button>
    </motion.div>,
    document.body
  );
}

function TmsPanel({
  color,
  shipments,
  drivers,
  vehicles,
  facilities,
  referenceData,
  busy,
  onAssign,
  onArchive,
  onCancel,
  onRequestSlotChange,
  createOpen,
  onOpenCreate,
  onCloseCreate,
  onCreate,
}: {
  color: string;
  shipments: ShipmentSummary[];
  drivers: TmsDriver[];
  vehicles: TmsVehicle[];
  facilities: TmsFacility[];
  referenceData: ShipmentReferenceData;
  busy: string | null;
  onAssign: (shipmentId: string, driverId: string) => void;
  onArchive: (shipmentId: string) => void;
  onCancel: (shipmentId: string, reason?: string) => void;
  onRequestSlotChange: (shipmentId: string, slotId: string, reason?: string) => void;
  createOpen: boolean;
  onOpenCreate: () => void;
  onCloseCreate: () => void;
  onCreate: (input: ShipmentCreateInput) => void;
}) {
  const driversById = new Map(drivers.map((d) => [d.driver_id, d]));
  const vehiclesById = new Map(vehicles.map((v) => [v.vehicle_id, v]));
  const facilitiesById = new Map(facilities.map((f) => [f.facility_id, f]));
  const availableDrivers = drivers.filter((d) => d.driver_status === "ACTIVE");

  const [tab, setTab] = useState<"active" | "historical">("active");
  const [exporting, setExporting] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<ShipmentSummary | null>(null);
  const [slotChangeTarget, setSlotChangeTarget] = useState<ShipmentSummary | null>(null);

  const visibleShipments = shipments.filter((s) => {
    const isHistorical = Boolean(s.archived_flag) || s.current_status === "COMPLETED" || s.current_status === "CANCELLED";
    return tab === "historical" ? isHistorical : !isHistorical;
  });

  const handleExport = async () => {
    setExporting(true);
    try {
      await downloadShipmentsExport();
    } finally {
      setExporting(false);
    }
  };

  // Live trace (dock booking / check-in / ETA) fetched lazily on hover of the
  // status icon per shipment, so we're not firing N context calls up front.
  const [contextCache, setContextCache] = useState<Record<string, ShipmentContext | "loading" | "error">>({});
  const requestedRef = useRef<Set<string>>(new Set());
  const loadContext = (shipmentId: string) => {
    if (requestedRef.current.has(shipmentId)) return;
    requestedRef.current.add(shipmentId);
    setContextCache((prev) => ({ ...prev, [shipmentId]: "loading" }));
    getShipmentContext(shipmentId)
      .then((ctx) => setContextCache((prev) => ({ ...prev, [shipmentId]: ctx })))
      .catch(() => {
        requestedRef.current.delete(shipmentId);
        setContextCache((prev) => ({ ...prev, [shipmentId]: "error" }));
      });
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-extrabold text-ink">Active shipments</h2>
          <p className="text-sm text-ink-soft">Live loads currently assigned across the fleet.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-full border border-line bg-cloud p-1 text-xs font-bold">
            {(["active", "historical"] as const).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`rounded-full px-3.5 py-1.5 capitalize transition ${
                  tab === key ? "bg-white text-ink shadow-soft" : "text-ink-soft"
                }`}
              >
                {key}
              </button>
            ))}
          </div>
          <button
            onClick={() => void handleExport()}
            disabled={exporting}
            className="flex items-center gap-1.5 rounded-full border border-line px-3.5 py-1.5 text-xs font-bold text-ink-soft transition hover:border-mist disabled:opacity-50"
          >
            {exporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            Export monthly report
          </button>
        </div>
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[900px] border-collapse text-sm">
          <thead>
            <tr className="text-left text-xs font-bold uppercase tracking-wide text-mist">
              <th className="pb-2"></th>
              <th className="pb-2">Shipment</th>
              <th className="pb-2">Destination</th>
              <th className="pb-2">ETA</th>
              <th className="pb-2">Status</th>
              <th className="pb-2">Driver</th>
              <th className="pb-2">Vehicle</th>
              <th className="pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {visibleShipments.map((s) => (
              <tr key={s.shipment_id} className="border-t border-line">
                <td className="py-3 pr-1">
                  <ShipmentStatusIcon
                    shipment={s}
                    context={contextCache[s.shipment_id]}
                    onHover={() => loadContext(s.shipment_id)}
                  />
                </td>
                <td className="py-3 font-bold text-ink">{s.shipment_id}</td>
                <td className="py-3 text-ink-soft">
                  {s.destination_facility_id
                    ? facilitiesById.get(s.destination_facility_id)?.facility_name ?? s.destination_facility_id
                    : "Unknown destination"}
                </td>
                <td className="py-3 text-ink-soft">
                  {s.original_eta_ts ? new Date(s.original_eta_ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "TBD"}
                </td>
                <td className="py-3">
                  <Badge status={s.current_status ?? "PLANNED"} />
                </td>
                <td className="py-3">
                  {s.driver_id ? (
                    <span className="text-ink-soft">{driversById.get(s.driver_id)?.driver_name ?? s.driver_id}</span>
                  ) : (
                    <AssignDriverControl
                      color={color}
                      drivers={availableDrivers}
                      busy={busy === `assign-${s.shipment_id}`}
                      onAssign={(driverId) => onAssign(s.shipment_id, driverId)}
                    />
                  )}
                </td>
                <td className="py-3 text-ink-soft">
                  {s.vehicle_id ? vehiclesById.get(s.vehicle_id)?.registration_number ?? s.vehicle_id : "—"}
                </td>
                <td className="py-3">
                  <div className="flex justify-end gap-1.5">
                    {s.current_status === "COMPLETED" && !s.archived_flag && (
                      <button
                        onClick={() => onArchive(s.shipment_id)}
                        disabled={busy === `archive-${s.shipment_id}`}
                        className="rounded-lg border border-line px-3 py-1.5 text-xs font-bold text-ink-soft transition hover:border-mist disabled:opacity-50"
                      >
                        {busy === `archive-${s.shipment_id}` ? "Archiving…" : "Archive"}
                      </button>
                    )}
                    {s.current_status !== "COMPLETED" && s.current_status !== "CANCELLED" && (
                      <>
                        <button
                          onClick={() => setCancelTarget(s)}
                          disabled={busy === `cancel-${s.shipment_id}`}
                          className="flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-bold text-rose-600 transition hover:border-rose-300 disabled:opacity-50"
                        >
                          <Ban className="h-3.5 w-3.5" />
                          {busy === `cancel-${s.shipment_id}` ? "Cancelling…" : "Cancel"}
                        </button>
                        {s.driver_id && s.vehicle_id && (
                          <button
                            onClick={() => setSlotChangeTarget(s)}
                            disabled={busy === `slot-change-${s.shipment_id}`}
                            className="flex items-center gap-1 rounded-lg border border-line px-3 py-1.5 text-xs font-bold text-ink-soft transition hover:border-mist disabled:opacity-50"
                          >
                            <ArrowRightLeft className="h-3.5 w-3.5" />
                            Slot change
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {visibleShipments.length === 0 && (
              <tr>
                <td colSpan={8} className="py-6 text-center text-sm text-ink-soft">
                  {tab === "historical" ? "No completed, cancelled or archived shipments yet." : "No shipments yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <button
        onClick={onOpenCreate}
        className="mt-5 rounded-xl px-4 py-2.5 text-sm font-bold text-white"
        style={{ background: color }}
      >
        Plan new shipment
      </button>
      {createOpen && (
        <CreateShipmentModal
          color={color}
          drivers={drivers}
          vehicles={vehicles}
          facilities={facilities}
          referenceData={referenceData}
          busy={busy === "create-shipment"}
          onClose={onCloseCreate}
          onCreate={onCreate}
        />
      )}
      {cancelTarget && (
        <CancelShipmentModal
          shipment={cancelTarget}
          busy={busy === `cancel-${cancelTarget.shipment_id}`}
          onClose={() => setCancelTarget(null)}
          onConfirm={(reason) => {
            onCancel(cancelTarget.shipment_id, reason);
            setCancelTarget(null);
          }}
        />
      )}
      {slotChangeTarget && (
        <ChangeSlotModal
          color={color}
          shipment={slotChangeTarget}
          busy={busy === `slot-change-${slotChangeTarget.shipment_id}`}
          onClose={() => setSlotChangeTarget(null)}
          onSubmit={(slotId, reason) => {
            onRequestSlotChange(slotChangeTarget.shipment_id, slotId, reason);
            setSlotChangeTarget(null);
          }}
        />
      )}
    </div>
  );
}

function CancelShipmentModal({
  shipment,
  busy,
  onClose,
  onConfirm,
}: {
  shipment: ShipmentSummary;
  busy: boolean;
  onClose: () => void;
  onConfirm: (reason?: string) => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-2xl">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-rose-50 text-rose-600">
            <Ban className="h-5 w-5" />
          </div>
          <div>
            <h3 className="text-base font-extrabold text-ink">Cancel {shipment.shipment_id}?</h3>
            <p className="mt-1 text-sm text-ink-soft">
              This releases any booked dock slot and notifies the assigned driver. This can't be undone.
            </p>
          </div>
        </div>
        <label className="mt-4 block text-xs font-bold uppercase tracking-wide text-mist">
          Reason (optional)
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            className="mt-1.5 w-full rounded-xl border border-line px-3 py-2 text-sm font-normal normal-case text-ink outline-none focus:border-ink"
            placeholder="e.g. Customer cancelled the order"
          />
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-xl border border-line px-4 py-2 text-sm font-bold text-ink-soft transition hover:border-mist"
          >
            Keep shipment
          </button>
          <button
            onClick={() => onConfirm(reason.trim() || undefined)}
            disabled={busy}
            className="rounded-xl bg-rose-600 px-4 py-2 text-sm font-bold text-white transition hover:bg-rose-500 disabled:opacity-50"
          >
            {busy ? "Cancelling…" : "Cancel shipment"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ChangeSlotModal({
  color,
  shipment,
  busy,
  onClose,
  onSubmit,
}: {
  color: string;
  shipment: ShipmentSummary;
  busy: boolean;
  onClose: () => void;
  onSubmit: (slotId: string, reason?: string) => void;
}) {
  const [slots, setSlots] = useState<DockSlot[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selectedSlotId, setSelectedSlotId] = useState("");
  const [reason, setReason] = useState("");

  useEffect(() => {
    let cancelled = false;
    getTmsDockBoardForShipment(shipment.shipment_id)
      .then((board) => !cancelled && setSlots(board))
      .catch((err) => !cancelled && setLoadError(err instanceof Error ? err.message : "Unable to load dock slots."));
    return () => {
      cancelled = true;
    };
  }, [shipment.shipment_id]);

  const available = (slots ?? []).filter((s) => s.availability_status === "AVAILABLE");

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-md flex-col rounded-2xl bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-extrabold text-ink">Request a different dock slot</h3>
            <p className="mt-1 text-sm text-ink-soft">
              For {shipment.shipment_id}. WMS staff must approve this before it takes effect.
            </p>
          </div>
          <button onClick={onClose} className="rounded-full p-1 text-mist transition hover:bg-cloud">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-4 flex-1 overflow-y-auto">
          {loadError && <p className="text-sm text-rose-600">{loadError}</p>}
          {!loadError && slots === null && (
            <div className="flex items-center gap-2 py-6 text-sm text-ink-soft">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading available slots…
            </div>
          )}
          {!loadError && slots !== null && available.length === 0 && (
            <p className="py-6 text-center text-sm italic text-mist">No open slots right now.</p>
          )}
          <div className="space-y-1.5">
            {available.map((slot) => (
              <button
                key={slot.slot_id}
                onClick={() => setSelectedSlotId(slot.slot_id)}
                className="flex w-full items-center justify-between rounded-xl border px-3 py-2.5 text-left text-sm transition"
                style={
                  selectedSlotId === slot.slot_id
                    ? { borderColor: color, background: `${color}0D` }
                    : { borderColor: "var(--color-line)" }
                }
              >
                <span className="font-bold text-ink">{slot.dock_code}</span>
                <span className="text-xs text-ink-soft">
                  {new Date(slot.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} -{" "}
                  {new Date(slot.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </button>
            ))}
          </div>
        </div>

        <label className="mt-4 block text-xs font-bold uppercase tracking-wide text-mist">
          Reason (optional)
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="mt-1.5 w-full rounded-xl border border-line px-3 py-2 text-sm font-normal normal-case text-ink outline-none focus:border-ink"
            placeholder="e.g. Driver reported a 40 min delay"
          />
        </label>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-xl border border-line px-4 py-2 text-sm font-bold text-ink-soft transition hover:border-mist"
          >
            Cancel
          </button>
          <button
            onClick={() => selectedSlotId && onSubmit(selectedSlotId, reason.trim() || undefined)}
            disabled={busy || !selectedSlotId}
            className="rounded-xl px-4 py-2 text-sm font-bold text-white transition disabled:opacity-50"
            style={{ background: color }}
          >
            {busy ? "Submitting…" : "Send request to WMS"}
          </button>
        </div>
      </div>
    </div>
  );
}

function formatTraceTime(iso?: string | null) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return null;
  }
}

type StatusTone = {
  key: string;
  label: string;
  Icon: typeof Clock;
  iconClasses: string;
  ringClasses: string;
  spin?: boolean;
};

function deriveStatusTone(
  shipment: ShipmentSummary,
  ctx: ShipmentContext | "loading" | "error" | undefined,
): StatusTone {
  if (ctx === "loading") {
    return { key: "loading", label: "Checking live status…", Icon: Loader2, iconClasses: "text-mist", ringClasses: "border-line bg-white", spin: true };
  }
  if (ctx === "error") {
    return { key: "error", label: "Couldn't load live status", Icon: AlertTriangle, iconClasses: "text-rose-600", ringClasses: "border-rose-200 bg-rose-50" };
  }
  const dock = ctx?.dock;
  const checkin = ctx?.checkin;

  if (checkin?.queue_state === "COMPLETED" || dock?.appointment_status === "COMPLETED" || shipment.current_status === "COMPLETED") {
    return { key: "done", label: "Unloaded", Icon: CheckCircle2, iconClasses: "text-emerald-600", ringClasses: "border-emerald-200 bg-emerald-50" };
  }
  if (checkin?.queue_state === "IN_DOCK" || checkin?.dock_in_ts) {
    return { key: "in-dock", label: "At dock", Icon: PackageCheck, iconClasses: "text-emerald-600", ringClasses: "border-emerald-200 bg-emerald-50" };
  }
  if (checkin?.arrival_state === "LATE") {
    return { key: "late", label: "Running late", Icon: AlertTriangle, iconClasses: "text-amber-600", ringClasses: "border-amber-200 bg-amber-50" };
  }
  if (dock?.appointment_status === "CONFIRMED") {
    return { key: "booked", label: "Dock slot booked", Icon: CalendarClock, iconClasses: "text-sky-600", ringClasses: "border-sky-200 bg-sky-50" };
  }
  return { key: "awaiting", label: "Awaiting dock booking", Icon: Clock, iconClasses: "text-mist", ringClasses: "border-line bg-white" };
}

function ShipmentStatusIcon({
  shipment,
  context,
  onHover,
}: {
  shipment: ShipmentSummary;
  context: ShipmentContext | "loading" | "error" | undefined;
  onHover: () => void;
}) {
  const tone = deriveStatusTone(shipment, context);
  const Icon = tone.Icon;
  const etaChanged = Boolean(
    shipment.latest_eta_ts && shipment.original_eta_ts && shipment.latest_eta_ts !== shipment.original_eta_ts,
  );

  // Rendered through a portal at a fixed, viewport-computed position rather
  // than absolutely positioned inside the table cell -- the shipments table
  // sits in an overflow-x-auto wrapper, and per the CSS overflow spec,
  // setting only overflow-x to a non-visible value forces the browser to
  // also compute overflow-y as auto, which was silently clipping the
  // popover instead of letting it float above the table like the reference
  // design.
  const anchorRef = useRef<HTMLButtonElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number; openUp: boolean } | null>(null);

  const show = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    onHover();
    const rect = anchorRef.current?.getBoundingClientRect();
    if (rect) {
      const openUp = rect.bottom + 340 > window.innerHeight;
      const left = Math.min(Math.max(rect.left + rect.width / 2, 170), window.innerWidth - 170);
      setCoords({ top: openUp ? rect.top - 8 : rect.bottom + 8, left, openUp });
    }
    setOpen(true);
  };
  const scheduleHide = () => {
    closeTimer.current = setTimeout(() => setOpen(false), 120);
  };

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        aria-label={tone.label}
        onMouseEnter={show}
        onMouseLeave={scheduleHide}
        onFocus={show}
        onBlur={scheduleHide}
        className={`relative flex h-7 w-7 items-center justify-center rounded-full border transition ${tone.ringClasses}`}
      >
        <Icon className={`h-3.5 w-3.5 ${tone.iconClasses} ${tone.spin ? "animate-spin" : ""}`} />
        {etaChanged && !tone.spin && (
          <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-amber-500 ring-2 ring-white" />
        )}
      </button>

      {createPortal(
        <AnimatePresence>
          {open && coords && (
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: coords.openUp ? 6 : -6 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: coords.openUp ? 6 : -6 }}
              transition={{ duration: 0.12 }}
              onMouseEnter={() => closeTimer.current && clearTimeout(closeTimer.current)}
              onMouseLeave={scheduleHide}
              style={{
                position: "fixed",
                top: coords.top,
                left: coords.left,
                transform: `translate(-50%, ${coords.openUp ? "-100%" : "0"})`,
              }}
              className="z-50 w-80"
            >
              <div className="overflow-hidden rounded-2xl border border-line bg-white shadow-2xl">
                <div className={`flex items-center gap-2 border-b border-line px-4 py-2.5 ${tone.ringClasses}`}>
                  <Icon className={`h-4 w-4 ${tone.iconClasses} ${tone.spin ? "animate-spin" : ""}`} />
                  <span className="text-xs font-extrabold uppercase tracking-wide text-ink">{tone.label}</span>
                  <span className="ml-auto font-mono text-[11px] font-bold text-mist">{shipment.shipment_id}</span>
                </div>

                {context === "loading" && (
                  <div className="px-4 py-4 text-xs text-ink-soft">Fetching dock, check-in and ETA trace…</div>
                )}
                {context === "error" && (
                  <div className="px-4 py-4 text-xs text-rose-600">Live status is unavailable right now.</div>
                )}
                {context && context !== "loading" && context !== "error" && (
                  <div className="space-y-3 px-4 py-3">
                    <TraceRow label="ETA">
                      <div className="flex items-center gap-1.5 text-xs">
                        <span className="font-bold text-ink">{formatTraceTime(shipment.original_eta_ts) ?? "TBD"}</span>
                        {etaChanged && (
                          <>
                            <span className="text-mist">→</span>
                            <span className="font-bold text-amber-600">{formatTraceTime(shipment.latest_eta_ts)}</span>
                            <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-bold text-amber-700">updated</span>
                          </>
                        )}
                      </div>
                    </TraceRow>

                    <TraceRow label="Dock booking">
                      {context.dock ? (
                        <div className="text-xs text-ink-soft">
                          <span className="font-bold text-ink">{context.dock.dock_code ?? "Dock TBD"}</span>
                          {context.dock.appointment_status && (
                            <span className="ml-1.5 rounded-full bg-cloud px-1.5 py-0.5 text-[10px] font-bold uppercase text-ink-soft">
                              {context.dock.appointment_status}
                            </span>
                          )}
                          {context.dock.slot_start_ts && context.dock.slot_end_ts && (
                            <div className="mt-0.5 flex items-center gap-1 text-[11px] text-mist">
                              <Clock className="h-3 w-3" />
                              {formatTraceTime(context.dock.slot_start_ts)} - {formatTraceTime(context.dock.slot_end_ts)}
                            </div>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs italic text-mist">No slot booked yet</span>
                      )}
                    </TraceRow>

                    <TraceRow label="Check-in">
                      {context.checkin ? (
                        <div className="space-y-1 text-xs text-ink-soft">
                          <div className="flex flex-wrap items-center gap-1.5">
                            {context.checkin.arrival_state && (
                              <span className="rounded-full bg-cloud px-1.5 py-0.5 text-[10px] font-bold uppercase text-ink-soft">
                                {context.checkin.arrival_state}
                              </span>
                            )}
                            {context.checkin.queue_state && (
                              <span className="rounded-full bg-cloud px-1.5 py-0.5 text-[10px] font-bold uppercase text-ink-soft">
                                {context.checkin.queue_state.replaceAll("_", " ")}
                              </span>
                            )}
                          </div>
                          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-mist">
                            {context.checkin.gate_in_ts && <span>Gate in {formatTraceTime(context.checkin.gate_in_ts)}</span>}
                            {context.checkin.dock_in_ts && <span>Dock in {formatTraceTime(context.checkin.dock_in_ts)}</span>}
                            {context.checkin.unload_end_ts && <span>Unload done {formatTraceTime(context.checkin.unload_end_ts)}</span>}
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs italic text-mist">Not checked in yet</span>
                      )}
                    </TraceRow>
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}

function TraceRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[80px_1fr] items-start gap-2">
      <span className="pt-0.5 text-[10px] font-bold uppercase tracking-wide text-mist">{label}</span>
      <div>{children}</div>
    </div>
  );
}

function CreateShipmentModal({
  color,
  drivers,
  vehicles,
  facilities,
  referenceData,
  busy,
  onClose,
  onCreate,
}: {
  color: string;
  drivers: TmsDriver[];
  vehicles: TmsVehicle[];
  facilities: TmsFacility[];
  referenceData: ShipmentReferenceData;
  busy: boolean;
  onClose: () => void;
  onCreate: (input: ShipmentCreateInput) => void;
}) {
  const [orderReference, setOrderReference] = useState("");
  const [destinationFacilityId, setDestinationFacilityId] = useState("");
  const [originKey, setOriginKey] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [productCategory, setProductCategory] = useState("");
  const [loadWeightKg, setLoadWeightKg] = useState("");
  const [priorityCode, setPriorityCode] = useState("NORMAL");
  const [driverId, setDriverId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [plannedDepartureTs, setPlannedDepartureTs] = useState("");
  const [originalEtaTs, setOriginalEtaTs] = useState("");
  const [expectedUnloadMin, setExpectedUnloadMin] = useState("45");
  const [formError, setFormError] = useState("");

  // origins/product categories are dropdown-only, sourced from whatever
  // already exists in Supabase (see ShipmentReferenceData) -- no free text.
  // Keyed as "name|||city" since (name, city) together identify one origin.
  const origins = referenceData.origins;
  const selectedOrigin = origins.find((o) => `${o.origin_name}|||${o.origin_city ?? ""}` === originKey);

  // Only offer drivers/vehicles that are actually available to take a new load.
  const availableDrivers = drivers.filter((d) => d.driver_status === "ACTIVE");
  const availableVehicles = vehicles.filter((v) => v.active_flag);

  const selectedDriver = availableDrivers.find((d) => d.driver_id === driverId);
  // Strictly limited to vehicles that share the selected driver's carrier --
  // no fallback to the full vehicle list. Showing an unrelated carrier's
  // vehicle here would let the form submit a combination the backend is
  // guaranteed to reject ("Assigned driver and vehicle must belong to the
  // same carrier"), so the dropdown itself must only ever offer real,
  // carrier-matched options from the database.
  const eligibleVehicles = selectedDriver?.carrier_id
    ? availableVehicles.filter((v) => v.carrier_id === selectedDriver.carrier_id)
    : [];

  function submit() {
    if (
      !orderReference ||
      !destinationFacilityId ||
      !selectedOrigin ||
      !selectedOrigin.origin_city ||
      !driverId ||
      !vehicleId ||
      !customerName.trim() ||
      !productCategory ||
      !loadWeightKg ||
      !plannedDepartureTs ||
      !originalEtaTs ||
      !expectedUnloadMin
    ) {
      setFormError(
        "Order reference, destination, origin, customer, product category, load weight, driver, vehicle, planned departure, ETA, and unload time are all required."
      );
      return;
    }
    const driver = availableDrivers.find((d) => d.driver_id === driverId);
    if (!driver?.carrier_id) {
      setFormError("Selected driver has no carrier on file.");
      return;
    }
    setFormError("");
    onCreate({
      order_reference: orderReference,
      carrier_id: driver.carrier_id,
      driver_id: driverId,
      vehicle_id: vehicleId,
      origin_name: selectedOrigin.origin_name,
      origin_city: selectedOrigin.origin_city,
      destination_facility_id: destinationFacilityId,
      customer_name: customerName.trim(),
      product_category: productCategory,
      load_weight_kg: Number(loadWeightKg),
      priority_code: priorityCode || undefined,
      planned_departure_ts: new Date(plannedDepartureTs).toISOString(),
      original_eta_ts: new Date(originalEtaTs).toISOString(),
      expected_unload_min: Number(expectedUnloadMin),
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-3xl bg-white p-6 shadow-pop">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-extrabold text-ink">Plan new shipment</h3>
          <button onClick={onClose} className="text-sm font-bold text-ink-soft hover:text-ink">
            Close
          </button>
        </div>
        {formError && (
          <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">{formError}</div>
        )}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field label="Order reference">
            <input value={orderReference} onChange={(e) => setOrderReference(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" placeholder="ORD-1042" />
          </Field>
          <Field label="Destination facility">
            <select value={destinationFacilityId} onChange={(e) => setDestinationFacilityId(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="">Select destination…</option>
              {facilities.map((f) => (
                <option key={f.facility_id} value={f.facility_id}>
                  {f.facility_name ?? f.facility_id}
                  {f.city ? ` · ${f.city}` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Origin">
            <select
              value={originKey}
              onChange={(e) => setOriginKey(e.target.value)}
              className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink"
            >
              <option value="">{origins.length === 0 ? "No origins on file yet" : "Select origin…"}</option>
              {origins.map((o) => (
                <option key={`${o.origin_name}|||${o.origin_city ?? ""}`} value={`${o.origin_name}|||${o.origin_city ?? ""}`}>
                  {o.origin_name}
                  {o.origin_city ? ` · ${o.origin_city}` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Customer name">
            <input value={customerName} onChange={(e) => setCustomerName(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" placeholder="RajRetail Distribution" />
          </Field>
          <Field label="Product category">
            <select
              value={productCategory}
              onChange={(e) => setProductCategory(e.target.value)}
              className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink"
            >
              <option value="">
                {referenceData.product_categories.length === 0 ? "No categories on file yet" : "Select category…"}
              </option>
              {referenceData.product_categories.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Load weight (kg)">
            <input type="number" min={1} value={loadWeightKg} onChange={(e) => setLoadWeightKg(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" placeholder="12000" />
          </Field>
          <Field label="Priority">
            <select value={priorityCode} onChange={(e) => setPriorityCode(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="LOW">Low</option>
              <option value="NORMAL">Normal</option>
              <option value="HIGH">High</option>
              <option value="CRITICAL">Critical</option>
            </select>
          </Field>
          <Field label="Driver">
            <select value={driverId} onChange={(e) => { setDriverId(e.target.value); setVehicleId(""); }} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="">Select driver…</option>
              {availableDrivers.map((d) => (
                <option key={d.driver_id} value={d.driver_id}>
                  {d.driver_name ?? d.driver_id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Vehicle">
            <select
              value={vehicleId}
              onChange={(e) => setVehicleId(e.target.value)}
              className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink"
              disabled={!driverId || eligibleVehicles.length === 0}
            >
              <option value="">
                {!driverId
                  ? "Select a driver first…"
                  : eligibleVehicles.length === 0
                  ? "No active vehicles for this driver's carrier"
                  : "Select vehicle…"}
              </option>
              {eligibleVehicles.map((v) => (
                <option key={v.vehicle_id} value={v.vehicle_id}>
                  {v.registration_number ?? v.vehicle_id}
                </option>
              ))}
            </select>
            {driverId && eligibleVehicles.length === 0 && (
              <p className="mt-1 text-xs text-rose-600">
                This driver's carrier has no active vehicle on file -- add one in TMS before assigning this driver.
              </p>
            )}
          </Field>
          <Field label="Planned departure">
            <input type="datetime-local" value={plannedDepartureTs} onChange={(e) => setPlannedDepartureTs(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" />
          </Field>
          <Field label="Planned ETA">
            <input type="datetime-local" value={originalEtaTs} onChange={(e) => setOriginalEtaTs(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" />
          </Field>
          <Field label="Expected unload (min)">
            <input type="number" min={1} value={expectedUnloadMin} onChange={(e) => setExpectedUnloadMin(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" />
          </Field>
        </div>
        <button
          onClick={submit}
          disabled={busy}
          className="mt-5 w-full rounded-xl px-4 py-2.5 text-sm font-bold text-white disabled:opacity-50"
          style={{ background: color }}
        >
          {busy ? "Creating…" : "Create shipment"}
        </button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-xs font-bold text-ink-soft">
      {label}
      <div className="mt-1">{children}</div>
    </label>
  );
}

function AssignDriverControl({
  color,
  drivers,
  busy,
  onAssign,
}: {
  color: string;
  drivers: TmsDriver[];
  busy: boolean;
  onAssign: (driverId: string) => void;
}) {
  const [selected, setSelected] = useState("");
  return (
    <div className="flex items-center gap-2">
      <select
        value={selected}
        onChange={(e) => setSelected(e.target.value)}
        className="rounded-lg border border-line px-2 py-1.5 text-xs font-semibold text-ink"
        disabled={busy}
      >
        <option value="">Select driver…</option>
        {drivers.map((d) => (
          <option key={d.driver_id} value={d.driver_id}>
            {d.driver_name ?? d.driver_id}
          </option>
        ))}
      </select>
      <button
        onClick={() => selected && onAssign(selected)}
        disabled={!selected || busy}
        className="rounded-lg px-3 py-1.5 text-xs font-bold text-white disabled:opacity-50"
        style={{ background: color }}
      >
        {busy ? "Assigning…" : "Assign"}
      </button>
    </div>
  );
}

// Visual styling per availability_status, driving both the slot chip color
// and the hover tooltip badge. Falls back gracefully for any status string
// the backend adds later (e.g. raw slot_status passthrough).
const DOCK_SLOT_STYLES: Record<string, { chip: string; ring: string }> = {
  AVAILABLE: { chip: "border-emerald-300 bg-emerald-50 text-emerald-700", ring: "16, 185, 129" },
  HELD: { chip: "border-amber-300 bg-amber-50 text-amber-700", ring: "217, 119, 6" },
  OCCUPIED: { chip: "border-rose-300 bg-rose-50 text-rose-700", ring: "225, 29, 72" },
  BLOCKED: { chip: "border-slate-300 bg-slate-100 text-slate-500", ring: "100, 116, 139" },
  CLOSED: { chip: "border-slate-300 bg-slate-100 text-slate-500", ring: "100, 116, 139" },
};

function dockSlotStyle(status: string) {
  return DOCK_SLOT_STYLES[status] ?? { chip: "border-slate-300 bg-slate-50 text-slate-500", ring: "100, 116, 139" };
}

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// The dock board now spans several days of backfilled capacity (see the
// backend's ensure_future_slots), not just "today" -- without a date label,
// a flat time-only list of e.g. "09:00 AM - 10:00 AM" repeated once per
// upcoming day looked like duplicated or nonsensical data.
function formatDayLabel(ts: string) {
  const date = new Date(ts);
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === tomorrow.toDateString()) return "Tomorrow";
  return date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

function dayKey(ts: string) {
  return new Date(ts).toDateString();
}

function WmsChangeRequestsHeader({
  color,
  shipments,
  onDecided,
  onMessage,
}: {
  color: string;
  shipments: ShipmentSummary[];
  onDecided: () => void;
  onMessage: (text: string, tone: "success" | "error") => void;
}) {
  const [requests, setRequests] = useState<ChangeRequest[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");

  // Facility-scoped by cross-referencing against the shipments this WMS
  // staff member can already see (already resolved server-side from their
  // own staff_facility_assignments row) -- dock_scheduler's change-request
  // endpoint itself has no facility filter of its own.
  const shipmentIds = useMemo(() => new Set(shipments.map((s) => s.shipment_id)), [shipments]);

  const refresh = useCallback(async () => {
    try {
      const pending = await listPendingChangeRequests();
      setRequests(pending.filter((r) => shipmentIds.has(r.shipment_id)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load change requests.");
    }
  }, [shipmentIds]);

  useEffect(() => {
    void refresh();
    const interval = setInterval(() => void refresh(), 15_000);
    return () => clearInterval(interval);
  }, [refresh]);

  const decide = async (id: string, approve: boolean) => {
    setBusyId(id);
    setError("");
    const target = requests.find((r) => r.change_request_id === id);
    try {
      await decideChangeRequest(id, approve);
      await refresh();
      onDecided();
      onMessage(
        approve
          ? `Approved -- ${target?.shipment_id ?? "shipment"} moved to ${target?.dock_code ?? "the requested dock"}.`
          : `Declined the slot change request for ${target?.shipment_id ?? "that shipment"}.`,
        "success"
      );
    } catch (err) {
      const text = err instanceof Error ? err.message : "Unable to record that decision.";
      setError(text);
      onMessage(text, "error");
    } finally {
      setBusyId(null);
    }
  };

  if (requests.length === 0) return null;

  return (
    <div className="mb-5 overflow-hidden rounded-2xl border border-amber-200 bg-amber-50">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4 text-amber-700" />
          <span className="text-sm font-extrabold text-amber-800">
            {requests.length} dock slot change {requests.length === 1 ? "request" : "requests"} awaiting your approval
          </span>
        </div>
        <ChevronDown className={`h-4 w-4 shrink-0 text-amber-700 transition-transform ${expanded ? "rotate-180" : ""}`} />
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-amber-200 px-4 py-3">
          {error && <p className="text-xs font-semibold text-rose-600">{error}</p>}
          {requests.map((r) => (
            <div
              key={r.change_request_id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-white p-3"
            >
              <div className="text-xs">
                <p className="font-bold text-ink">
                  {r.shipment_id} → {r.dock_code ?? "Dock TBD"}
                </p>
                <p className="mt-0.5 text-ink-soft">
                  {r.slot_start_ts && r.slot_end_ts
                    ? `${formatTime(r.slot_start_ts)} - ${formatTime(r.slot_end_ts)}`
                    : "Time TBD"}
                  {" · requested by "}
                  {r.requested_by_role === "TMS" ? "dispatch" : "driver"}
                </p>
                {r.reason && <p className="mt-0.5 italic text-mist">"{r.reason}"</p>}
                {r.displaced_shipment_id && (
                  <p className="mt-1 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 font-bold text-amber-800">
                    Approving this will also move {r.displaced_shipment_id}'s appointment to slot{" "}
                    {r.displaced_to_slot_id ?? "TBD"}.
                  </p>
                )}
              </div>
              <div className="flex gap-1.5">
                <button
                  onClick={() => void decide(r.change_request_id, false)}
                  disabled={busyId === r.change_request_id}
                  className="rounded-lg border border-line px-3 py-1.5 text-xs font-bold text-ink-soft transition hover:border-mist disabled:opacity-50"
                >
                  Decline
                </button>
                <button
                  onClick={() => void decide(r.change_request_id, true)}
                  disabled={busyId === r.change_request_id}
                  className="rounded-lg px-3 py-1.5 text-xs font-bold text-white transition disabled:opacity-50"
                  style={{ background: color }}
                >
                  {busyId === r.change_request_id ? "Approving…" : "Approve"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function WmsPanel({
  color,
  board,
  shipments,
  selectedShipmentId,
  onSelectShipment,
  selectedSlotId,
  onSelectSlot,
  onReserve,
  reserving,
  onDecided,
  onMessage,
}: {
  color: string;
  board: DockSlot[];
  shipments: ShipmentSummary[];
  selectedShipmentId: string;
  onSelectShipment: (shipmentId: string) => void;
  selectedSlotId: string;
  onSelectSlot: (slotId: string) => void;
  onReserve: () => void;
  reserving: boolean;
  onDecided: () => void;
  onMessage: (text: string, tone: "success" | "error") => void;
}) {
  // Group by real dock_code from the DB response -- the number of dock
  // columns and slots rendered adjusts automatically to whatever docks and
  // slots the backend actually returns for this shipment, nothing hardcoded.
  const docks = useMemo(() => {
    const byDock = new Map<string, DockSlot[]>();
    for (const slot of board) {
      const list = byDock.get(slot.dock_code) ?? [];
      list.push(slot);
      byDock.set(slot.dock_code, list);
    }
    for (const list of byDock.values()) list.sort((a, b) => a.start.localeCompare(b.start));
    return [...byDock.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [board]);

  const selectedSlot = board.find((s) => s.slot_id === selectedSlotId) ?? null;

  // Only fetched when the board is actually empty -- avoids a second
  // round trip on every normal (non-empty) board load. Re-fetched whenever
  // the empty shipment changes so switching shipments doesn't show a stale
  // reason from the previous one.
  const [emptyReason, setEmptyReason] = useState<string | null>(null);
  useEffect(() => {
    if (docks.length > 0 || !selectedShipmentId) {
      setEmptyReason(null);
      return;
    }
    let cancelled = false;
    getDockBoardUnavailableReason(selectedShipmentId)
      .then((res) => {
        if (!cancelled) setEmptyReason(res.reason);
      })
      .catch(() => {
        if (!cancelled) setEmptyReason(null);
      });
    return () => {
      cancelled = true;
    };
  }, [docks.length, selectedShipmentId]);

  return (
    <div>
      <WmsChangeRequestsHeader color={color} shipments={shipments} onDecided={onDecided} onMessage={onMessage} />
      <h2 className="text-lg font-extrabold text-ink">Dock & appointment slots</h2>
      <p className="text-sm text-ink-soft">Hover a slot for details. Click an open slot to select it, then reserve.</p>
      <div className="mt-4 max-w-xs">
        <Field label="Shipment">
          <select
            value={selectedShipmentId}
            onChange={(e) => onSelectShipment(e.target.value)}
            className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink"
          >
            {shipments.length === 0 && <option value="">No shipments awaiting scheduling</option>}
            {shipments.map((s) => (
              <option key={s.shipment_id} value={s.shipment_id}>
                {s.shipment_id} · {s.order_reference ?? "—"}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {docks.length === 0 && (
        <p className="mt-4 text-sm text-ink-soft">
          {emptyReason ?? "No compatible docks or slots for this shipment right now."}
        </p>
      )}

      <div className="mt-5 flex flex-wrap gap-3">
        {docks.map(([dockCode, slots]) => (
          <div key={dockCode} className="w-full min-w-[180px] rounded-2xl border border-line p-3 sm:w-[calc(50%-0.375rem)] lg:w-[calc(25%-0.5625rem)]">
            <p className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink-soft">
              Dock {dockCode}
              {slots[0] && <span className="ml-1 font-medium normal-case text-mist">· {slots[0].dock_type}</span>}
            </p>
            <div className="flex flex-col gap-1.5">
              {(() => {
                let lastDay = "";
                return slots.map((slot) => {
                  const style = dockSlotStyle(slot.availability_status);
                  const isSelected = slot.slot_id === selectedSlotId;
                  const selectable = slot.availability_status === "AVAILABLE";
                  const thisDay = dayKey(slot.start);
                  const showDayLabel = thisDay !== lastDay;
                  lastDay = thisDay;
                  return (
                    <div key={slot.slot_id}>
                      {showDayLabel && (
                        <p className="mb-1 mt-2 text-[10px] font-bold uppercase tracking-wide text-mist first:mt-0">
                          {formatDayLabel(slot.start)}
                        </p>
                      )}
                      <div className="group relative">
                        <button
                          type="button"
                          disabled={!selectable}
                          onClick={() => onSelectSlot(isSelected ? "" : slot.slot_id)}
                          className={`w-full rounded-lg border px-2.5 py-1.5 text-left text-xs font-semibold transition ${style.chip} ${
                            selectable ? "cursor-pointer hover:brightness-95" : "cursor-not-allowed opacity-80"
                          }`}
                          style={isSelected ? { boxShadow: `0 0 0 2px ${color}` } : undefined}
                        >
                          {formatTime(slot.start)} – {formatTime(slot.end)}
                        </button>
                        {/* Hoverable diagram detail: full time range, status, and
                            occupant, revealed on hover without needing a click. */}
                        <div className="pointer-events-none absolute left-1/2 top-full z-10 mt-1.5 w-56 -translate-x-1/2 rounded-xl border border-line bg-white p-3 text-xs text-ink opacity-0 shadow-pop transition duration-150 group-hover:opacity-100">
                          <p className="font-bold">
                            {slot.dock_code} · {slot.slot_id}
                          </p>
                          <p className="mt-1 flex items-center gap-1.5 text-ink-soft">
                            <Clock className="h-3.5 w-3.5" />
                            {formatDayLabel(slot.start)}, {formatTime(slot.start)} – {formatTime(slot.end)}
                          </p>
                          <div className="mt-1.5">
                            <Badge status={slot.availability_status} />
                          </div>
                          {slot.occupant_shipment_id && (
                            <p className="mt-1.5 text-ink-soft">
                              Booked by {slot.occupant_shipment_id}
                              {slot.occupant_driver_name ? ` · ${slot.occupant_driver_name}` : ""}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                });
              })()}
              {slots.length === 0 && <p className="text-xs text-mist">No slots</p>}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button
          onClick={onReserve}
          disabled={!selectedSlot || reserving}
          className="rounded-xl px-4 py-2.5 text-sm font-bold text-white transition disabled:cursor-not-allowed disabled:opacity-50"
          style={{ background: color }}
        >
          {reserving ? "Reserving…" : "Reserve a slot"}
        </button>
        {selectedSlot ? (
          <p className="text-xs text-ink-soft">
            Selected: {selectedSlot.dock_code} · {formatTime(selectedSlot.start)} – {formatTime(selectedSlot.end)}
          </p>
        ) : (
          <p className="text-xs text-mist">Click an available (green) slot above to select it.</p>
        )}
      </div>
    </div>
  );
}

const TIMING_STYLES: Record<string, { label: string; bg: string; text: string }> = {
  EARLY: { label: "Arrived early", bg: "#eff6ff", text: "#1d4ed8" },
  ON_TIME: { label: "On time", bg: "#ecfdf5", text: "#047857" },
  LATE: { label: "Running late", bg: "#fef2f2", text: "#b91c1c" },
};

function CheckinPanel({
  color,
  record,
  busy,
  shipments,
  selectedShipmentId,
  onSelectShipment,
  onGateIn,
  onApproveGate,
  onQueue,
  onDock,
  onComplete,
}: {
  color: string;
  record: CheckInRecord | null;
  busy: string | null;
  shipments: ShipmentSummary[];
  selectedShipmentId: string;
  onSelectShipment: (shipmentId: string) => void;
  onGateIn: () => void;
  onApproveGate: () => void;
  onQueue: () => void;
  onDock: () => void;
  onComplete: () => void;
}) {
  const stages: Array<{ key: string; label: string; icon: typeof MapPin; done: boolean; active: boolean; value: string | null }> = [
    { key: "gate", label: "Gate check-in", icon: MapPin, done: Boolean(record?.gate_in_at), active: record?.arrival_status === "GATE_IN", value: record?.gate_in_at ?? null },
    { key: "queue", label: "Queue", icon: Clock, done: record?.arrival_status === "WAITING" || record?.arrival_status === "DOCKED" || record?.arrival_status === "COMPLETED", active: record?.arrival_status === "WAITING", value: record?.queue_status && record.queue_status !== "NONE" ? record.queue_status.replace("_", " ").toLowerCase() : null },
    { key: "dock", label: "Docked", icon: PackageCheck, done: Boolean(record?.dock_in_at), active: record?.arrival_status === "DOCKED", value: record?.dock_in_at ?? null },
    { key: "complete", label: "Unload complete", icon: CheckCircle2, done: Boolean(record?.completed_at), active: record?.arrival_status === "COMPLETED", value: record?.completed_at ?? null },
  ];

  const needsApproval = Boolean(record?.gate_in_at) && !record?.staff_approved;
  const timing = record?.timing_status ? TIMING_STYLES[record.timing_status] : null;

  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink">Gate & yard activity</h2>
      <p className="text-sm text-ink-soft">Real-time arrivals and yard movement.</p>
      <div className="mt-4 max-w-xs">
        <Field label="Shipment">
          <select
            value={selectedShipmentId}
            onChange={(e) => onSelectShipment(e.target.value)}
            className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink"
          >
            {shipments.length === 0 && <option value="">No active shipments</option>}
            {shipments.map((s) => (
              <option key={s.shipment_id} value={s.shipment_id}>
                {s.shipment_id} · {s.order_reference ?? "—"}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {needsApproval && (
        <div className="mt-4 flex flex-col items-start justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 sm:flex-row sm:items-center">
          <div className="flex items-start gap-2.5">
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
            <div>
              <p className="text-sm font-bold text-amber-800">Driver reported gate arrival — pending your approval</p>
              <p className="mt-0.5 text-xs text-amber-700">
                TMS and WMS won't see this shipment as checked in until you confirm the driver is actually at the gate.
              </p>
            </div>
          </div>
          <button
            onClick={onApproveGate}
            disabled={Boolean(busy)}
            className="flex shrink-0 items-center gap-1.5 rounded-xl bg-amber-600 px-4 py-2 text-sm font-bold text-white shadow-soft transition hover:bg-amber-500 disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
            Approve check-in
          </button>
        </div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-line p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-bold text-ink">Shipment {record?.shipment_id ?? selectedShipmentId ?? "—"}</p>
            <div className="flex items-center gap-1.5">
              {record?.staff_approved && (
                <span className="flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-bold text-emerald-700">
                  <ShieldCheck className="h-3 w-3" /> approved
                </span>
              )}
              {timing && (
                <span className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold" style={{ background: timing.bg, color: timing.text }}>
                  {record?.timing_status === "LATE" ? <AlertTriangle className="h-3 w-3" /> : <Clock className="h-3 w-3" />}
                  {timing.label}
                </span>
              )}
            </div>
          </div>

          {(record?.driver_name || record?.driver_phone) && (
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink-soft">
              {record?.driver_name && <span className="font-medium text-ink">{record.driver_name}</span>}
              {record?.driver_phone && (
                <span className="flex items-center gap-1">
                  <Phone className="h-3 w-3" /> {record.driver_phone}
                </span>
              )}
            </div>
          )}

          <p className="mt-2 text-xs text-ink-soft">Backend validated status only. React never reimplements the state machine.</p>

          <div className="mt-4 space-y-2">
            {!record && <p className="text-sm text-ink-soft">Not checked in yet.</p>}
            {stages.map((stage) => {
              const Icon = stage.icon;
              return (
                <div
                  key={stage.key}
                  className="flex items-center justify-between rounded-xl px-3 py-2 text-sm transition-colors"
                  style={
                    stage.active
                      ? { background: `${color}14`, border: `1px solid ${color}40` }
                      : stage.done
                      ? { background: "#ecfdf5", border: "1px solid transparent" }
                      : { background: "var(--color-cloud)", border: "1px solid transparent", opacity: 0.7 }
                  }
                >
                  <span className="flex items-center gap-2 font-medium text-ink">
                    <Icon className="h-4 w-4" style={{ color: stage.done || stage.active ? color : "var(--color-mist)" }} />
                    {stage.label}
                  </span>
                  <span className="text-xs text-ink-soft">
                    {stage.value ? (stage.key === "gate" || stage.key === "dock" || stage.key === "complete" ? new Date(stage.value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : stage.value) : stage.done ? "done" : "pending"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
        <div className="rounded-2xl border border-line p-4">
          <div className="flex flex-col gap-2">
            <button onClick={onGateIn} disabled={Boolean(busy) || !selectedShipmentId} className="rounded-xl px-4 py-2.5 text-sm font-bold text-white disabled:opacity-50" style={{ background: color }}>
              {busy ? <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> : null}
              Gate check-in
            </button>
            <button onClick={onQueue} disabled={Boolean(busy) || !selectedShipmentId} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink disabled:opacity-50">
              Queue update
            </button>
            <button onClick={onDock} disabled={Boolean(busy) || !selectedShipmentId} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink disabled:opacity-50">
              Mark docked
            </button>
            <button onClick={onComplete} disabled={Boolean(busy) || !selectedShipmentId} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink disabled:opacity-50">
              Complete unload
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

