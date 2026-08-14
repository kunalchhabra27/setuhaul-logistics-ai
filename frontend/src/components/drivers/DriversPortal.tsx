import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { MessageCircle, X } from "lucide-react";
import { ApiClientError } from "../../services/api";
import {
  confirmDockSlot,
  escalateDriverException,
  getDriverSnapshot,
  getMyDriverProfile,
  holdDockSlot,
  requestDriverDockSlotChange,
  sendDriverChatMessage,
  sendDriverVoiceMessage,
  updateDriverCheckin,
} from "../../services/driverChatApi";
import type { ArrivalUpdateChoice, DriverProfile, DriverSnapshot } from "../../types/driverChat";
import { getService } from "../../data/services";
import { SkeletonCard } from "../Skeleton";
import { ErrorBanner } from "../ErrorBanner";
import { useAsyncResource } from "../../lib/useAsyncResource";
import ProfileSetupForm from "./ProfileSetupForm";
import ContextBar from "./ContextBar";
import AppointmentBanner from "./AppointmentBanner";
import GateTimeline from "./GateTimeline";
import ChatPanel from "./ChatPanel";
import DockSlotBoard from "./DockSlotBoard";

const driversServiceDef = getService("drivers")!;

// Module-level (stable-reference) predicate so useAsyncResource's internal
// `run` callback keeps a stable identity across renders -- refreshSnapshot's
// own useCallback and the 8s-poll effect below both depend on that identity
// staying put, the same way they depended on refreshSnapshot's own [] deps
// before this change.
const isSnapshotEmpty = (data: DriverSnapshot) => !data.shipment;

export default function DriversPortal({ color }: { color: string }) {
  const [driver, setDriver] = useState<DriverProfile | null>(null);
  const [needsProfile, setNeedsProfile] = useState(false);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);
  const snapshotResource = useAsyncResource<DriverSnapshot>({ isEmpty: isSnapshotEmpty });
  const [isSending, setIsSending] = useState(false);
  const [toast, setToast] = useState<{ text: string; tone: "success" | "error" | "info" } | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatExpanded, setChatExpanded] = useState(false);
  const [hasUnread, setHasUnread] = useState(false);
  const lastSeenMessageId = useRef<string | null>(null);

  const showToast = (text: string, tone: "success" | "error" | "info" = "info") => {
    setToast({ text, tone });
    setTimeout(() => setToast(null), 120_000);
  };

  const snapshotInFlight = useRef(false);

  // Mirrors snapshotResource.data one render behind, purely so the fetcher
  // below (which runs *before* the resource's own state updates) can see
  // "what we were showing right before this fetch" without React's setState
  // updater-function form (useAsyncResource's run() doesn't expose one).
  const prevSnapshotRef = useRef<DriverSnapshot | null>(null);
  useEffect(() => {
    prevSnapshotRef.current = snapshotResource.data;
  }, [snapshotResource.data]);

  const refreshSnapshot = useCallback(async () => {
    // The backend's /snapshot read is several sequential Supabase round
    // trips deep (shipment, vehicle, facility, docks, appointment,
    // checkin, exception, chat messages, dock-slot feasibility) and can
    // legitimately take longer than this poll's own 8s interval -- without
    // this guard, setInterval fires again before the previous fetch
    // resolves, so overlapping polls pile up on the single backend worker
    // and can starve a driver's actual chat message behind a growing queue
    // of stale background polls (this is what made the chatbot look
    // completely unresponsive: the message was queued behind several
    // already-overlapping snapshot polls, not failing to parse the query).
    if (snapshotInFlight.current) return;
    snapshotInFlight.current = true;
    try {
      await snapshotResource.run(async () => {
        const data = await getDriverSnapshot();
        setDriver(data.driver);
        // With no shipment assigned, chat replies are ephemeral (no
        // chat_threads row exists to persist to or reload from -- see
        // service.py's no-shipment branch). A background poll's snapshot
        // always comes back with an empty chat_messages list in that case,
        // so applying it verbatim would immediately wipe out whatever the
        // driver just saw the assistant say. Only let a poll clear/replace
        // the visible messages once a real, persisted thread exists
        // (i.e. a shipment is assigned) or the poll actually has content.
        const prev = prevSnapshotRef.current;
        if (data.chat_messages.length === 0 && !data.shipment && prev?.chat_messages?.length) {
          return { ...data, chat_messages: prev.chat_messages };
        }
        return data;
      });
    } finally {
      snapshotInFlight.current = false;
    }
  }, [snapshotResource.run]);

  // The single entry point for "resolve who this driver is and what they're
  // hauling" -- used on mount and by the profile-fetch error's Retry button.
  // profileLoading/profileError/needsProfile only ever get their *final*
  // value once this whole chain has actually settled (success, 404, or a
  // real error), never optimistically, so the render below can never see
  // "not loading" while the profile fetch is still genuinely unresolved.
  const loadInitial = useCallback(async () => {
    setProfileLoading(true);
    setProfileError(null);
    try {
      const profile = await getMyDriverProfile();
      setDriver(profile);
      setNeedsProfile(false);
      await refreshSnapshot();
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 404) {
        setNeedsProfile(true);
      } else {
        setProfileError(err instanceof Error ? err.message : "Failed to load your driver profile.");
      }
    } finally {
      setProfileLoading(false);
    }
  }, [refreshSnapshot]);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    if (!driver) return;
    const interval = setInterval(() => void refreshSnapshot(), 8000);
    return () => clearInterval(interval);
    // driver is refetched (new object reference) on every poll -- depend on
    // presence only, so the interval ticks steadily every 8s instead of
    // being torn down and rebuilt on every single successful poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(driver), refreshSnapshot]);

  // Flag a new agent reply while the chat widget is closed, and clear it once opened.
  useEffect(() => {
    const messages = snapshotResource.data?.chat_messages ?? [];
    const last = messages[messages.length - 1];
    if (!last) return;
    if (chatOpen) {
      lastSeenMessageId.current = last.chat_message_id;
      setHasUnread(false);
      return;
    }
    if (last.sender_type === "AGENT" && last.chat_message_id !== lastSeenMessageId.current) {
      lastSeenMessageId.current = last.chat_message_id;
      setHasUnread(true);
    }
  }, [snapshotResource.data?.chat_messages, chatOpen]);

  const handleProfileComplete = (profile: DriverProfile) => {
    setDriver(profile);
    setNeedsProfile(false);
    void refreshSnapshot();
  };

  const handleSendMessage = async (text: string) => {
    setIsSending(true);
    try {
      const res = await sendDriverChatMessage(text);
      await snapshotResource.run(async () => res.snapshot);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Error communicating with the dispatch agent", "error");
    } finally {
      setIsSending(false);
    }
  };

  const handleSendVoiceMessage = async (audioBase64: string, mimeType: string) => {
    setIsSending(true);
    try {
      const res = await sendDriverVoiceMessage(audioBase64, mimeType);
      await snapshotResource.run(async () => res.snapshot);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not process that voice message", "error");
    } finally {
      setIsSending(false);
    }
  };

  const handleHoldSlot = async (slotId: string) => {
    try {
      const res = await holdDockSlot(slotId);
      await snapshotResource.run(async () => res.snapshot);
      showToast(res.message, "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to hold slot", "error");
    }
  };

  const handleConfirmSlot = async (slotId: string) => {
    try {
      const res = await confirmDockSlot(slotId);
      await snapshotResource.run(async () => res.snapshot);
      showToast(res.message, "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to confirm slot", "error");
    }
  };

  const handleRequestSlotChange = async (slotId: string) => {
    const shipment = snapshotResource.data?.shipment;
    if (!shipment) return;
    try {
      await requestDriverDockSlotChange(shipment.shipment_id, slotId);
      showToast("Slot change requested -- waiting on WMS approval.", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to request a slot change", "error");
    }
  };

  const handleUpdateCheckin = async (status: ArrivalUpdateChoice) => {
    try {
      const res = await updateDriverCheckin(status);
      await snapshotResource.run(async () => res.snapshot);
      showToast(`Check-in updated to ${status.replace("_", " ")}`, "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Error updating gate status", "error");
    }
  };

  const handleQuickUpdateEta = (minutes: number) => {
    void handleSendMessage(
      `I am delayed by around ${minutes} minutes. Please update my declared ETA and show me available dock slots after that time.`
    );
  };

  // Real, driver-scoped stats computed from this driver's own snapshot --
  // replaces the fleet-wide placeholder numbers in PortalWorkspace's generic
  // stat row (which are static demo copy, not backed by any query, and were
  // never meaningful here: a driver's own JWT can't see fleet-wide RLS-scoped
  // data like "how many drivers are on the road" anyway).
  const driverStats = useMemo(() => {
    const shipment = snapshotResource.data?.shipment;
    const exception = snapshotResource.data?.exception;

    const statusLabel = shipment?.current_status
      ? shipment.current_status.replace(/_/g, " ").toLowerCase()
      : "no active load";

    const hasOpenException = Boolean(
      exception && !["RESOLVED", "CANCELLED", "DUPLICATE"].includes(exception.exception_status ?? "")
    );

    let etaLabel = "N/A";
    if (shipment?.original_eta_ts) {
      const original = new Date(shipment.original_eta_ts).getTime();
      const latest = shipment.latest_eta_ts ? new Date(shipment.latest_eta_ts).getTime() : original;
      const diffMin = Math.round((latest - original) / 60000);
      etaLabel = diffMin <= 5 ? "On time" : `+${diffMin}m vs plan`;
    }

    return [
      { value: statusLabel, label: "current shipment status" },
      { value: hasOpenException ? "1" : "0", label: "your open exceptions" },
      { value: etaLabel, label: "ETA vs. original plan" },
    ];
  }, [snapshotResource.data]);

  const handleEscalate = async (reason: string) => {
    try {
      const res = await escalateDriverException(reason);
      await snapshotResource.run(async () => res.snapshot);
      showToast("Thread escalated to a human coordinator", "info");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Escalation failed", "error");
    }
  };

  if (profileError) {
    return (
      <div className="space-y-3">
        <ErrorBanner>{profileError}</ErrorBanner>
        <button
          onClick={() => void loadInitial()}
          className="rounded-xl px-4 py-2.5 text-sm font-bold text-white"
          style={{ background: color }}
        >
          Retry
        </button>
      </div>
    );
  }

  if (profileLoading) {
    return (
      <SkeletonCard
        color={driversServiceDef.color}
        colorSoft={driversServiceDef.colorSoft}
        icon={driversServiceDef.icon}
        label="Loading driver portal…"
        lines={4}
      />
    );
  }

  if (needsProfile || !driver) {
    return <ProfileSetupForm color={color} onComplete={handleProfileComplete} />;
  }

  // Driver profile is resolved. Whether to show the shipment view, "no
  // active load", the loading skeleton, or an error now depends entirely on
  // snapshotResource's own explicit status -- never on snapshot data being
  // merely falsy, which is what let a still-in-flight/failed fetch get
  // misread as "confirmed no shipment" before this change.
  const snapshotStillResolving = (snapshotResource.status === "idle" || snapshotResource.status === "loading") && !snapshotResource.data;
  if (snapshotStillResolving) {
    return (
      <SkeletonCard
        color={driversServiceDef.color}
        colorSoft={driversServiceDef.colorSoft}
        icon={driversServiceDef.icon}
        label="Loading your shipment…"
        lines={4}
      />
    );
  }

  const snapshotFailedWithNoData = snapshotResource.status === "error" && !snapshotResource.data;
  if (snapshotFailedWithNoData) {
    return (
      <div className="space-y-3">
        <ErrorBanner>{snapshotResource.error ?? "Unable to load your shipment right now."}</ErrorBanner>
        <button
          onClick={() => void refreshSnapshot()}
          className="rounded-xl px-4 py-2.5 text-sm font-bold text-white"
          style={{ background: color }}
        >
          Retry
        </button>
      </div>
    );
  }

  // A background poll can be mid-retry (status "error") while we still have
  // the last good payload -- keep rendering that instead of an error
  // screen, matching the resilient "one dropped poll shouldn't nuke a
  // working view" behavior this portal already had.
  const snapshot = snapshotResource.data;
  if (!snapshot) return null;

  return (
    <div className="space-y-5">
      {toast &&
        createPortal(
          <motion.div
            initial={{ opacity: 0, y: -12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -12, scale: 0.97 }}
            transition={{ duration: 0.18 }}
            className={`fixed right-5 top-5 z-[9999] flex max-w-sm items-start gap-2.5 rounded-2xl border px-4 py-3 text-sm font-bold shadow-lg ${
              toast.tone === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : toast.tone === "error"
                ? "border-rose-200 bg-rose-50 text-rose-800"
                : "border-line bg-cloud text-ink"
            }`}
          >
            <span className="flex-1">{toast.text}</span>
            <button
              onClick={() => setToast(null)}
              className="shrink-0 opacity-60 transition hover:opacity-100"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" />
            </button>
          </motion.div>,
          document.body
        )}

      <div className="grid gap-4 sm:grid-cols-3">
        {driverStats.map((s, i) => (
          <motion.div
            key={s.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.06 * i }}
            className="rounded-2xl border border-line bg-white p-5"
          >
            <p className="text-2xl font-extrabold capitalize text-ink">{s.value}</p>
            <p className="mt-1 text-xs font-semibold text-mist">{s.label}</p>
          </motion.div>
        ))}
      </div>

      {snapshot.shipment ? (
        <>
          <ContextBar snapshot={snapshot} color={color} onQuickUpdateEta={handleQuickUpdateEta} />
          <AppointmentBanner appointment={snapshot.appointment} color={color} />
          <GateTimeline snapshot={snapshot} color={color} onUpdateCheckin={handleUpdateCheckin} />
        </>
      ) : (
        <div className="rounded-2xl border border-line bg-cloud/60 p-5 text-sm text-ink-soft">
          Profile registered as <strong className="text-ink">{driver.driver_name}</strong>. Awaiting a shipment
          assignment from dispatch — gate tracking and dock booking unlock automatically once a load is assigned,
          but you can still ask the dispatch assistant a question below.
        </div>
      )}

      {snapshot.shipment && (
        <DockSlotBoard
          color={color}
          facility={snapshot.facility}
          docks={snapshot.docks}
          slotOptions={snapshot.slot_options}
          onHoldSlot={handleHoldSlot}
          onConfirmSlot={handleConfirmSlot}
          onRequestSlotChange={handleRequestSlotChange}
        />
      )}

      {/* Floating dispatch-assistant chat: icon bottom-right, always available. */}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col items-end gap-4 sm:bottom-6 sm:right-6">
        <AnimatePresence>
          {chatOpen && (
            <motion.div
              initial={{ opacity: 0, scale: 0.92, y: 16 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.92, y: 16 }}
              transition={{ type: "spring", stiffness: 340, damping: 28 }}
              className={
                chatExpanded
                  ? "fixed inset-3 origin-bottom-right sm:static sm:inset-auto sm:h-[82vh] sm:max-h-[820px] sm:w-[600px]"
                  : "fixed inset-3 origin-bottom-right sm:static sm:inset-auto sm:h-[680px] sm:max-h-[78vh] sm:w-[440px]"
              }
            >
              <ChatPanel
                color={color}
                messages={snapshot.chat_messages}
                isSending={isSending}
                onSendMessage={handleSendMessage}
                onSendVoiceMessage={handleSendVoiceMessage}
                onEscalate={handleEscalate}
                onClose={() => setChatOpen(false)}
                isExpanded={chatExpanded}
                onToggleExpand={() => setChatExpanded((v) => !v)}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <motion.button
          onClick={() => setChatOpen((v) => !v)}
          aria-label={chatOpen ? "Close dispatch assistant chat" : "Open dispatch assistant chat"}
          whileHover={{ scale: 1.06 }}
          whileTap={{ scale: 0.94 }}
          className="relative flex h-16 w-16 items-center justify-center rounded-full text-white shadow-pop sm:h-[68px] sm:w-[68px]"
          style={{ background: `linear-gradient(135deg, ${color}, color-mix(in srgb, ${color} 70%, black))` }}
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={chatOpen ? "close" : "open"}
              initial={{ rotate: -45, opacity: 0 }}
              animate={{ rotate: 0, opacity: 1 }}
              exit={{ rotate: 45, opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="flex items-center justify-center"
            >
              {chatOpen ? <X className="h-7 w-7" /> : <MessageCircle className="h-7 w-7" />}
            </motion.span>
          </AnimatePresence>
          {!chatOpen && hasUnread && (
            <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center">
              <span className="absolute h-full w-full animate-ping rounded-full bg-rose-500 opacity-75" />
              <span className="relative h-3.5 w-3.5 rounded-full border-2 border-white bg-rose-500" />
            </span>
          )}
        </motion.button>
      </div>
    </div>
  );
}
