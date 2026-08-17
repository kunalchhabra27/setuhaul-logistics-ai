import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Send,
  Bot,
  User,
  ShieldAlert,
  CheckCircle2,
  Sparkles,
  AlertCircle,
  AlertTriangle,
  X,
  Maximize2,
  Minimize2,
  Mic,
  Square,
} from "lucide-react";
import type { DriverChatMessageSummary } from "../../types/driverChat";

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      resolve(result.split(",")[1] ?? "");
    };
    reader.onerror = () => reject(reader.error ?? new Error("Could not read recorded audio."));
    reader.readAsDataURL(blob);
  });
}

function pickRecorderMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return undefined;
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return candidates.find((c) => MediaRecorder.isTypeSupported(c));
}

function formatDuration(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function ChatPanel({
  color,
  messages,
  isSending,
  onSendMessage,
  onSendVoiceMessage,
  onEscalate,
  onClose,
  isExpanded,
  onToggleExpand,
  emergencyAvailable,
  onEmergencyAlert,
}: {
  color: string;
  messages: DriverChatMessageSummary[];
  isSending: boolean;
  onSendMessage: (text: string) => Promise<void>;
  onSendVoiceMessage?: (audioBase64: string, mimeType: string) => Promise<void>;
  onEscalate: (reason: string) => Promise<void>;
  onClose?: () => void;
  isExpanded?: boolean;
  onToggleExpand?: () => void;
  // True once the assistant has flagged a safety-critical situation
  // (accident, engine failure, etc.) this session -- see
  // DriversPortal.tsx's derivation from snapshot.exception.severity_code.
  emergencyAvailable?: boolean;
  onEmergencyAlert?: () => Promise<void>;
}) {
  const [inputText, setInputText] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [micError, setMicError] = useState<string | null>(null);
  const [sendingAlert, setSendingAlert] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length, isSending]);

  useEffect(() => {
    if (!micError) return;
    const t = setTimeout(() => setMicError(null), 5000);
    return () => clearTimeout(t);
  }, [micError]);

  // Stop any in-progress recording if the panel unmounts (e.g. closed mid-recording).
  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // Belt-and-suspenders alongside the `isSending` prop check: the prop only
  // updates after the parent re-renders following its own setIsSending(true),
  // so a same-tick double-tap (e.g. two rapid clicks on a quick action) can
  // slip past an `isSending`-only guard before that re-render lands. This
  // ref is set synchronously, closing that window locally; the parent
  // (DriversPortal) still holds the authoritative lock across all send
  // paths, this is just a first line of defense that also avoids firing an
  // extra request at all.
  const sendingRef = useRef(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!inputText.trim() || isSending || isRecording || sendingRef.current) return;
    const text = inputText;
    setInputText("");
    sendingRef.current = true;
    try {
      await onSendMessage(text);
    } finally {
      sendingRef.current = false;
    }
  };

  const quickPrompt = (prompt: string) => {
    if (isSending || isRecording || sendingRef.current) return;
    sendingRef.current = true;
    void onSendMessage(prompt).finally(() => {
      sendingRef.current = false;
    });
  };

  const startRecording = async () => {
    setMicError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mimeType = pickRecorderMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      audioChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        const clipMimeType = recorder.mimeType || "audio/webm";
        const blob = new Blob(audioChunksRef.current, { type: clipMimeType });
        audioChunksRef.current = [];
        if (blob.size === 0 || !onSendVoiceMessage) return;
        void blobToBase64(blob)
          .then((base64) => onSendVoiceMessage(base64, clipMimeType))
          .catch(() => setMicError("Could not process that recording. Please try again."));
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
      setRecordingSeconds(0);
      timerRef.current = window.setInterval(() => setRecordingSeconds((s) => s + 1), 1000);
    } catch (err) {
      const denied = err instanceof DOMException && err.name === "NotAllowedError";
      setMicError(denied ? "Microphone access denied. Allow microphone permission to send voice messages." : "Could not access your microphone.");
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
    setIsRecording(false);
    if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const handleMicClick = () => {
    if (isSending) return;
    if (isRecording) stopRecording();
    else void startRecording();
  };

  const handleEmergencyAlert = async () => {
    if (!onEmergencyAlert || sendingAlert) return;
    setSendingAlert(true);
    try {
      await onEmergencyAlert();
    } finally {
      setSendingAlert(false);
    }
  };

  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-3xl border border-line bg-white shadow-pop">
      <div
        className="flex flex-shrink-0 items-center justify-between border-b border-line px-4 py-3.5 sm:px-5"
        style={{
          background: `linear-gradient(135deg, color-mix(in srgb, ${color} 12%, white), var(--color-cloud))`,
        }}
      >
        <div className="flex items-center gap-3">
          <div
            className="flex h-11 w-11 items-center justify-center rounded-2xl shadow-sm"
            style={{ background: `color-mix(in srgb, ${color} 16%, white)`, color }}
          >
            <Bot className="h-6 w-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-extrabold text-ink sm:text-base">Dispatch support assistant</h2>
              <span className="flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                Online
              </span>
            </div>
            <p className="text-xs text-mist">Ask questions or request dock slot changes</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {emergencyAvailable && onEmergencyAlert && (
            <button
              onClick={() => void handleEmergencyAlert()}
              disabled={sendingAlert}
              className="flex animate-pulse items-center gap-1.5 rounded-xl border border-rose-300 bg-rose-600 px-3 py-2 text-xs font-bold text-white shadow-sm transition hover:bg-rose-700 active:scale-95 disabled:opacity-60"
            >
              <AlertTriangle className="h-4 w-4" />
              <span className="hidden sm:inline">{sendingAlert ? "Sending alert..." : "Send Emergency Alert"}</span>
            </button>
          )}
          <button
            onClick={() => void onEscalate("Driver requested manual coordinator support")}
            className="hidden items-center gap-1.5 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-600 transition hover:bg-rose-100 active:scale-95 sm:flex"
          >
            <ShieldAlert className="h-4 w-4" />
            Talk to human
          </button>
          <button
            onClick={() => void onEscalate("Driver requested manual coordinator support")}
            aria-label="Talk to human"
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-rose-200 bg-rose-50 text-rose-600 transition hover:bg-rose-100 active:scale-95 sm:hidden"
          >
            <ShieldAlert className="h-4 w-4" />
          </button>
          {onToggleExpand && (
            <button
              onClick={onToggleExpand}
              aria-label={isExpanded ? "Shrink chat" : "Expand chat"}
              className="hidden h-9 w-9 items-center justify-center rounded-xl border border-line bg-white text-ink-soft transition hover:bg-line active:scale-95 sm:flex"
            >
              {isExpanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
            </button>
          )}
          {onClose && (
            <button
              onClick={onClose}
              aria-label="Close chat"
              className="flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-white text-ink-soft transition hover:bg-line active:scale-95"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto bg-cloud p-3 sm:p-5">
        {messages.length === 0 && (
          <p className="py-8 text-center text-xs text-mist">No messages yet — report an exception or ask a question to get started.</p>
        )}
        {messages.map((msg) => {
          const isDriver = msg.sender_type === "DRIVER";
          const isSystemish = msg.sender_type === "SYSTEM" || msg.sender_type === "OPERATIONS";

          if (isSystemish) {
            const isOps = msg.sender_type === "OPERATIONS";
            return (
              <div key={msg.chat_message_id} className="my-2 flex justify-center">
                <div className={`max-w-xl rounded-2xl border px-4 py-3 text-center text-xs font-semibold sm:text-sm ${isOps ? "border-rose-200 bg-rose-50 text-rose-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
                  <div className="mb-1 flex items-center justify-center gap-2 font-bold">
                    {isOps ? <AlertCircle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
                    <span>{isOps ? "Human coordinator" : "System"}</span>
                  </div>
                  <p>{msg.message_text}</p>
                </div>
              </div>
            );
          }

          return (
            <div key={msg.chat_message_id} className={`flex items-start gap-2.5 sm:gap-3 ${isDriver ? "flex-row-reverse" : ""}`}>
              <div
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-xs font-bold sm:h-9 sm:w-9 ${isDriver ? "text-white" : "border border-line bg-white"}`}
                style={isDriver ? { background: color } : { color }}
              >
                {isDriver ? <User className="h-4 w-4 sm:h-5 sm:w-5" /> : <Bot className="h-4 w-4 sm:h-5 sm:w-5" />}
              </div>

              <div
                className={`max-w-[88%] rounded-2xl border p-3.5 shadow-soft sm:max-w-[80%] sm:p-4 ${isDriver ? "rounded-tr-none border-transparent text-white" : "rounded-tl-none border-line bg-white text-ink"}`}
                style={isDriver ? { background: color } : undefined}
              >
                <div className="mb-1 flex items-center justify-between gap-3">
                  <span className="text-xs font-extrabold sm:text-sm">{isDriver ? "You" : "SetuHaul dispatch agent"}</span>
                  <span className={`font-mono text-[10px] sm:text-xs ${isDriver ? "text-white/70" : "text-mist"}`}>
                    {msg.message_ts ? new Date(msg.message_ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                  </span>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed sm:text-base">{msg.message_text}</p>
              </div>
            </div>
          );
        })}

        {isSending && (
          <div className="flex items-center gap-3 py-2 text-xs text-mist sm:text-sm">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-white" style={{ color }}>
              <Bot className="h-4 w-4 animate-spin" />
            </div>
            <div className="flex items-center gap-2 rounded-2xl border border-line bg-white px-4 py-2.5 shadow-soft">
              <Sparkles className="h-4 w-4 animate-pulse" style={{ color }} />
              <span className="font-semibold text-ink-soft">Checking dock door availability...</span>
            </div>
          </div>
        )}
      </div>

      <div className="no-scrollbar flex flex-shrink-0 items-center gap-2 overflow-x-auto border-t border-line bg-cloud px-3 py-2">
        <span className="shrink-0 text-xs font-bold uppercase tracking-wide text-mist">Quick:</span>
        <button
          onClick={() => quickPrompt("I have a tyre damage delay. I will arrive 45 minutes late.")}
          disabled={isSending || isRecording}
          className="shrink-0 rounded-xl border border-line bg-white px-3 py-2 text-xs font-bold text-ink-soft transition hover:bg-line disabled:opacity-50 sm:text-sm"
        >
          🛞 Tyre delay (+45m)
        </button>
        <button
          onClick={() => quickPrompt("I need to exit the facility before 9 PM for my next shipment.")}
          disabled={isSending || isRecording}
          className="shrink-0 rounded-xl border border-line bg-white px-3 py-2 text-xs font-bold text-ink-soft transition hover:bg-line disabled:opacity-50 sm:text-sm"
        >
          ⏱️ Must leave &lt; 9 PM
        </button>
      </div>

      {micError && (
        <div className="flex-shrink-0 border-t border-rose-100 bg-rose-50 px-4 py-2 text-xs font-semibold text-rose-700">
          {micError}
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex flex-shrink-0 items-center gap-2 border-t border-line bg-white p-2.5 sm:p-3">
        {isRecording ? (
          <div className="flex min-h-[44px] flex-1 items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4">
            <span className="relative flex h-2.5 w-2.5 shrink-0">
              <span className="absolute h-full w-full animate-ping rounded-full bg-rose-500 opacity-75" />
              <span className="relative h-2.5 w-2.5 rounded-full bg-rose-500" />
            </span>
            <span className="text-sm font-bold text-rose-700">Recording {formatDuration(recordingSeconds)}</span>
            <span className="ml-auto hidden text-xs font-medium text-rose-600/80 sm:inline">Tap the square to send</span>
          </div>
        ) : (
          <input
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="Type message or ask for help..."
            disabled={isSending}
            className="min-h-[44px] flex-1 rounded-xl border border-line bg-cloud px-4 py-3 text-sm text-ink outline-none transition focus:border-transparent focus:ring-2 sm:text-base"
            style={{ ["--tw-ring-color" as string]: color }}
          />
        )}

        {onSendVoiceMessage && (
          <button
            type="button"
            onClick={handleMicClick}
            disabled={isSending}
            aria-label={isRecording ? "Stop recording and send" : "Record a voice message"}
            className={`flex min-h-[44px] min-w-[44px] items-center justify-center rounded-xl border transition active:scale-95 disabled:opacity-50 ${
              isRecording
                ? "border-rose-500 bg-rose-500 text-white hover:bg-rose-600"
                : "border-line bg-cloud text-ink-soft hover:bg-line"
            }`}
          >
            {isRecording ? <Square className="h-4 w-4" fill="currentColor" /> : <Mic className="h-5 w-5" />}
          </button>
        )}

        <button
          type="submit"
          disabled={!inputText.trim() || isSending || isRecording}
          className="flex min-h-[44px] items-center gap-1.5 rounded-xl px-5 py-3 text-sm font-black text-white shadow-soft transition disabled:opacity-50 sm:text-base"
          style={{ background: color }}
        >
          <span className="hidden sm:inline">Send</span>
          <Send className="h-5 w-5" />
        </button>
      </form>
    </div>
  );
}
