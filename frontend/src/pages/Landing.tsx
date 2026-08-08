import { motion } from "framer-motion";
import { ArrowRight, Route, Warehouse, ScanLine, MessagesSquare } from "lucide-react";
import { services } from "../data/services";
import { useTransition } from "../context/TransitionContext";
import ServiceCard from "../components/ServiceCard";
import HeroIllustration from "../components/HeroIllustration";

const steps = [
  { icon: Route, title: "Plan the load", text: "TMS assigns driver, vehicle & destination." },
  { icon: Warehouse, title: "Book the dock", text: "WMS reserves a feasible receiving slot." },
  { icon: ScanLine, title: "Check in", text: "Gate scans arrival, yard status updates live." },
  { icon: MessagesSquare, title: "Handle exceptions", text: "Driver's Portal keeps every delay on record." },
];

export default function Landing() {
  const { goToService } = useTransition();
  const driversService = services.find((s) => s.id === "drivers")!;
  const others = services.filter((s) => s.id !== "drivers");

  return (
    <div className="mx-auto max-w-[1400px] px-4 pb-24 pt-10 sm:px-6 sm:pt-14">
      <section className="grid items-center gap-10 lg:grid-cols-2 lg:gap-14">
        <div>
          <motion.span
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="inline-flex items-center gap-2 rounded-full border border-line bg-white px-3.5 py-1.5 text-xs font-bold text-ink-soft shadow-sm"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            340 loads in motion right now
          </motion.span>

          <motion.h1
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.06 }}
            className="mt-5 text-4xl font-extrabold leading-[1.08] tracking-tight text-ink sm:text-5xl"
          >
            One portal to run
            <br />
            <span className="brand-gradient-text">every mile of freight.</span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.12 }}
            className="mt-4 max-w-md text-[15px] leading-relaxed text-ink-soft"
          >
            SetuHaul connects TMS, WMS, gate check-in and driver conversations into a single,
            secure workspace — so a missed appointment never turns into a wave of delays.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.18 }}
            className="mt-7 flex flex-wrap items-center gap-3"
          >
            <button
              onClick={() => goToService("drivers")}
              className="flex items-center gap-2 rounded-full px-5 py-3 text-sm font-bold text-white shadow-pop transition-transform hover:-translate-y-0.5"
              style={{ background: driversService.color }}
            >
              Enter Driver's Portal
              <ArrowRight className="h-4 w-4" />
            </button>
            <button
              onClick={() => goToService("tms")}
              className="flex items-center gap-2 rounded-full border border-line bg-white px-5 py-3 text-sm font-bold text-ink transition hover:border-tms/40 hover:text-tms"
            >
              Explore TMS
            </button>
          </motion.div>

          <div className="mt-8 flex items-center gap-5 text-xs font-semibold text-mist">
            <span>32 dock doors</span>
            <span className="h-1 w-1 rounded-full bg-mist" />
            <span>6 facilities</span>
            <span className="h-1 w-1 rounded-full bg-mist" />
            <span>Secure per-service sign-in</span>
          </div>
        </div>

        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.1 }}
        >
          <HeroIllustration />
        </motion.div>
      </section>

      <section className="mt-16">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-xl font-extrabold text-ink">How a load moves through SetuHaul</h2>
            <p className="mt-1 text-sm text-ink-soft">Four systems, one connected story.</p>
          </div>
        </div>
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map((step, i) => (
            <motion.div
              key={step.title}
              initial={{ opacity: 0, y: 10 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.06 }}
              className="rounded-2xl border border-line bg-white p-4"
            >
              <div className="flex items-center gap-2">
                <span className="grid h-8 w-8 place-items-center rounded-xl bg-cloud text-ink-soft">
                  <step.icon className="h-4 w-4" />
                </span>
                <span className="text-xs font-bold text-mist">Step {i + 1}</span>
              </div>
              <h3 className="mt-3 text-sm font-bold text-ink">{step.title}</h3>
              <p className="mt-1 text-xs leading-relaxed text-ink-soft">{step.text}</p>
            </motion.div>
          ))}
        </div>
      </section>

      <section className="mt-16">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-xl font-extrabold text-ink">Choose a workspace</h2>
            <p className="mt-1 text-sm text-ink-soft">
              Every workspace is authenticated independently — sign in once per service.
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
          <ServiceCard service={driversService} featured />
          {others.map((s) => (
            <ServiceCard key={s.id} service={s} />
          ))}
        </div>
      </section>
    </div>
  );
}
