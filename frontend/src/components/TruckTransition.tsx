import { AnimatePresence, motion } from "framer-motion";
import { Truck } from "lucide-react";
import { useTransition, activeServiceDef } from "../context/TransitionContext";

export default function TruckTransition() {
  const { active, service } = useTransition();
  const def = activeServiceDef(service);

  return (
    <AnimatePresence>
      {active && def && (
        <motion.div
          key="truck-overlay"
          className="fixed inset-0 z-[100] flex flex-col items-center justify-center overflow-hidden"
          style={{ background: def.colorSoft }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 0.25 } }}
          transition={{ duration: 0.3 }}
        >
          <div
            className="pointer-events-none absolute inset-0 opacity-30"
            style={{
              background: `radial-gradient(600px 300px at 50% 40%, ${def.color}33, transparent 70%)`,
            }}
          />

          <motion.div
            className="mb-10 text-sm font-semibold tracking-wide uppercase"
            style={{ color: def.color }}
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
          >
            {def.shortName} · secure connection
          </motion.div>

          <div className="relative h-40 w-full max-w-3xl">
            {/* speed lines */}
            <motion.div
              className="absolute inset-x-0 top-1/2 -translate-y-1/2 flex flex-col gap-3"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.3 }}
            >
              {[0, 1, 2].map((row) => (
                <div key={row} className="h-[3px] w-full overflow-hidden rounded-full">
                  <motion.div
                    className="h-full rounded-full"
                    style={{ background: `${def.color}30`, width: "40%" }}
                    animate={{ x: ["-40%", "220%"] }}
                    transition={{
                      duration: 0.9 - row * 0.12,
                      repeat: Infinity,
                      ease: "linear",
                      delay: row * 0.08,
                    }}
                  />
                </div>
              ))}
            </motion.div>

            {/* truck */}
            <motion.div
              className="absolute top-1/2 -translate-y-[70%]"
              initial={{ left: "-15%" }}
              animate={{ left: "108%" }}
              transition={{ duration: 1.45, ease: [0.45, 0, 0.55, 1] }}
            >
              <motion.div
                animate={{ y: [0, -5, 0] }}
                transition={{ duration: 0.35, repeat: Infinity, ease: "easeInOut" }}
                className="drop-shadow-[0_18px_20px_rgba(0,0,0,0.18)]"
              >
                <Truck className="h-16 w-16" style={{ color: def.color }} strokeWidth={1.75} />
              </motion.div>
            </motion.div>

            {/* road */}
            <div className="absolute bottom-6 left-0 right-0 h-[3px] rounded-full bg-black/10" />
            <div className="road-strip absolute bottom-6 left-0 right-0 h-[3px] animate-drift opacity-70" />
          </div>

          <motion.p
            className="mt-8 text-base font-medium text-ink"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.35 }}
          >
            {def.roadCaption}
          </motion.p>

          <div className="mt-6 h-1 w-48 overflow-hidden rounded-full bg-black/10">
            <motion.div
              className="h-full rounded-full"
              style={{ background: def.color }}
              initial={{ width: "0%" }}
              animate={{ width: "100%" }}
              transition={{ duration: 1.4, ease: "linear" }}
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
