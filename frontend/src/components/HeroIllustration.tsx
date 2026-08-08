import { motion } from "framer-motion";
import { Truck, Warehouse, ScanLine, UserRound, MapPin } from "lucide-react";

export default function HeroIllustration() {
  return (
    <div className="relative h-72 w-full overflow-hidden rounded-3xl border border-line bg-gradient-to-br from-white to-cloud shadow-soft sm:h-80">
      <div
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage:
            "radial-gradient(circle, #12121a 1px, transparent 1px)",
          backgroundSize: "18px 18px",
        }}
      />

      {/* floating waypoint icons */}
      <motion.div
        className="absolute left-[10%] top-10 flex flex-col items-center gap-1"
        animate={{ y: [0, -8, 0] }}
        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
      >
        <span className="grid h-11 w-11 place-items-center rounded-2xl bg-wms text-white shadow-soft">
          <Warehouse className="h-5 w-5" />
        </span>
        <span className="text-[10px] font-bold text-ink-soft">WMS</span>
      </motion.div>

      <motion.div
        className="absolute right-[14%] top-14 flex flex-col items-center gap-1"
        animate={{ y: [0, -8, 0] }}
        transition={{ duration: 4.5, repeat: Infinity, ease: "easeInOut", delay: 0.6 }}
      >
        <span className="grid h-11 w-11 place-items-center rounded-2xl bg-checkin text-white shadow-soft">
          <ScanLine className="h-5 w-5" />
        </span>
        <span className="text-[10px] font-bold text-ink-soft">Check-in</span>
      </motion.div>

      <motion.div
        className="absolute left-[42%] top-6 flex flex-col items-center gap-1"
        animate={{ y: [0, -6, 0] }}
        transition={{ duration: 3.6, repeat: Infinity, ease: "easeInOut", delay: 0.3 }}
      >
        <span className="grid h-11 w-11 place-items-center rounded-2xl bg-tms text-white shadow-soft">
          <Truck className="h-5 w-5" />
        </span>
        <span className="text-[10px] font-bold text-ink-soft">TMS</span>
      </motion.div>

      <motion.div
        className="absolute bottom-16 right-[8%] flex flex-col items-center gap-1"
        animate={{ y: [0, -8, 0] }}
        transition={{ duration: 4.2, repeat: Infinity, ease: "easeInOut", delay: 0.9 }}
      >
        <span className="grid h-11 w-11 place-items-center rounded-2xl bg-drivers text-white shadow-soft">
          <UserRound className="h-5 w-5" />
        </span>
        <span className="text-[10px] font-bold text-ink-soft">Drivers</span>
      </motion.div>

      {/* road */}
      <div className="absolute inset-x-0 bottom-10 h-10">
        <div className="absolute inset-x-6 top-1/2 h-3 -translate-y-1/2 rounded-full bg-ink/85" />
        <div className="road-strip absolute inset-x-6 top-1/2 h-3 -translate-y-1/2 animate-drift opacity-90" />

        <motion.div
          className="absolute top-1/2 -translate-y-[68%]"
          animate={{ left: ["4%", "82%", "4%"] }}
          transition={{ duration: 9, repeat: Infinity, ease: "easeInOut" }}
        >
          <motion.div animate={{ y: [0, -3, 0] }} transition={{ duration: 0.4, repeat: Infinity }}>
            <Truck
              className="h-9 w-9 -scale-x-100 text-ink drop-shadow-[0_10px_10px_rgba(0,0,0,0.15)]"
              strokeWidth={1.75}
            />
          </motion.div>
        </motion.div>
      </div>

      <div className="absolute bottom-4 left-6 flex items-center gap-1.5 text-[11px] font-semibold text-mist">
        <MapPin className="h-3.5 w-3.5" />
        Origin → Gate → Yard → Dock → Delivered
      </div>
    </div>
  );
}
