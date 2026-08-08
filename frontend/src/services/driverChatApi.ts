import { api } from "./api";

export function driverChatHealth() {
  return api.request<{ status: string; system: string }>("/driver-chat-eta/health");
}
