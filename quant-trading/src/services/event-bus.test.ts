import type { ServerResponse } from "node:http";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { EventBus } from "./event-bus.js";
import type { EventCategory, QuantEvent } from "./event-bus.js";

function mockSSEResponse(): ServerResponse & {
  chunks: string[];
  headArgs: unknown[];
  ended: boolean;
  __sseFilter?: Set<string> | null;
  listeners: Record<string, Array<(...args: unknown[]) => void>>;
} {
  const chunks: string[] = [];
  const listeners: Record<string, Array<(...args: unknown[]) => void>> = {};
  const obj = {
    chunks,
    headArgs: [] as unknown[],
    ended: false,
    listeners,
    writeHead(status: number, headers: Record<string, string>) {
      obj.headArgs = [status, headers];
    },
    write(chunk: string) {
      obj.chunks.push(chunk);
      return true;
    },
    end() {
      obj.ended = true;
    },
    on(event: string, handler: (...args: unknown[]) => void) {
      if (!obj.listeners[event]) obj.listeners[event] = [];
      obj.listeners[event]!.push(handler);
      return obj;
    },
  };
  return obj as unknown as ServerResponse & {
    chunks: string[];
    headArgs: unknown[];
    ended: boolean;
    listeners: Record<string, Array<(...args: unknown[]) => void>>;
  };
}

describe("EventBus", () => {
  let bus: EventBus;

  beforeEach(() => {
    bus = new EventBus();
  });

  // ── Pub/Sub ──────────────────────────────────────────────

  describe("pub/sub", () => {
    it("delivers events to matching category subscribers", () => {
      const handler = vi.fn();
      bus.on("signal", handler);

      bus.emit("signal", { symbol: "600519", direction: "buy" });

      expect(handler).toHaveBeenCalledTimes(1);
      const event: QuantEvent = handler.mock.calls[0]![0];
      expect(event.category).toBe("signal");
      expect(event.data).toEqual({ symbol: "600519", direction: "buy" });
      expect(event.id).toBe(1);
      expect(event.timestamp).toBeTruthy();
    });

    it("does not deliver events to other category subscribers", () => {
      const handler = vi.fn();
      bus.on("order", handler);

      bus.emit("signal", { symbol: "000001" });

      expect(handler).not.toHaveBeenCalled();
    });

    it("wildcard subscriber receives all events", () => {
      const handler = vi.fn();
      bus.on("*", handler);

      bus.emit("signal", {});
      bus.emit("order", {});
      bus.emit("pnl", {});

      expect(handler).toHaveBeenCalledTimes(3);
      expect(handler.mock.calls.map((c: unknown[]) => (c[0] as QuantEvent).category)).toEqual([
        "signal",
        "order",
        "pnl",
      ]);
    });

    it("unsubscribe removes handler", () => {
      const handler = vi.fn();
      const unsub = bus.on("signal", handler);

      bus.emit("signal", {});
      expect(handler).toHaveBeenCalledTimes(1);

      unsub();
      bus.emit("signal", {});
      expect(handler).toHaveBeenCalledTimes(1);
    });

    it("assigns monotonically increasing IDs", () => {
      const e1 = bus.emit("signal", {});
      const e2 = bus.emit("order", {});
      const e3 = bus.emit("pnl", {});

      expect(e1.id).toBe(1);
      expect(e2.id).toBe(2);
      expect(e3.id).toBe(3);
    });

    it("swallows handler errors without affecting others", () => {
      const bad = vi.fn(() => {
        throw new Error("boom");
      });
      const good = vi.fn();
      bus.on("signal", bad);
      bus.on("signal", good);

      bus.emit("signal", {});

      expect(bad).toHaveBeenCalled();
      expect(good).toHaveBeenCalled();
    });
  });

  // ── Recent Events ────────────────────────────────────────

  describe("recent events", () => {
    it("stores recent events", () => {
      bus.emit("signal", { a: 1 });
      bus.emit("order", { b: 2 });

      const recent = bus.getRecentEvents();
      expect(recent).toHaveLength(2);
      expect(recent[0]!.category).toBe("signal");
      expect(recent[1]!.category).toBe("order");
    });

    it("filters by category", () => {
      bus.emit("signal", {});
      bus.emit("order", {});
      bus.emit("signal", {});

      expect(bus.getRecentEvents("signal")).toHaveLength(2);
      expect(bus.getRecentEvents("order")).toHaveLength(1);
    });

    it("respects limit parameter", () => {
      for (let i = 0; i < 10; i++) bus.emit("signal", { i });

      expect(bus.getRecentEvents(undefined, 3)).toHaveLength(3);
      // Returns the last 3
      expect(bus.getRecentEvents(undefined, 3).map((e) => (e.data as { i: number }).i)).toEqual([
        7, 8, 9,
      ]);
    });

    it("prunes beyond maxRecent", () => {
      const smallBus = new EventBus({ maxRecent: 5 });
      for (let i = 0; i < 10; i++) smallBus.emit("signal", { i });

      expect(smallBus.getRecentEvents()).toHaveLength(5);
      expect(smallBus.getRecentEvents().map((e) => (e.data as { i: number }).i)).toEqual([
        5, 6, 7, 8, 9,
      ]);
    });
  });

  // ── SSE ──────────────────────────────────────────────────

  describe("SSE", () => {
    it("sends SSE headers on connection", () => {
      const res = mockSSEResponse();
      bus.addSSEClient(res);

      expect(res.headArgs[0]).toBe(200);
      expect((res.headArgs[1] as Record<string, string>)["Content-Type"]).toBe("text/event-stream");
      expect(res.chunks[0]).toBe(":ok\n\n");
      expect(bus.clientCount).toBe(1);
    });

    it("pushes events to connected clients", () => {
      const res = mockSSEResponse();
      bus.addSSEClient(res);

      bus.emit("signal", { symbol: "600519" });

      // After initial ":ok\n\n", should have id, event, data lines
      const sseOutput = res.chunks.slice(1).join("");
      expect(sseOutput).toContain("id: 1");
      expect(sseOutput).toContain("event: signal");
      expect(sseOutput).toContain('"category":"signal"');
      expect(sseOutput).toContain('"symbol":"600519"');
    });

    it("filters events by category", () => {
      const res = mockSSEResponse();
      bus.addSSEClient(res, "signal,order");

      bus.emit("signal", { a: 1 });
      bus.emit("pnl", { b: 2 }); // should be filtered out
      bus.emit("order", { c: 3 });

      // ":ok\n\n" + 2 SSE events (signal + order), pnl filtered
      const sseOutput = res.chunks.slice(1).join("");
      expect(sseOutput).toContain("event: signal");
      expect(sseOutput).toContain("event: order");
      expect(sseOutput).not.toContain("event: pnl");
    });

    it("removes client on close event", () => {
      const res = mockSSEResponse();
      bus.addSSEClient(res);
      expect(bus.clientCount).toBe(1);

      // Simulate client disconnect
      const closeHandler = res.listeners["close"]?.[0];
      expect(closeHandler).toBeTruthy();
      closeHandler!();

      expect(bus.clientCount).toBe(0);
    });

    it("catches up from lastEventId", () => {
      // Emit some events before client connects
      bus.emit("signal", { first: true });
      bus.emit("order", { second: true });
      bus.emit("pnl", { third: true });

      // Client connects with lastEventId=1 (wants events after ID 1)
      const res = mockSSEResponse();
      bus.addSSEClient(res, undefined, "1");

      // Should receive events 2 and 3 as catch-up
      const sseOutput = res.chunks.slice(1).join("");
      expect(sseOutput).toContain("id: 2");
      expect(sseOutput).toContain("id: 3");
      expect(sseOutput).not.toContain("id: 1\n");
    });

    it("catches up with category filter", () => {
      bus.emit("signal", { a: 1 });
      bus.emit("order", { b: 2 });
      bus.emit("signal", { c: 3 });

      const res = mockSSEResponse();
      bus.addSSEClient(res, "signal", "0");

      const sseOutput = res.chunks.slice(1).join("");
      expect(sseOutput).toContain("id: 1");
      expect(sseOutput).toContain("id: 3");
      expect(sseOutput).not.toContain("id: 2");
    });

    it("supports multiple clients", () => {
      const res1 = mockSSEResponse();
      const res2 = mockSSEResponse();
      bus.addSSEClient(res1);
      bus.addSSEClient(res2, "order");

      bus.emit("signal", { test: true });

      // res1 (no filter) should get it
      expect(res1.chunks.slice(1).join("")).toContain("event: signal");
      // res2 (order filter) should not
      expect(res2.chunks.slice(1).join("")).not.toContain("event: signal");

      expect(bus.clientCount).toBe(2);
    });
  });

  // ── Close ────────────────────────────────────────────────

  describe("close", () => {
    it("disconnects all clients and clears state", () => {
      const res = mockSSEResponse();
      bus.addSSEClient(res);
      bus.emit("signal", {});

      bus.close();

      expect(res.ended).toBe(true);
      expect(bus.clientCount).toBe(0);
      expect(bus.getRecentEvents()).toHaveLength(0);
    });

    it("resets ID counter after close", () => {
      bus.emit("signal", {});
      bus.emit("signal", {});
      bus.close();

      const e = bus.emit("signal", {});
      expect(e.id).toBe(1);
    });
  });
});
