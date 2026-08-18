import { useEffect, useState, type FormEvent } from "react";
import { Building2, CheckCircle2, AlertCircle } from "lucide-react";
import type { FacilityStaffAssignment, TmsFacility } from "../../types/api";

export default function FacilitySetupForm({
  color,
  listFacilities,
  registerFacility,
  onComplete,
}: {
  color: string;
  listFacilities: () => Promise<TmsFacility[]>;
  registerFacility: (facilityId: string) => Promise<FacilityStaffAssignment>;
  onComplete: (assignment: FacilityStaffAssignment) => void;
}) {
  const [facilities, setFacilities] = useState<TmsFacility[]>([]);
  const [facilityId, setFacilityId] = useState("");
  const [facilitiesLoading, setFacilitiesLoading] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const list = await listFacilities();
        setFacilities(list);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load facilities from Supabase.");
      } finally {
        setFacilitiesLoading(false);
      }
    })();
  }, [listFacilities]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!facilityId) {
      setError("Select the warehouse facility you're registering for.");
      return;
    }
    setError("");
    setIsLoading(true);
    try {
      const assignment = await registerFacility(facilityId);
      onComplete(assignment);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save your facility assignment.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-lg">
      <div className="rounded-2xl border border-line bg-cloud/60 p-4 text-sm text-ink-soft">
        You're signed in, but you haven't registered a warehouse facility yet. Pick the one you work at -- you'll
        only see shipments allotted to that facility, not other facilities'.
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-3.5">
        <label className="block">
          <span className="mb-1.5 block text-xs font-bold text-ink-soft">Warehouse facility</span>
          <span className="flex items-center gap-2.5 rounded-xl border border-line bg-cloud/60 px-3.5 py-3 transition focus-within:border-ink/30 focus-within:bg-white">
            <Building2 className="h-4 w-4 shrink-0 text-mist" />
            <select
              value={facilityId}
              onChange={(e) => setFacilityId(e.target.value)}
              disabled={facilitiesLoading}
              className="peer w-full bg-transparent text-sm font-medium text-ink outline-none disabled:opacity-60"
            >
              <option value="" disabled>
                {facilitiesLoading ? "Loading facilities..." : "Select your facility"}
              </option>
              {facilities.map((facility) => (
                <option key={facility.facility_id} value={facility.facility_id}>
                  {facility.facility_name ?? facility.facility_id}
                </option>
              ))}
            </select>
          </span>
        </label>

        <button
          type="submit"
          disabled={isLoading}
          className="mt-2 flex items-center justify-center gap-2 rounded-2xl py-3.5 text-sm font-bold text-white shadow-soft transition-all disabled:opacity-70"
          style={{ background: color }}
        >
          <CheckCircle2 className="h-4 w-4" />
          {isLoading ? "Saving to Supabase..." : "Save facility & enter portal"}
        </button>
      </form>
    </div>
  );
}
