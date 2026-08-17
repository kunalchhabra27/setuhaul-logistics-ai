import { act, cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DriversPortal from "../DriversPortal";
import type { DriverChatMessageSummary, DriverChatResponse, DriverProfile, DriverSnapshot } from "../../../types/driverChat";

const {
  getMyDriverProfile,
  getDriverSnapshot,
  sendDriverChatMessage,
  sendDriverVoiceMessage,
  holdDockSlot,
  confirmDockSlot,
  requestDriverDockSlotChange,
  updateDriverCheckin,
  escalateDriverException,
  sendEmergencyAlert,
} = vi.hoisted(() => ({
  getMyDriverProfile: vi.fn(),
  getDriverSnapshot: vi.fn(),
  sendDriverChatMessage: vi.fn(),
  sendDriverVoiceMessage: vi.fn(),
  holdDockSlot: vi.fn(),
  confirmDockSlot: vi.fn(),
  requestDriverDockSlotChange: vi.fn(),
  updateDriverCheckin: vi.fn(),
  escalateDriverException: vi.fn(),
  sendEmergencyAlert: vi.fn(),
}));

vi.mock("../../../services/driverChatApi", () => ({
  getMyDriverProfile,
  getDriverSnapshot,
  sendDriverChatMessage,
  sendDriverVoiceMessage,
  holdDockSlot,
  confirmDockSlot,
  requestDriverDockSlotChange,
  updateDriverCheckin,
  escalateDriverException,
  sendEmergencyAlert,
}));

const driver: DriverProfile = {
  driver_id: "drv-1",
  driver_name: "Jordan Rivera",
  driver_status: "ACTIVE",
};

function baseSnapshot(chat_messages: DriverChatMessageSummary[] = []): DriverSnapshot {
  return {
    driver,
    vehicle: null,
    shipment: null,
    facility: null,
    docks: [],
    appointment: null,
    checkin: null,
    exception: null,
    slot_options: [],
    chat_messages,
  };
}

function msg(id: string, sender: "DRIVER" | "AGENT", text: string): DriverChatMessageSummary {
  return { chat_message_id: id, sender_type: sender, message_text: text, message_ts: new Date().toISOString() };
}

function snapshotWithOpenSlot(): DriverSnapshot {
  return {
    ...baseSnapshot(),
    shipment: { shipment_id: "shp-1", destination_facility_id: "fac-1", current_status: "PLANNED" },
    facility: { facility_id: "fac-1", facility_name: "Test Facility" },
    docks: [{ dock_id: "dock-1", facility_id: "fac-1", dock_code: "D1" }],
    slot_options: [
      {
        slot_id: "slot-1",
        dock_id: "dock-1",
        dock_code: "D1",
        start_time: new Date(Date.now() + 3600_000).toISOString(),
        end_time: new Date(Date.now() + 7200_000).toISOString(),
        is_compatible: true,
        compatibility_reason: "Fits ETA, dock type, and vehicle compatibility",
        estimated_wait_minutes: 0,
        is_held: false,
        is_booked_by_me: false,
      },
    ],
  };
}

function chatResponse(chat_messages: DriverChatMessageSummary[]): DriverChatResponse {
  const agentMessage = [...chat_messages].reverse().find((m) => m.sender_type === "AGENT")!;
  return {
    agent_message: agentMessage,
    suggested_options: [],
    exception: null,
    snapshot: baseSnapshot(chat_messages),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function openChatAndLoad() {
  render(<DriversPortal color="#123456" />);
  const openButton = await screen.findByLabelText("Open dispatch assistant chat");
  fireEvent.click(openButton);
  await screen.findByText("Dispatch support assistant");
}

async function sendTyped(text: string) {
  const input = screen.getByPlaceholderText("Type message or ask for help...");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Send/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  getMyDriverProfile.mockResolvedValue(driver);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("DriversPortal chat send/reconciliation", () => {
  it("renders the assistant's reply exactly once on a successful send", async () => {
    getDriverSnapshot.mockResolvedValueOnce(baseSnapshot());
    await openChatAndLoad();

    const reply = [msg("d1", "DRIVER", "I have a tyre delay"), msg("a1", "AGENT", "Got it, updating your ETA.")];
    sendDriverChatMessage.mockResolvedValueOnce(chatResponse(reply));

    await act(async () => {
      await sendTyped("I have a tyre delay");
    });

    expect(screen.getAllByText("Got it, updating your ETA.")).toHaveLength(1);
    expect(screen.queryByText("Checking dock door availability...")).not.toBeInTheDocument();
  });

  it("does not let a stale background poll revert a just-rendered reply (the reported bug)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getDriverSnapshot.mockResolvedValueOnce(baseSnapshot());
    await openChatAndLoad();

    // Turn 1: an ordinary, non-racing send establishes prior chat history.
    const turn1 = [msg("d1", "DRIVER", "Hello"), msg("a1", "AGENT", "Hi, how can I help?")];
    sendDriverChatMessage.mockResolvedValueOnce(chatResponse(turn1));
    await act(async () => {
      await sendTyped("Hello");
    });
    await screen.findByText("Hi, how can I help?");

    // The 8s background poll fires and is still in flight (its own fetch
    // hasn't resolved yet) when a second chat send starts and completes.
    const pollForTurn2 = deferred<DriverSnapshot>();
    getDriverSnapshot.mockReturnValueOnce(pollForTurn2.promise);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });

    const turn2 = [...turn1, msg("d2", "DRIVER", "Tyre delay +45m"), msg("a2", "AGENT", "Updated your ETA by 45 minutes.")];
    sendDriverChatMessage.mockResolvedValueOnce(chatResponse(turn2));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Tyre delay \(\+45m\)/i }));
      await flush();
    });
    await screen.findByText("Updated your ETA by 45 minutes.");

    // The poll finally resolves with data fetched before turn 2 existed --
    // it must not wipe out the reply the driver is already looking at, even
    // though it's a perfectly valid 200 response in its own right.
    await act(async () => {
      pollForTurn2.resolve(baseSnapshot(turn1));
      await flush();
    });

    expect(screen.getByText("Updated your ETA by 45 minutes.")).toBeInTheDocument();
    expect(screen.getByText("Tyre delay +45m")).toBeInTheDocument();
  });

  it("fires exactly one request when a quick-action button is double-tapped", async () => {
    getDriverSnapshot.mockResolvedValueOnce(baseSnapshot());
    await openChatAndLoad();

    const pendingSend = deferred<DriverChatResponse>();
    sendDriverChatMessage.mockReturnValueOnce(pendingSend.promise);

    const quickButton = screen.getByRole("button", { name: /Tyre delay \(\+45m\)/i });
    await act(async () => {
      fireEvent.click(quickButton);
      fireEvent.click(quickButton);
      await flush();
    });

    expect(sendDriverChatMessage).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingSend.resolve(chatResponse([msg("d1", "DRIVER", "x"), msg("a1", "AGENT", "y")]));
      await flush();
    });
  });

  it("preserves conversation and allows retry after a failed send", async () => {
    getDriverSnapshot.mockResolvedValueOnce(baseSnapshot());
    await openChatAndLoad();

    const turn1 = [msg("d1", "DRIVER", "Hello"), msg("a1", "AGENT", "Hi there.")];
    sendDriverChatMessage.mockResolvedValueOnce(chatResponse(turn1));
    await act(async () => {
      await sendTyped("Hello");
    });
    await screen.findByText("Hi there.");

    sendDriverChatMessage.mockRejectedValueOnce(
      new Error("Lost connection to dispatch. Check your network and try again.")
    );
    await act(async () => {
      await sendTyped("Are you there?");
    });

    expect(screen.queryByText("Checking dock door availability...")).not.toBeInTheDocument();
    expect(screen.getByText("Hi there.")).toBeInTheDocument();
    expect(screen.getByText(/Lost connection to dispatch/i)).toBeInTheDocument();

    const turn3 = [...turn1, msg("d2", "DRIVER", "retry"), msg("a2", "AGENT", "Yes, still here.")];
    sendDriverChatMessage.mockResolvedValueOnce(chatResponse(turn3));
    await act(async () => {
      await sendTyped("retry");
    });
    expect(await screen.findByText("Yes, still here.")).toBeInTheDocument();
  });

  it("fires exactly one request when Hold slot is double-clicked", async () => {
    // Regression test: a real user hit a raw "RESOURCE_CONFLICT" 409 from
    // double-tapping Hold/Confirm on the dock slot board -- these handlers
    // had no synchronous lock at all (unlike the chat-send handlers above),
    // so two overlapping requests could both pass the backend's
    // availability check before either committed, then collide on insert.
    getDriverSnapshot.mockResolvedValueOnce(snapshotWithOpenSlot());
    render(<DriversPortal color="#123456" />);

    const pendingHold = deferred<{ slot: unknown; snapshot: DriverSnapshot; message: string }>();
    holdDockSlot.mockReturnValueOnce(pendingHold.promise);

    const holdButton = await screen.findByRole("button", { name: /Hold slot/i });
    await act(async () => {
      fireEvent.click(holdButton);
      fireEvent.click(holdButton);
      await flush();
    });

    expect(holdDockSlot).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingHold.resolve({ slot: {}, snapshot: snapshotWithOpenSlot(), message: "Slot held for 5 minutes." });
      await flush();
    });
  });

  it("keeps the reply visible across several normal (non-racing) poll cycles", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getDriverSnapshot.mockResolvedValueOnce(baseSnapshot());
    await openChatAndLoad();

    const turn1 = [msg("d1", "DRIVER", "Hello"), msg("a1", "AGENT", "Hi, how can I help?")];
    sendDriverChatMessage.mockResolvedValueOnce(chatResponse(turn1));
    await act(async () => {
      await sendTyped("Hello");
    });
    await screen.findByText("Hi, how can I help?");

    getDriverSnapshot.mockResolvedValue(baseSnapshot(turn1));
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(8000);
        await flush();
      });
      expect(screen.getByText("Hi, how can I help?")).toBeInTheDocument();
    }
  });
});
