import { describe, expect, it } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { createBrokerAdapter, PaperBroker, QMTBroker } from "./index.js";

function makeConfig(overrides?: Partial<QuantTradingConfig>): QuantTradingConfig {
  return {
    dataDir: "/tmp/quant-broker-test",
    ...overrides,
  } as QuantTradingConfig;
}

describe("createBrokerAdapter", () => {
  it("should return PaperBroker when no tradingAccount is configured", () => {
    const broker = createBrokerAdapter(makeConfig());
    expect(broker).toBeInstanceOf(PaperBroker);
  });

  it("should return PaperBroker when mode is paper", () => {
    const broker = createBrokerAdapter(makeConfig({ tradingAccount: { mode: "paper" } }));
    expect(broker).toBeInstanceOf(PaperBroker);
  });

  it("should return PaperBroker when broker is explicitly paper", () => {
    const broker = createBrokerAdapter(
      makeConfig({ tradingAccount: { mode: "live", broker: "paper" } }),
    );
    expect(broker).toBeInstanceOf(PaperBroker);
  });

  it("should return QMTBroker when broker is qmt and mode is live", () => {
    const broker = createBrokerAdapter(
      makeConfig({ tradingAccount: { mode: "live", broker: "qmt" } }),
    );
    expect(broker).toBeInstanceOf(QMTBroker);
  });

  it("should throw for futu broker", () => {
    expect(() =>
      createBrokerAdapter(makeConfig({ tradingAccount: { mode: "live", broker: "futu" } })),
    ).toThrow(/Futu broker is not yet implemented/);
  });

  it("should throw for ib broker", () => {
    expect(() =>
      createBrokerAdapter(makeConfig({ tradingAccount: { mode: "live", broker: "ib" } })),
    ).toThrow(/Interactive Brokers connector is not yet implemented/);
  });

  it("should throw for eastmoney broker", () => {
    expect(() =>
      createBrokerAdapter(makeConfig({ tradingAccount: { mode: "live", broker: "eastmoney" } })),
    ).toThrow(/Eastmoney broker is not yet implemented/);
  });

  it("should throw for unknown broker type", () => {
    expect(() =>
      createBrokerAdapter(
        makeConfig({ tradingAccount: { mode: "live", broker: "unknown" as never } }),
      ),
    ).toThrow(/Unknown broker type/);
  });
});
