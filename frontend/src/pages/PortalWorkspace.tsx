import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { Clock, Loader2, RefreshCw } from "lucide-react";
import { getService } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { ApiClientError } from "../services/api";
import {
  completeUnload,
  fetchCheckInStatus,
  gateCheckIn,
  listCheckinFacilityOptions,
  listShipmentsForCheckin,
  markDocked,
  updateQueue,
} from "../services/checkinApi";
import {
  archiveShipment,
  assignShipmentDriver,
  createShipment,
  getNextShipmentIds,
  listDrivers,
  listFacilities,
  listShipments,
  listVehicles,
} from "../services/tmsApi";
import { confirmBooking, getDockBoard, holdSlot, listShipmentsForScheduling } from "../services/dockSchedulerApi";
import type {
  CheckInRecord,
  CheckInShipmentSummary,
  DockSlot,
  ShipmentCreateInput,
  ShipmentSummary,
  TmsDriver,
  TmsFacility,
  TmsVehicle,
} from "../types/api";
import DriversPortal from "../components/drivers/DriversPortal";

export default function PortalWorkspace() {
  const { serviceId } = useParams();
  const service = getService(serviceId);
  const { isAuthed, sessions, logout } = useAuth();
  const [checkin, setCheckin] = useState<CheckInRecord | null>(null);
  const [shipments, setShipments] = useState<ShipmentSummary[]>([]);
  const [tmsDrivers, setTmsDrivers] = useState<TmsDriver[]>([]);
  const [tmsVehicles, setTmsVehicles] = useState<TmsVehicle[]>([]);
  const [tmsFacilities, setTmsFacilities] = useState<TmsFacility[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [dockBoard, setDockBoard] = useState<DockSlot[]>([]);
  const [selectedSlotId, setSelectedSlotId] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [wmsShipments, setWmsShipments] = useState<ShipmentSummary[]>([]);
  const [checkinShipments, setCheckinShipments] = useState<CheckInShipmentSummary[]>([]);
  const [checkinFacilities, setCheckinFacilities] = useState<TmsFacility[]>([]);
  const [activeShipmentId, setActiveShipmentId] = useState<string>("");

  if (!service) return <Navigate to="/" replace />;
  if (!isAuthed(service.id)) return <Navigate to={`/auth/${service.id}`} replace />;

  const name = sessions[service.id]?.name ?? "there";
  const assignedCheckinFacilityId = sessions.checkin?.facilityId;

  useEffect(() => {
    if (service.id !== "checkin") return;
    void (async () => {
      await Promise.all([refreshCheckinShipments(), refreshCheckinFacilities()]);
    })();
  }, [service.id]);

  useEffect(() => {
    if (service.id !== "checkin" || !activeShipmentId) return;
    void refreshCheckin();
  }, [service.id, activeShipmentId]);

  async function refreshCheckinShipments(preferredShipmentId?: string) {
    try {
      const items = await listShipmentsForCheckin();
      setCheckinShipments(items);
      const preferred = preferredShipmentId ?? activeShipmentId;
      if (preferred && items.some((item) => item.shipment_id === preferred)) {
        setActiveShipmentId(preferred);
      } else if (items.length > 0) {
        setActiveShipmentId(items[0].shipment_id);
      } else {
        setActiveShipmentId("");
        setCheckin(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load shipments.");
    }
  }

  async function refreshCheckinFacilities() {
    try {
      const items = await listCheckinFacilityOptions();
      setCheckinFacilities(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load facilities.");
    }
  }

  useEffect(() => {
    if (service.id !== "tms") return;
    void refreshTms();
  }, [service.id]);

  async function refreshTms() {
    try {
      const [items, drivers, vehicles, facilities] = await Promise.all([
        listShipments(),
        listDrivers(),
        listVehicles(),
        listFacilities(),
      ]);
      setShipments(items);
      setTmsDrivers(drivers);
      setTmsVehicles(vehicles);
      setTmsFacilities(facilities);
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

  useEffect(() => {
    if (service.id !== "wms") return;
    void (async () => {
      try {
        const items = await listShipmentsForScheduling();
        const relevant = items.filter((s) =>
          ["PLANNED", "ASSIGNED", "IN_TRANSIT", "AT_GATE", "WAITING"].includes(s.current_status ?? "")
        );
        setWmsShipments(relevant);
        if (relevant.length > 0) setActiveShipmentId(relevant[0].shipment_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load shipments.");
      }
    })();
  }, [service.id]);

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
    setError("");
    try {
      await holdSlot({ shipment_id: activeShipmentId, slot_id: selectedSlotId });
      await confirmBooking({ shipment_id: activeShipmentId, slot_id: selectedSlotId, accepted: true });
      setMessage(`Slot ${selectedSlotId} reserved for ${activeShipmentId}.`);
      await refreshDockBoard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to reserve that slot.");
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
      await refreshCheckinShipments(record.shipment_id);
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

  const selectedCheckinShipment = useMemo(
    () => checkinShipments.find((shipment) => shipment.shipment_id === activeShipmentId) ?? null,
    [checkinShipments, activeShipmentId]
  );
  const assignedCheckinFacility = useMemo(
    () => checkinFacilities.find((facility) => facility.facility_id === assignedCheckinFacilityId) ?? null,
    [checkinFacilities, assignedCheckinFacilityId]
  );

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
        {message && <div className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
        {service.id === "tms" && (
          <TmsPanel
            color={service.color}
            shipments={shipments}
            drivers={tmsDrivers}
            vehicles={tmsVehicles}
            facilities={tmsFacilities}
            busy={busy}
            onAssign={handleAssignDriver}
            onArchive={handleArchive}
            createOpen={createOpen}
            onOpenCreate={() => setCreateOpen(true)}
            onCloseCreate={() => setCreateOpen(false)}
            onCreate={handleCreateShipment}
          />
        )}
        {service.id === "wms" && (
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
          />
        )}
        {service.id === "checkin" && (
          <CheckinPanel
            color={service.color}
            record={checkin}
            timeline={checkinTimeline}
            busy={busy}
            shipments={checkinShipments}
            selectedShipment={selectedCheckinShipment}
            assignedFacility={assignedCheckinFacility}
            hasAssignedFacility={Boolean(assignedCheckinFacilityId)}
            selectedShipmentId={activeShipmentId}
            onSelectShipment={setActiveShipmentId}
            onGateIn={() =>
              mutateCheckin(
                () =>
                  gateCheckIn({
                    shipment_id: activeShipmentId,
                    facility_id: selectedCheckinShipment?.destination_facility_id ?? "",
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

function TmsPanel({
  color,
  shipments,
  drivers,
  vehicles,
  facilities,
  busy,
  onAssign,
  onArchive,
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
  busy: string | null;
  onAssign: (shipmentId: string, driverId: string) => void;
  onArchive: (shipmentId: string) => void;
  createOpen: boolean;
  onOpenCreate: () => void;
  onCloseCreate: () => void;
  onCreate: (input: ShipmentCreateInput) => void;
}) {
  const driversById = new Map(drivers.map((d) => [d.driver_id, d]));
  const vehiclesById = new Map(vehicles.map((v) => [v.vehicle_id, v]));
  const facilitiesById = new Map(facilities.map((f) => [f.facility_id, f]));
  const availableDrivers = drivers.filter((d) => d.driver_status === "ACTIVE");
  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink">Active shipments</h2>
      <p className="text-sm text-ink-soft">Live loads currently assigned across the fleet.</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-sm">
          <thead>
            <tr className="text-left text-xs font-bold uppercase tracking-wide text-mist">
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
            {shipments.map((s) => (
              <tr key={s.shipment_id} className="border-t border-line">
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
                <td className="py-3 text-right">
                  {s.current_status === "COMPLETED" && !s.archived_flag && (
                    <button
                      onClick={() => onArchive(s.shipment_id)}
                      disabled={busy === `archive-${s.shipment_id}`}
                      className="rounded-lg border border-line px-3 py-1.5 text-xs font-bold text-ink-soft transition hover:border-mist disabled:opacity-50"
                    >
                      {busy === `archive-${s.shipment_id}` ? "Archiving…" : "Archive"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {shipments.length === 0 && (
              <tr>
                <td colSpan={7} className="py-6 text-center text-sm text-ink-soft">
                  No shipments yet.
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
          busy={busy === "create-shipment"}
          onClose={onCloseCreate}
          onCreate={onCreate}
        />
      )}
    </div>
  );
}

function CreateShipmentModal({
  color,
  drivers,
  vehicles,
  facilities,
  busy,
  onClose,
  onCreate,
}: {
  color: string;
  drivers: TmsDriver[];
  vehicles: TmsVehicle[];
  facilities: TmsFacility[];
  busy: boolean;
  onClose: () => void;
  onCreate: (input: ShipmentCreateInput) => void;
}) {
  const [shipmentId, setShipmentId] = useState("");
  const [orderReference, setOrderReference] = useState("");
  const [idsError, setIdsError] = useState("");
  const [destinationFacilityId, setDestinationFacilityId] = useState("");
  const [originName, setOriginName] = useState("");
  const [originCity, setOriginCity] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [productCategory, setProductCategory] = useState("");
  const [loadWeightKg, setLoadWeightKg] = useState("1000");
  const [palletCount, setPalletCount] = useState("");
  const [requiredDockType, setRequiredDockType] = useState("ANY");
  const [temperatureControlRequired, setTemperatureControlRequired] = useState("0");
  const [priorityCode, setPriorityCode] = useState<"LOW" | "NORMAL" | "HIGH" | "CRITICAL">("NORMAL");
  const [currentStatus, setCurrentStatus] = useState<
    | "PLANNED"
    | "ASSIGNED"
    | "IN_TRANSIT"
    | "AT_GATE"
    | "WAITING"
    | "IN_DOCK"
    | "COMPLETED"
    | "CANCELLED"
  >("PLANNED");
  const [driverId, setDriverId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [plannedDepartureTs, setPlannedDepartureTs] = useState("");
  const [originalEtaTs, setOriginalEtaTs] = useState("");
  const [actualDepartureTs, setActualDepartureTs] = useState("");
  const [latestEtaTs, setLatestEtaTs] = useState("");
  const [expectedUnloadMin, setExpectedUnloadMin] = useState("45");
  const [formError, setFormError] = useState("");

  const ORIGIN_NAMES = [
    "Jaipur Depot",
    "Manesar Plant",
    "Bhiwadi Packaging Works",
    "Kota Engineering Supplies",
    "Delhi Cold Storage",
  ];
  const ORIGIN_CITIES = ["Jaipur", "Manesar", "Bhiwadi", "Kota", "Delhi"];
  const CUSTOMER_NAMES = [
    "RajRetail Distribution",
    "IndustrialHub Jaipur",
    "CareSupply Rajasthan",
    "FreshBasket Jaipur",
    "BuildPro Rajasthan",
  ];
  const PRODUCT_CATEGORIES = [
    "General cargo",
    "FMCG",
    "Medical devices",
    "Textiles",
    "Dairy products",
    "Consumer electronics",
  ];

  // Only offer drivers/vehicles that are actually available to take a new load.
  const availableDrivers = drivers.filter((d) => d.driver_status === "ACTIVE");
  const availableVehicles = vehicles.filter((v) => v.active_flag);

  useEffect(() => {
    let mounted = true;
    setIdsError("");

    void getNextShipmentIds()
      .then((ids) => {
        if (!mounted) return;
        setShipmentId(ids.shipment_id);
        setOrderReference(ids.order_reference);
      })
      .catch(() => {
        if (!mounted) return;
        setIdsError("Shipment identifiers are unavailable right now. They will be assigned by the backend when the shipment is created.");
      });

    return () => {
      mounted = false;
    };
  }, []);

  const selectedDriver = availableDrivers.find((d) => d.driver_id === driverId);
  // Prefer vehicles that share the driver's carrier, but never leave the
  // dropdown completely empty because of that filter (e.g. driver has no
  // carrier on file, or no vehicle happens to share it) -- fall back to the
  // full available list so the field always has something to pick from. The
  // backend still enforces the same-carrier rule at submit time regardless.
  const carrierMatchedVehicles = selectedDriver?.carrier_id
    ? availableVehicles.filter((v) => v.carrier_id === selectedDriver.carrier_id)
    : [];
  const eligibleVehicles = carrierMatchedVehicles.length > 0 ? carrierMatchedVehicles : availableVehicles;

  function submit() {
    if (!destinationFacilityId || !originName || !originCity || !customerName || !driverId || !vehicleId) {
      setFormError("Destination, origin, customer, driver, and vehicle are required.");
      return;
    }
    if (!productCategory) {
      setFormError("Product category is required.");
      return;
    }
    if (!loadWeightKg || Number(loadWeightKg) <= 0) {
      setFormError("Load weight must be a positive number.");
      return;
    }
    if (!plannedDepartureTs) {
      setFormError("Planned departure is required.");
      return;
    }
    if (!originalEtaTs) {
      setFormError("Original ETA is required.");
      return;
    }
    if (!expectedUnloadMin || Number(expectedUnloadMin) <= 0) {
      setFormError("Expected unload must be a positive number.");
      return;
    }
    const driver = availableDrivers.find((d) => d.driver_id === driverId);
    if (!driver?.carrier_id) {
      setFormError("Selected driver has no carrier on file.");
      return;
    }
    setFormError("");
    onCreate({
      ...(shipmentId ? { shipment_id: shipmentId } : {}),
      ...(orderReference ? { order_reference: orderReference } : {}),
      carrier_id: driver.carrier_id,
      driver_id: driverId,
      vehicle_id: vehicleId,
      origin_name: originName,
      origin_city: originCity,
      destination_facility_id: destinationFacilityId,
      customer_name: customerName,
      product_category: productCategory,
      load_weight_kg: Number(loadWeightKg),
      ...(palletCount ? { pallet_count: Number(palletCount) } : {}),
      required_dock_type: requiredDockType,
      temperature_control_required: Boolean(Number(temperatureControlRequired)),
      priority_code: priorityCode,
      planned_departure_ts: new Date(plannedDepartureTs).toISOString(),
      actual_departure_ts: actualDepartureTs ? new Date(actualDepartureTs).toISOString() : undefined,
      original_eta_ts: new Date(originalEtaTs).toISOString(),
      latest_eta_ts: latestEtaTs ? new Date(latestEtaTs).toISOString() : undefined,
      expected_unload_min: Number(expectedUnloadMin),
      current_status: currentStatus,
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
          <Field label="Shipment ID">
            <input
              value={shipmentId}
              readOnly
              className="w-full rounded-xl border border-line bg-cloud/30 px-3 py-2 text-sm text-ink outline-none"
              placeholder={shipmentId ? "" : idsError ? "Assigned on submit" : "Loading…"}
            />
          </Field>
          <Field label="Order reference">
            <input
              value={orderReference}
              readOnly
              className="w-full rounded-xl border border-line bg-cloud/30 px-3 py-2 text-sm text-ink outline-none"
              placeholder={orderReference ? "" : idsError ? "Assigned on submit" : "Loading…"}
            />
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
          <Field label="Origin name">
            <select value={originName} onChange={(e) => setOriginName(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="">Select origin…</option>
              {ORIGIN_NAMES.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Origin city">
            <select value={originCity} onChange={(e) => setOriginCity(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="">Select city…</option>
              {ORIGIN_CITIES.map((city) => (
                <option key={city} value={city}>
                  {city}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Customer name">
            <select value={customerName} onChange={(e) => setCustomerName(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="">Select customer…</option>
              {CUSTOMER_NAMES.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Product category">
            <select value={productCategory} onChange={(e) => setProductCategory(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="">Select category…</option>
              {PRODUCT_CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Load weight (kg)">
            <input type="number" min={1} value={loadWeightKg} onChange={(e) => setLoadWeightKg(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" placeholder="12000" />
          </Field>
          <Field label="Pallet count">
            <input type="number" min={0} value={palletCount} onChange={(e) => setPalletCount(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" placeholder="0" />
          </Field>
          <Field label="Required dock type">
            <select value={requiredDockType} onChange={(e) => setRequiredDockType(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="ANY">Any</option>
              <option value="STANDARD">Standard</option>
              <option value="REEFER">Reefer</option>
              <option value="HEAVY">Heavy</option>
            </select>
          </Field>
          <Field label="Temperature control">
            <select value={temperatureControlRequired} onChange={(e) => setTemperatureControlRequired(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="0">No</option>
              <option value="1">Yes</option>
            </select>
          </Field>
          <Field label="Priority">
            <select value={priorityCode} onChange={(e) => setPriorityCode(e.target.value as "LOW" | "NORMAL" | "HIGH" | "CRITICAL")} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="LOW">Low</option>
              <option value="NORMAL">Normal</option>
              <option value="HIGH">High</option>
              <option value="CRITICAL">Critical</option>
            </select>
          </Field>
          <Field label="Current status">
            <select value={currentStatus} onChange={(e) => setCurrentStatus(e.target.value as "PLANNED" | "ASSIGNED" | "IN_TRANSIT" | "AT_GATE" | "WAITING" | "IN_DOCK" | "COMPLETED" | "CANCELLED")} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink">
              <option value="PLANNED">Planned</option>
              <option value="ASSIGNED">Assigned</option>
              <option value="IN_TRANSIT">In transit</option>
              <option value="AT_GATE">At gate</option>
              <option value="WAITING">Waiting</option>
              <option value="IN_DOCK">In dock</option>
              <option value="COMPLETED">Completed</option>
              <option value="CANCELLED">Cancelled</option>
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
            <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" disabled={!driverId}>
              <option value="">Select vehicle…</option>
              {eligibleVehicles.map((v) => (
                <option key={v.vehicle_id} value={v.vehicle_id}>
                  {v.vehicle_id} · {v.registration_number ?? "—"} · {v.vehicle_type_code ?? "—"}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Planned departure">
            <input type="datetime-local" value={plannedDepartureTs} onChange={(e) => setPlannedDepartureTs(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" />
          </Field>
          <Field label="Original ETA">
            <input type="datetime-local" value={originalEtaTs} onChange={(e) => setOriginalEtaTs(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" />
          </Field>
          <Field label="Actual departure">
            <input type="datetime-local" value={actualDepartureTs} onChange={(e) => setActualDepartureTs(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" />
          </Field>
          <Field label="Latest ETA">
            <input type="datetime-local" value={latestEtaTs} onChange={(e) => setLatestEtaTs(e.target.value)} className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink" />
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

function formatCheckinShipmentLabel(shipment: CheckInShipmentSummary) {
  const route = `${shipment.origin_city ?? shipment.origin_name ?? "Origin"} -> ${shipment.destination_facility_id ?? "Facility"}`;
  const assignment = `${shipment.driver_id ?? "No driver"} | ${shipment.registration_number ?? shipment.vehicle_id ?? "No vehicle"}`;
  const status = shipment.checkin?.arrival_status
    ? `${shipment.current_status ?? "UNKNOWN"} / ${shipment.checkin.arrival_status}`
    : (shipment.current_status ?? "UNKNOWN");
  return `${shipment.shipment_id} | ${shipment.order_reference ?? "—"} | ${route} | ${assignment} | Status: ${status}`;
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

  return (
    <div>
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
        <p className="mt-4 text-sm text-ink-soft">No compatible docks or slots for this shipment right now.</p>
      )}

      <div className="mt-5 flex flex-wrap gap-3">
        {docks.map(([dockCode, slots]) => (
          <div key={dockCode} className="w-full min-w-[180px] rounded-2xl border border-line p-3 sm:w-[calc(50%-0.375rem)] lg:w-[calc(25%-0.5625rem)]">
            <p className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink-soft">
              Dock {dockCode}
              {slots[0] && <span className="ml-1 font-medium normal-case text-mist">· {slots[0].dock_type}</span>}
            </p>
            <div className="flex flex-col gap-1.5">
              {slots.map((slot) => {
                const style = dockSlotStyle(slot.availability_status);
                const isSelected = slot.slot_id === selectedSlotId;
                const selectable = slot.availability_status === "AVAILABLE";
                return (
                  <div key={slot.slot_id} className="group relative">
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
                        {formatTime(slot.start)} – {formatTime(slot.end)}
                      </p>
                      <div className="mt-1.5">
                        <Badge status={slot.availability_status} />
                      </div>
                      {slot.occupant_shipment_id && (
                        <p className="mt-1.5 text-ink-soft">Booked by {slot.occupant_shipment_id}</p>
                      )}
                    </div>
                  </div>
                );
              })}
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

function CheckinPanel({
  color,
  record,
  timeline,
  busy,
  shipments,
  selectedShipment,
  assignedFacility,
  hasAssignedFacility,
  selectedShipmentId,
  onSelectShipment,
  onGateIn,
  onQueue,
  onDock,
  onComplete,
}: {
  color: string;
  record: CheckInRecord | null;
  timeline: Array<{ label: string; value: string; status: string }>;
  busy: string | null;
  shipments: CheckInShipmentSummary[];
  selectedShipment: CheckInShipmentSummary | null;
  assignedFacility: TmsFacility | null;
  hasAssignedFacility: boolean;
  selectedShipmentId: string;
  onSelectShipment: (shipmentId: string) => void;
  onGateIn: () => void;
  onQueue: () => void;
  onDock: () => void;
  onComplete: () => void;
}) {
  const canGateIn = Boolean(selectedShipmentId) && Boolean(selectedShipment?.can_gate_in);
  const canQueue = Boolean(selectedShipmentId) && Boolean(selectedShipment?.can_queue);
  const canDock = Boolean(selectedShipmentId) && Boolean(selectedShipment?.can_dock);
  const canComplete = Boolean(selectedShipmentId) && Boolean(selectedShipment?.can_complete);

  return (
    <div>
      <h2 className="text-lg font-extrabold text-ink">Gate & yard activity</h2>
      <p className="text-sm text-ink-soft">Real-time arrivals and yard movement.</p>
      <div className="mt-3 rounded-xl bg-cloud/60 px-3 py-3 text-xs text-ink-soft">
        Assigned warehouse:{" "}
        <span className="font-semibold text-ink">
          {assignedFacility
            ? `${assignedFacility.facility_name ?? assignedFacility.facility_id}${assignedFacility.city ? ` · ${assignedFacility.city}` : ""}`
            : hasAssignedFacility
            ? selectedShipment?.destination_facility_id ?? "Assigned facility"
            : "No warehouse assigned to this account"}
        </span>
      </div>
      <div className="mt-4 max-w-2xl">
        <Field label="Shipment">
          <select
            value={selectedShipmentId}
            onChange={(e) => onSelectShipment(e.target.value)}
            className="w-full rounded-xl border border-line px-3 py-2 text-sm text-ink outline-none focus:border-ink"
          >
            {shipments.length === 0 && <option value="">No active shipments</option>}
            {shipments.map((s) => (
              <option key={s.shipment_id} value={s.shipment_id}>
                {formatCheckinShipmentLabel(s)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-line p-4">
          <p className="text-sm font-bold text-ink">Shipment {record?.shipment_id ?? selectedShipmentId ?? "—"}</p>
          <p className="mt-1 text-xs text-ink-soft">Backend validated status only. React never reimplements the state machine.</p>
          {selectedShipment && (
            <div className="mt-3 rounded-xl bg-cloud/60 px-3 py-3 text-xs text-ink-soft">
              <p>
                {selectedShipment.origin_city ?? selectedShipment.origin_name ?? "Origin"} {"->"} {selectedShipment.destination_facility_id ?? "Facility"}
              </p>
              <p className="mt-1">
                Driver: {selectedShipment.driver_id ?? "—"}
                {selectedShipment.driver_name ? ` (${selectedShipment.driver_name})` : ""}
              </p>
              <p className="mt-1">
                Vehicle: {selectedShipment.registration_number ?? selectedShipment.vehicle_id ?? "—"}
              </p>
              <p className="mt-1">Shipment status: {selectedShipment.current_status ?? "UNKNOWN"}</p>
            </div>
          )}
          <div className="mt-4 grid gap-2">
            {timeline.length === 0 && <p className="text-sm text-ink-soft">Not checked in yet.</p>}
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
            <button onClick={onGateIn} disabled={Boolean(busy) || !canGateIn} className="rounded-xl px-4 py-2.5 text-sm font-bold text-white disabled:opacity-50" style={{ background: color }}>
              {busy ? <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> : null}
              Gate check-in
            </button>
            <button onClick={onQueue} disabled={Boolean(busy) || !canQueue} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink disabled:opacity-50">
              Queue update
            </button>
            <button onClick={onDock} disabled={Boolean(busy) || !canDock} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink disabled:opacity-50">
              Mark docked
            </button>
            <button onClick={onComplete} disabled={Boolean(busy) || !canComplete} className="rounded-xl border border-line px-4 py-2.5 text-sm font-bold text-ink disabled:opacity-50">
              Complete unload
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
