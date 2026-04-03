import { beforeEach, describe, expect, it } from "vitest";
import {
  handleTradingEventsPoll,
  handleTradingEventsPost,
  resetTradingBridgeStateForTests,
} from "./trading-bridge-api.js";

describe("trading-bridge event cursor", () => {
  beforeEach(() => {
    resetTradingBridgeStateForTests();
  });

  it("assigns monotonic seq values and polls incrementally by afterSeq", () => {
    handleTradingEventsPost({
      events: [
        { event: "quote", data: { stock_code: "600519.SH", last_price: 100 } },
        { event: "quote", data: { stock_code: "000001.SZ", last_price: 10 } },
      ],
    });

    const first = handleTradingEventsPoll(0, 0);
    expect(first.latestSeq).toBe(2);
    expect(first.events).toHaveLength(2);
    expect(first.events.map((event) => event.seq)).toEqual([1, 2]);

    handleTradingEventsPost({
      events: [{ event: "fill", data: { order_id: "A1" } }],
    });

    const second = handleTradingEventsPoll(first.latestSeq, 0);
    expect(second.events).toHaveLength(1);
    expect(second.events[0]?.seq).toBe(3);
    expect(second.latestSeq).toBe(3);
  });
});
