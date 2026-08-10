import { useEffect, useMemo, useState, type FormEvent } from "react";
import { User, Phone, CreditCard, Building2, MapPin, CheckCircle2, AlertCircle } from "lucide-react";
import { completeDriverProfile, getDriverOnboardingOptions } from "../../services/driverChatApi";
import type { DriverOnboardingOptions, DriverProfile } from "../../types/driverChat";

const CARRIER_ID_PATTERN = /^CAR\d{3}$/;
const LICENCE_PATTERN = /^[A-Z]{2}[0-9]{2}[- ]?[0-9]{4}[- ]?[0-9]{6,7}$/;

export default function ProfileSetupForm({ color, onComplete }: { color: string; onComplete: (driver: DriverProfile) => void }) {
  const [driverName, setDriverName] = useState("");
  const [phoneDigits, setPhoneDigits] = useState("");
  const [licenceNumber, setLicenceNumber] = useState("");
  const [carrierId, setCarrierId] = useState("");
  const [homeBaseCity, setHomeBaseCity] = useState("");
  const [options, setOptions] = useState<DriverOnboardingOptions>({ carrier_ids: [], home_base_cities: [] });
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        setLoadingOptions(true);
        setOptions(await getDriverOnboardingOptions());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load onboarding options.");
      } finally {
        setLoadingOptions(false);
      }
    })();
  }, []);

  const carrierIdError = useMemo(() => {
    if (!carrierId.trim()) return "";
    return CARRIER_ID_PATTERN.test(carrierId.trim()) ? "" : "Carrier ID must look like CAR001.";
  }, [carrierId]);

  const nameError = useMemo(() => {
    if (!driverName.trim()) return "";
    return /^[A-Za-z ]{1,40}$/.test(driverName.trim()) ? "" : "Name must contain only letters and spaces, up to 40 characters.";
  }, [driverName]);

  const phoneError = useMemo(() => {
    if (!phoneDigits.trim()) return "";
    return /^[6-9][0-9]{9}$/.test(phoneDigits.trim()) ? "" : "Enter a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9.";
  }, [phoneDigits]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (
      !driverName.trim() ||
      !phoneDigits.trim() ||
      !licenceNumber.trim() ||
      !carrierId.trim() ||
      !homeBaseCity.trim()
    ) {
      setError("All fields are mandatory to register your driver profile.");
      return;
    }
    if (!/^[A-Za-z ]{1,40}$/.test(driverName.trim())) {
      setError("Name must contain only letters and spaces, up to 40 characters.");
      return;
    }
    if (!/^[6-9][0-9]{9}$/.test(phoneDigits.trim())) {
      setError("Enter a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9.");
      return;
    }
    if (!CARRIER_ID_PATTERN.test(carrierId.trim())) {
      setError("Carrier ID must match the CAR001 format.");
      return;
    }
    setError("");
    setIsLoading(true);
    try {
      const driver = await completeDriverProfile({
        driver_name: driverName.trim(),
        phone: phoneDigits.trim(),
        licence_number: licenceNumber.trim(),
        carrier_id: carrierId.trim(),
        home_base_city: homeBaseCity.trim(),
      });
      onComplete(driver);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save your profile to Supabase.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-lg">
      <div className="rounded-2xl border border-line bg-cloud/60 p-4 text-sm text-ink-soft">
        You're signed in, but there's no driver profile on file yet. Complete these details once to register in
        the Supabase database.
      </div>

      {error && (
        <div className="mt-4 flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-3.5">
        <div className="rounded-xl border border-dashed border-line bg-cloud/40 px-3.5 py-3 text-xs font-semibold text-ink-soft">
          Your driver ID will be assigned automatically after saving and will follow the next available `DRV###`
          sequence from the database.
        </div>
        <Field icon={User} label="Full name">
          <input
            value={driverName}
            onChange={(e) => setDriverName(e.target.value.replace(/[^A-Za-z ]/g, "").slice(0, 40))}
            maxLength={40}
            placeholder="Ravi Kumar"
            className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist"
          />
        </Field>
        <div className="grid gap-3.5 sm:grid-cols-2">
          <Field icon={Phone} label="Phone number">
            <div className="flex w-full items-center gap-2">
              <span className="text-sm font-bold text-ink-soft">+91-</span>
              <input
                value={phoneDigits}
                onChange={(e) => setPhoneDigits(e.target.value.replace(/\D/g, "").slice(0, 10))}
                inputMode="numeric"
                maxLength={10}
                pattern="[6-9][0-9]{9}"
                placeholder="9876543210"
                className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist"
              />
            </div>
          </Field>
          <Field icon={CreditCard} label="Driving licence number">
            <input
              value={licenceNumber}
              onChange={(e) => setLicenceNumber(e.target.value.toUpperCase().slice(0, 16))}
              maxLength={16}
              pattern={LICENCE_PATTERN.source}
              placeholder="RJ14 2024 1234567"
              className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist"
            />
          </Field>
        </div>
        <Field icon={Building2} label="Carrier ID">
          <select
            value={carrierId}
            onChange={(e) => setCarrierId(e.target.value.toUpperCase())}
            className="peer w-full bg-transparent text-sm font-medium text-ink outline-none"
            disabled={loadingOptions}
          >
            <option value="">{loadingOptions ? "Loading carriers..." : "Select a carrier"}</option>
            {options.carrier_ids.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </Field>
        <Field icon={MapPin} label="Home base city">
          <select
            value={homeBaseCity}
            onChange={(e) => setHomeBaseCity(e.target.value)}
            className="peer w-full bg-transparent text-sm font-medium text-ink outline-none"
            disabled={loadingOptions}
          >
            <option value="">{loadingOptions ? "Loading cities..." : "Select a city"}</option>
            {options.home_base_cities.map((city) => (
              <option key={city} value={city}>{city}</option>
            ))}
          </select>
        </Field>

        {carrierIdError && <p className="text-xs font-semibold text-rose-600">{carrierIdError}</p>}
        {nameError && <p className="text-xs font-semibold text-rose-600">{nameError}</p>}
        {phoneError && <p className="text-xs font-semibold text-rose-600">{phoneError}</p>}

        <button
          type="submit"
          disabled={isLoading || loadingOptions}
          className="mt-2 flex items-center justify-center gap-2 rounded-2xl py-3.5 text-sm font-bold text-white shadow-soft transition-all disabled:opacity-70"
          style={{ background: color }}
        >
          <CheckCircle2 className="h-4 w-4" />
          {isLoading ? "Saving to Supabase..." : "Save profile & enter portal"}
        </button>
      </form>
    </div>
  );
}

function Field({ icon: Icon, label, children }: { icon: typeof User; label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-bold text-ink-soft">{label}</span>
      <span className="flex items-center gap-2.5 rounded-xl border border-line bg-cloud/60 px-3.5 py-3 transition focus-within:border-ink/30 focus-within:bg-white">
        <Icon className="h-4 w-4 shrink-0 text-mist" />
        {children}
      </span>
    </label>
  );
}
