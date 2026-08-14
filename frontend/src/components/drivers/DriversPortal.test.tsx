import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DriversPortal from "./DriversPortal";
import {
  getDriverSnapshot,
  getMyDriverProfile,
} from "../../services/driverChatApi";
import type { DriverProfile, DriverSnapshot } from "../../types/driverChat";

vi.mock("../../services/driverChatApi", () => ({
  getMyDriverProfile: vi.fn(),
  getDriverSnapshot: vi.fn(),
  sendDriverChatMessage: vi.fn(),
  sendDriverVoiceMessage: vi.fn(),
  holdDockSlot: vi.fn(),
  confirmDockSlot: vi.fn(),
  updateDriverCheckin: vi.fn(),
  escalateDriverException: vi.fn(),
  requestDriverDockSlotChange: vi.fn(),
}));

// Child components have their own unrelated dependencies/rendering concerns
// -- stub them so this suite stays focused on DriversPortal's own
// loading/success/empty/error state machine, which is what the bug and the
// fix are actually about.
vi.mock("./ProfileSetupForm", () => ({ default: () => <div>profile-setup-form</div> }));
vi.mock("./ContextBar", () => ({ default: () => <div>context-bar</div> }));
vi.mock("./AppointmentBanner", () => ({ default: () => <div>appointment-banner</div> }));
vi.mock("./GateTimeline", () => ({ default: () => <div>gate-timeline</div> }));
vi.mock("./ChatPanel", () => ({ default: () => <div>chat-panel</div> }));
vi.mock("./DockSlotBoard", () => ({ default: () => <div>dock-slot-board</div> }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const baseDriver: DriverProfile = {
  driver_id: "DRV1",
  driver_name: "Test Driver",
  carrier_id: "CAR1",
  phone: "+1-555-0100",
  licence_number: "L1",
  home_base_city: "Jaipur",
  driver_status: "ACTIVE",
};

function makeSnapshot(overrides: Partial<DriverSnapshot> = {}): DriverSnapshot {
  return {
    driver: baseDriver,
    vehicle: null,
    shipment: null,
    facility: null,
    docks: [],
    appointment: null,
    checkin: null,
    exception: null,
    slot_options: [],
    chat_messages: [],
    ...overrides,
  };
}

const shipmentSnapshot = makeSnapshot({
  shipment: {
    shipment_id: "SHP1",
    order_reference: "ORD1",
    current_status: "PLANNED",
    original_eta_ts: "2026-01-01T10:00:00Z",
    latest_eta_ts: "2026-01-01T10:00:00Z",
  },
});

const NO_ACTIVE_LOAD = /no active load/i;
const AWAITING_ASSIGNMENT = /awaiting a shipment assignment/i;

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("DriversPortal initial load", () => {
  it("never renders 'no active load' when the driver has an assigned shipment", async () => {
    vi.mocked(getMyDriverProfile).mockResolvedValue(baseDriver);
    const d = deferred<DriverSnapshot>();
    vi.mocked(getDriverSnapshot).mockReturnValue(d.promise);

    render(<DriversPortal color="#db2777" />);

    // While the initial fetch is still in flight: skeleton, no incorrect state.
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText(NO_ACTIVE_LOAD)).not.toBeInTheDocument();
    expect(screen.queryByText("context-bar")).not.toBeInTheDocument();

    await act(async () => {
      d.resolve(shipmentSnapshot);
      await d.promise;
    });

    expect(await screen.findByText("context-bar")).toBeInTheDocument();
    expect(screen.queryByText(NO_ACTIVE_LOAD)).not.toBeInTheDocument();
    expect(screen.queryByText(AWAITING_ASSIGNMENT)).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows 'no active load' only after a successful fetch confirms there is genuinely none", async () => {
    vi.mocked(getMyDriverProfile).mockResolvedValue(baseDriver);
    const d = deferred<DriverSnapshot>();
    vi.mocked(getDriverSnapshot).mockReturnValue(d.promise);

    render(<DriversPortal color="#db2777" />);

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText(NO_ACTIVE_LOAD)).not.toBeInTheDocument();
    expect(screen.queryByText(AWAITING_ASSIGNMENT)).not.toBeInTheDocument();

    await act(async () => {
      d.resolve(makeSnapshot());
      await d.promise;
    });

    expect(await screen.findByText(AWAITING_ASSIGNMENT)).toBeInTheDocument();
    expect(screen.getByText("no active load")).toBeInTheDocument();
  });

  it("shows an error/retry state (not 'no active load') when the snapshot fetch fails, and retry recovers", async () => {
    vi.mocked(getMyDriverProfile).mockResolvedValue(baseDriver);
    vi.mocked(getDriverSnapshot)
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(shipmentSnapshot);

    render(<DriversPortal color="#db2777" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("network down");
    expect(screen.queryByText(NO_ACTIVE_LOAD)).not.toBeInTheDocument();
    expect(screen.queryByText(AWAITING_ASSIGNMENT)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText("context-bar")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the profile-load error/retry state (not the profile setup form) on a non-404 profile failure", async () => {
    vi.mocked(getMyDriverProfile).mockRejectedValueOnce(new Error("profile fetch failed"));

    render(<DriversPortal color="#db2777" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("profile fetch failed");
    expect(screen.queryByText("profile-setup-form")).not.toBeInTheDocument();
  });
});

describe("DriversPortal background refresh", () => {
  it("updates seamlessly on the 8s poll without flashing loading or 'no active load'", async () => {
    vi.useFakeTimers();
    vi.mocked(getMyDriverProfile).mockResolvedValue(baseDriver);
    vi.mocked(getDriverSnapshot)
      .mockResolvedValueOnce(shipmentSnapshot)
      .mockResolvedValueOnce(
        makeSnapshot({ shipment: { ...shipmentSnapshot.shipment!, current_status: "IN_TRANSIT" } })
      );

    render(<DriversPortal color="#db2777" />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("context-bar")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });

    // Still on the shipment view the whole time -- no fallback to the
    // skeleton or "no active load" in between polls.
    expect(screen.getByText("context-bar")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(NO_ACTIVE_LOAD)).not.toBeInTheDocument();
    expect(getDriverSnapshot).toHaveBeenCalledTimes(2);
  });

  it("keeps showing the last good view instead of an error screen when a background poll fails", async () => {
    vi.useFakeTimers();
    vi.mocked(getMyDriverProfile).mockResolvedValue(baseDriver);
    vi.mocked(getDriverSnapshot)
      .mockResolvedValueOnce(shipmentSnapshot)
      .mockRejectedValueOnce(new Error("transient poll failure"));

    render(<DriversPortal color="#db2777" />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("context-bar")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });

    expect(screen.getByText("context-bar")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(NO_ACTIVE_LOAD)).not.toBeInTheDocument();
  });

  it("never overlaps snapshot requests even if a poll tick fires while one is still in flight", async () => {
    vi.useFakeTimers();
    vi.mocked(getMyDriverProfile).mockResolvedValue(baseDriver);
    const slow = deferred<DriverSnapshot>();
    vi.mocked(getDriverSnapshot).mockReturnValueOnce(slow.promise);

    render(<DriversPortal color="#db2777" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    // The first call is still pending -- advance well past one poll
    // interval; the in-flight guard must skip the tick rather than firing a
    // second overlapping request.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(getDriverSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      slow.resolve(shipmentSnapshot);
      await slow.promise;
    });
    expect(screen.getByText("context-bar")).toBeInTheDocument();
  });
});
