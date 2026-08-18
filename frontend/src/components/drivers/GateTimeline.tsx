import { MapPin, Navigation, Clock, FileCheck, Truck } from "lucide-react";
import type { ArrivalUpdateChoice, DriverFacilityCheckinSummary, DriverSnapshot } from "../../types/driverChat";

const stages = [
  { key: "in_transit", label: "In transit", icon: Navigation },
  { key: "at_gate", label: "Gate in", icon: MapPin },
  { key: "in_yard", label: "Yard parked", icon: Clock },
  { key: "at_dock", label: "Unloading", icon: Truck },
  { key: "completed", label: "Trip done", icon: FileCheck },
];

// The real schema tracks discrete stage timestamps on facility_checkins
// rather than one "arrival_status" enum -- derive the current stage from
// which timestamps are populated.
function deriveStage(checkin?: DriverFacilityCheckinSummary | null): string {
  if (!checkin || !checkin.gate_in_ts) return "in_transit";
  if (!checkin.unload_end_ts && !checkin.dock_in_ts && !checkin.yard_queue_enter_ts) return "at_gate";
  if (!checkin.dock_in_ts) return "in_yard";
  if (!checkin.unload_end_ts) return "at_dock";
  return "completed";
}

export default function GateTimeline({
  snapshot,
  color,
  onUpdateCheckin,
  disabled,
}: {
  snapshot: DriverSnapshot;
  color: string;
  onUpdateCheckin: (status: ArrivalUpdateChoice) => void;
  disabled?: boolean;
}) {
  const { checkin, facility } = snapshot;
  const currentStatus = deriveStage(checkin);
  const currentIndex = stages.findIndex((s) => s.key === currentStatus);

  const nextAction: { label: string; value: ArrivalUpdateChoice } | null =
    currentStatus === "in_transit"
      ? { label: "Tap when arrived at gate", value: "arrived_gate" }
      : currentStatus === "at_gate"
      ? { label: "Tap when parked in yard", value: "waiting_yard" }
      : currentStatus === "in_yard"
      ? { label: "Tap when backed into dock door", value: "docked" }
      : currentStatus === "at_dock"
      ? { label: "Tap when unloaded & POD signed", value: "completed" }
      : null;

  return (
    <div className="rounded-2xl border border-line bg-white p-4 sm:p-5">
      <div className="flex flex-col justify-between gap-3 border-b border-line pb-3 sm:flex-row sm:items-center">
        <div>
          <h2 className="text-base font-extrabold text-ink sm:text-lg">Gate &amp; unloading progress</h2>
          <p className="mt-0.5 text-xs text-mist">
            Destination hub: <strong className="text-ink-soft">{facility?.facility_name || "Destination facility"}</strong>
          </p>
        </div>
        {nextAction && (
          <button
            onClick={() => onUpdateCheckin(nextAction.value)}
            disabled={disabled}
            className="flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-black text-white shadow-soft transition active:scale-95 disabled:opacity-50 sm:text-sm"
            style={{ background: color }}
          >
            {nextAction.label}
          </button>
        )}
      </div>

      <div className="overflow-x-auto py-3">
        <div className="grid min-w-[340px] grid-cols-5 gap-1.5 sm:gap-3">
          {stages.map((stg, idx) => {
            const isDone = idx < currentIndex;
            const isCurrent = idx === currentIndex;
            const Icon = stg.icon;
            return (
              <div key={stg.key} className="flex flex-col items-center text-center">
                <div
                  className={`flex h-10 w-10 items-center justify-center rounded-2xl font-bold transition-all sm:h-12 sm:w-12 ${
                    isDone ? "bg-emerald-500 text-white" : "border border-line bg-cloud text-mist"
                  }`}
                  style={isCurrent ? { background: color, color: "white" } : undefined}
                >
                  <Icon className="h-5 w-5 sm:h-6 sm:w-6" />
                </div>
                <span className="mt-2 text-xs font-bold" style={{ color: isCurrent ? color : isDone ? "#059669" : "var(--color-mist)" }}>
                  {stg.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex items-center justify-between rounded-xl border border-line bg-cloud/60 px-3.5 py-3 text-xs text-ink-soft">
        <span>
          Queue status: <strong className="font-semibold text-ink">{checkin?.queue_state?.replace(/_/g, " ") || "En route on highway. Gate entry open."}</strong>
        </span>
      </div>
    </div>
  );
}
