import { useState, type FormEvent } from "react";
import { User, Phone, CreditCard, Building2, MapPin, CheckCircle2, AlertCircle } from "lucide-react";
import { completeDriverProfile } from "../../services/driverChatApi";
import type { DriverProfile } from "../../types/driverChat";

export default function ProfileSetupForm({ color, onComplete }: { color: string; onComplete: (driver: DriverProfile) => void }) {
  const [driverName, setDriverName] = useState("");
  const [phone, setPhone] = useState("");
  const [licenceNumber, setLicenceNumber] = useState("");
  const [carrierName, setCarrierName] = useState("");
  const [homeBaseCity, setHomeBaseCity] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!driverName.trim() || !phone.trim() || !licenceNumber.trim() || !carrierName.trim() || !homeBaseCity.trim()) {
      setError("All fields are mandatory to register your driver profile.");
      return;
    }
    setError("");
    setIsLoading(true);
    try {
      const driver = await completeDriverProfile({
        driver_name: driverName.trim(),
        phone: phone.trim(),
        licence_number: licenceNumber.trim(),
        carrier_name: carrierName.trim(),
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
        <Field icon={User} label="Full name">
          <input value={driverName} onChange={(e) => setDriverName(e.target.value)} placeholder="Ravi Kumar" className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist" />
        </Field>
        <div className="grid gap-3.5 sm:grid-cols-2">
          <Field icon={Phone} label="Phone number">
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist" />
          </Field>
          <Field icon={CreditCard} label="Driving licence number">
            <input value={licenceNumber} onChange={(e) => setLicenceNumber(e.target.value)} placeholder="DL-1420240011" className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist" />
          </Field>
        </div>
        <Field icon={Building2} label="Carrier / fleet">
          <input value={carrierName} onChange={(e) => setCarrierName(e.target.value)} placeholder="Delhi Express Logistics" className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist" />
        </Field>
        <Field icon={MapPin} label="Home base city">
          <input value={homeBaseCity} onChange={(e) => setHomeBaseCity(e.target.value)} placeholder="Jaipur" className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist" />
        </Field>

        <button
          type="submit"
          disabled={isLoading}
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
