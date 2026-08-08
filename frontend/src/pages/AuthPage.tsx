import { useState } from "react";
import { useParams, Navigate, Link } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeft,
  Eye,
  EyeOff,
  Lock,
  Mail,
  User,
  CheckCircle2,
  Sparkles,
  ShieldCheck,
} from "lucide-react";
import { getService, type ServiceId } from "../data/services";
import { useAuth } from "../context/AuthContext";
import { useTransition } from "../context/TransitionContext";
import { cn } from "../lib/cn";

type Mode = "login" | "register";

export default function AuthPage() {
  const { serviceId } = useParams();
  const service = getService(serviceId);
  const { login } = useAuth();
  const { goToService } = useTransition();

  const [mode, setMode] = useState<Mode>("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);

  if (!service) return <Navigate to="/" replace />;

  const displayName = name.trim() || email.split("@")[0] || "Guest";

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || success) return;
    setSubmitting(true);
    window.setTimeout(() => {
      setSubmitting(false);
      setSuccess(true);
      login(service.id as ServiceId, displayName);
      window.setTimeout(() => {
        goToService(service.id as ServiceId);
      }, 700);
    }, 850);
  };

  return (
    <div className="mx-auto flex min-h-[calc(100svh-4rem)] max-w-[1400px] items-center justify-center px-4 py-10 sm:px-6">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-3xl border border-line bg-white shadow-pop md:grid-cols-5">
        {/* brand panel */}
        <div
          className="relative hidden flex-col justify-between overflow-hidden p-8 text-white md:col-span-2 md:flex"
          style={{ background: `linear-gradient(160deg, ${service.color}, #12121a)` }}
        >
          <div
            className="pointer-events-none absolute inset-0 opacity-25"
            style={{
              backgroundImage: "radial-gradient(circle, white 1px, transparent 1px)",
              backgroundSize: "16px 16px",
            }}
          />
          <Link to="/" className="relative z-10 flex items-center gap-1.5 text-sm font-semibold text-white/85 hover:text-white">
            <ArrowLeft className="h-4 w-4" />
            Back to portal
          </Link>

          <div className="relative z-10">
            <motion.span
              initial={{ scale: 0.7, opacity: 0, rotate: -8 }}
              animate={{ scale: 1, opacity: 1, rotate: 0 }}
              className="grid h-14 w-14 place-items-center rounded-2xl bg-white/15 backdrop-blur-sm"
            >
              <service.icon className="h-7 w-7" strokeWidth={2} />
            </motion.span>
            <h2 className="mt-5 text-2xl font-extrabold leading-tight">{service.name}</h2>
            <p className="mt-2 text-sm text-white/80">{service.tagline}</p>

            <div className="mt-6 flex flex-col gap-2.5">
              {service.highlights.map((h) => (
                <div key={h.label} className="flex items-center gap-2 text-xs font-medium text-white/85">
                  <h.icon className="h-3.5 w-3.5 shrink-0" />
                  {h.label}
                </div>
              ))}
            </div>
          </div>

          <div className="relative z-10 flex items-center gap-2 text-[11px] font-semibold text-white/70">
            <ShieldCheck className="h-4 w-4" />
            Isolated, per-service authentication
          </div>
        </div>

        {/* form panel */}
        <div className="col-span-3 flex flex-col p-6 sm:p-10">
          <Link
            to="/"
            className="mb-4 flex items-center gap-1.5 text-xs font-semibold text-ink-soft hover:text-ink md:hidden"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to portal
          </Link>

          <div className="mb-6 flex items-center gap-2 rounded-full bg-cloud p-1 text-sm font-bold">
            {(["login", "register"] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className="relative flex-1 rounded-full px-4 py-2 transition-colors"
              >
                {mode === m && (
                  <motion.span
                    layoutId="auth-tab-pill"
                    className="absolute inset-0 rounded-full bg-white shadow-sm"
                    transition={{ type: "spring", stiffness: 400, damping: 32 }}
                  />
                )}
                <span
                  className="relative z-10"
                  style={{ color: mode === m ? service.color : "var(--color-ink-soft)" }}
                >
                  {m === "login" ? "Sign in" : "Register"}
                </span>
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait">
            {success ? (
              <motion.div
                key="success"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                className="flex flex-1 flex-col items-center justify-center py-10 text-center"
              >
                <motion.span
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", stiffness: 260, damping: 16 }}
                  className="grid h-16 w-16 place-items-center rounded-full"
                  style={{ background: service.colorSoft }}
                >
                  <CheckCircle2 className="h-8 w-8" style={{ color: service.color }} />
                </motion.span>
                <p className="mt-4 text-lg font-bold text-ink">You're in, {displayName}!</p>
                <p className="mt-1 text-sm text-ink-soft">Pulling up {service.shortName}…</p>
              </motion.div>
            ) : (
              <motion.form
                key={mode}
                initial={{ opacity: 0, x: mode === "login" ? -16 : 16 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: mode === "login" ? 16 : -16 }}
                transition={{ duration: 0.22 }}
                onSubmit={handleSubmit}
                className="flex flex-1 flex-col"
              >
                <h1 className="text-2xl font-extrabold text-ink">
                  {mode === "login" ? "Welcome back" : "Create your account"}
                </h1>
                <p className="mt-1 text-sm text-ink-soft">
                  {mode === "login"
                    ? `Sign in to continue to ${service.shortName}.`
                    : `Register to get access to ${service.shortName}.`}
                </p>

                <div className="mt-6 flex flex-col gap-3.5">
                  {mode === "register" && (
                    <Field icon={User} label="Full name">
                      <input
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Ravi Kumar"
                        className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist"
                      />
                    </Field>
                  )}

                  <Field icon={Mail} label="Email">
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@setuhaul.com"
                      className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist"
                    />
                  </Field>

                  <Field icon={Lock} label="Password">
                    <input
                      type={showPassword ? "text" : "password"}
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="peer w-full bg-transparent text-sm font-medium text-ink outline-none placeholder:text-mist"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      className="text-mist hover:text-ink-soft"
                      tabIndex={-1}
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </Field>

                  {mode === "login" ? (
                    <div className="flex items-center justify-between text-xs font-semibold">
                      <label className="flex items-center gap-1.5 text-ink-soft">
                        <input type="checkbox" className="h-3.5 w-3.5 rounded accent-current" style={{ color: service.color }} />
                        Remember me
                      </label>
                      <button type="button" className="text-ink-soft hover:text-ink">
                        Forgot password?
                      </button>
                    </div>
                  ) : (
                    <label className="flex items-start gap-2 text-xs font-medium text-ink-soft">
                      <input
                        type="checkbox"
                        required
                        className="mt-0.5 h-3.5 w-3.5 rounded accent-current"
                        style={{ color: service.color }}
                      />
                      I agree to the operating terms for {service.shortName} access.
                    </label>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={submitting}
                  className={cn(
                    "mt-6 flex items-center justify-center gap-2 rounded-2xl py-3.5 text-sm font-bold text-white shadow-soft transition-all",
                    submitting ? "opacity-80" : "hover:-translate-y-0.5 hover:shadow-pop"
                  )}
                  style={{ background: service.color }}
                >
                  {submitting ? (
                    <motion.span
                      animate={{ rotate: 360 }}
                      transition={{ repeat: Infinity, duration: 0.8, ease: "linear" }}
                      className="h-4 w-4 rounded-full border-2 border-white/40 border-t-white"
                    />
                  ) : (
                    <Sparkles className="h-4 w-4" />
                  )}
                  {mode === "login" ? "Sign in" : "Create account"}
                </button>

                <p className="mt-5 text-center text-xs font-medium text-ink-soft">
                  {mode === "login" ? "New to " + service.shortName + "? " : "Already have access? "}
                  <button
                    type="button"
                    onClick={() => setMode(mode === "login" ? "register" : "login")}
                    className="font-bold"
                    style={{ color: service.color }}
                  >
                    {mode === "login" ? "Create an account" : "Sign in instead"}
                  </button>
                </p>
              </motion.form>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function Field({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof Mail;
  label: string;
  children: React.ReactNode;
}) {
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
