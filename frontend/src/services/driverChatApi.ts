import { createApiClient } from "./api";
import type {
  ArrivalUpdateChoice,
  DriverAppointmentSlotSummary,
  DriverAppointmentSummary,
  DriverChatResponse,
  DriverExceptionSummary,
  DriverFacilityCheckinSummary,
  DriverProfile,
  DriverProfileCompleteRequest,
  DriverSnapshot,
} from "../types/driverChat";

const api = createApiClient("drivers");
const base = "/driver-chat-eta";

export function driverChatHealth() {
  return api.request<{ status: string; system: string }>(`${base}/health`);
}

export function getMyDriverProfile() {
  return api.request<DriverProfile>(`${base}/me`);
}

export function completeDriverProfile(body: DriverProfileCompleteRequest) {
  return api.request<DriverProfile>(`${base}/profile/complete`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getDriverSnapshot() {
  return api.request<DriverSnapshot>(`${base}/snapshot`);
}

export function sendDriverChatMessage(message: string) {
  return api.request<DriverChatResponse>(`${base}/chat`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function sendDriverVoiceMessage(audioBase64: string, mimeType: string) {
  return api.request<DriverChatResponse>(`${base}/chat/voice`, {
    method: "POST",
    body: JSON.stringify({ audio_base64: audioBase64, mime_type: mimeType }),
  });
}

export function holdDockSlot(slot_id: string) {
  return api.request<{ slot: DriverAppointmentSlotSummary; snapshot: DriverSnapshot; message: string }>(
    `${base}/slots/hold`,
    { method: "POST", body: JSON.stringify({ slot_id }) }
  );
}

export function confirmDockSlot(slot_id: string) {
  return api.request<{ appointment: DriverAppointmentSummary; snapshot: DriverSnapshot; message: string }>(
    `${base}/slots/confirm`,
    { method: "POST", body: JSON.stringify({ slot_id }) }
  );
}

export function updateDriverCheckin(arrival_status: ArrivalUpdateChoice) {
  return api.request<{ checkin: DriverFacilityCheckinSummary; snapshot: DriverSnapshot }>(
    `${base}/checkin/update`,
    { method: "POST", body: JSON.stringify({ arrival_status }) }
  );
}

export function escalateDriverException(reason: string) {
  return api.request<{ exception?: DriverExceptionSummary | null; snapshot: DriverSnapshot }>(
    `${base}/escalate`,
    { method: "POST", body: JSON.stringify({ reason }) }
  );
}
