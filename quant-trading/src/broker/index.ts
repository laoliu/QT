import type { BrokerType, QuantTradingConfig } from "../types.js";
/**
 * Broker factory — create the correct IBrokerAdapter based on config.
 *
 * Usage:
 *   const broker = createBrokerAdapter(config);
 *   await broker.connect();
 */
import type { IBrokerAdapter } from "./adapter.js";
import { FutuBroker } from "./futu-broker.js";
import { PaperBroker } from "./paper-broker.js";
import { QMTBroker } from "./qmt-broker.js";
import { RemoteQMTBroker } from "./remote-qmt-broker.js";

export type { IBrokerAdapter };
export { FutuBroker, PaperBroker, QMTBroker, RemoteQMTBroker };

/**
 * Instantiate the correct broker adapter.
 * Falls back to PaperBroker when mode is "paper" or broker is unrecognised.
 */
export function createBrokerAdapter(config: QuantTradingConfig): IBrokerAdapter {
  const acct = config.tradingAccount;
  const mode = acct?.mode ?? "paper";
  const broker: BrokerType = (acct?.broker as BrokerType) ?? "paper";

  if (mode === "paper" || broker === "paper") {
    return new PaperBroker(config);
  }

  switch (broker) {
    case "qmt":
      return new QMTBroker(config);

    case "remote-qmt":
      return new RemoteQMTBroker(config);

    case "futu":
      return new FutuBroker(config);

    case "ib":
      throw new Error(
        "Interactive Brokers connector is not yet implemented. Set tradingAccount.broker = 'qmt' or 'paper'.",
      );

    case "eastmoney":
      throw new Error(
        "Eastmoney broker is not yet implemented. Use 'qmt' for A-share live trading.",
      );

    default:
      throw new Error(
        `Unknown broker type: ${broker as string}. Supported: paper, qmt, remote-qmt, futu`,
      );
  }
}
