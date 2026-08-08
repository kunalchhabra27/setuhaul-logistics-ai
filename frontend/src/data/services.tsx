import type { LucideIcon } from "lucide-react";
import {
  Truck,
  Warehouse,
  ScanLine,
  UserRound,
  Route,
  PackageSearch,
  ClipboardCheck,
  Radar,
  Boxes,
  MapPinned,
  ShieldCheck,
  Gauge,
} from "lucide-react";

export type ServiceId = "tms" | "wms" | "checkin" | "drivers";

export interface ServiceHighlight {
  icon: LucideIcon;
  label: string;
}

export interface ServiceDef {
  id: ServiceId;
  name: string;
  shortName: string;
  tagline: string;
  description: string;
  icon: LucideIcon;
  color: string;
  colorSoft: string;
  colorVar: string;
  gradient: string;
  stat: { value: string; label: string };
  highlights: ServiceHighlight[];
  roadCaption: string;
}

export const services: ServiceDef[] = [
  {
    id: "drivers",
    name: "Driver's Portal",
    shortName: "Drivers",
    tagline: "Where every mile starts",
    description:
      "Log exceptions, declare ETAs, and view dock appointments right from the cab — one thread per trip, zero guesswork.",
    icon: UserRound,
    color: "var(--color-drivers)",
    colorSoft: "var(--color-drivers-soft)",
    colorVar: "drivers",
    gradient: "from-[#db2777] to-[#f97316]",
    stat: { value: "312", label: "active drivers on road" },
    highlights: [
      { icon: Route, label: "Live trip & ETA updates" },
      { icon: ClipboardCheck, label: "Exception reporting chat" },
      { icon: MapPinned, label: "Dock appointment lookup" },
    ],
    roadCaption: "Fueling up your route to the Driver's Portal…",
  },
  {
    id: "tms",
    name: "Transportation Management",
    shortName: "TMS",
    tagline: "Command the fleet",
    description:
      "Plan shipments, assign drivers & vehicles, and track every load from origin to destination in real time.",
    icon: Truck,
    color: "var(--color-tms)",
    colorSoft: "var(--color-tms-soft)",
    colorVar: "tms",
    gradient: "from-[#2563eb] to-[#7c3aed]",
    stat: { value: "340", label: "loads coordinated today" },
    highlights: [
      { icon: PackageSearch, label: "Shipment & vehicle planning" },
      { icon: Radar, label: "Real-time fleet visibility" },
      { icon: Gauge, label: "Carrier performance insights" },
    ],
    roadCaption: "Routing your credentials through the TMS network…",
  },
  {
    id: "wms",
    name: "Warehouse Management",
    shortName: "WMS",
    tagline: "Every dock, accounted for",
    description:
      "Manage facilities, dock doors and appointment slots — keep receiving capacity aligned with what's inbound.",
    icon: Warehouse,
    color: "var(--color-wms)",
    colorSoft: "var(--color-wms-soft)",
    colorVar: "wms",
    gradient: "from-[#d97706] to-[#ec4899]",
    stat: { value: "32", label: "dock doors managed" },
    highlights: [
      { icon: Boxes, label: "Facility & dock scheduling" },
      { icon: ClipboardCheck, label: "Appointment slot booking" },
      { icon: ShieldCheck, label: "Facility eligibility rules" },
    ],
    roadCaption: "Backing your session into the WMS loading bay…",
  },
  {
    id: "checkin",
    name: "Check-in Service",
    shortName: "Check-in",
    tagline: "Gate to yard, tracked",
    description:
      "Digitize gate arrivals and yard queueing — know exactly who's checked in, waiting, or already docked.",
    icon: ScanLine,
    color: "var(--color-checkin)",
    colorSoft: "var(--color-checkin-soft)",
    colorVar: "checkin",
    gradient: "from-[#059669] to-[#2563eb]",
    stat: { value: "96%", label: "gate scans under 30s" },
    highlights: [
      { icon: ScanLine, label: "Instant gate check-in" },
      { icon: Radar, label: "Live yard queue status" },
      { icon: ClipboardCheck, label: "Dock-in / dock-out logs" },
    ],
    roadCaption: "Scanning you through the Check-in gate…",
  },
];

export const getService = (id: string | undefined) =>
  services.find((s) => s.id === id);

export const defaultServiceId: ServiceId = "drivers";
