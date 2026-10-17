import type { IncomingMessage, ServerResponse } from "node:http";

export type Logger = {
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
  debug?(message: string): void;
};

export type AnyAgentTool = {
  name: string;
  label?: string;
  description: string;
  parameters: unknown;
  execute(toolCallId: string, params: unknown): Promise<unknown>;
};

export type AppService = {
  id: string;
  start(ctx?: AppServiceContext): Promise<void>;
  stop(ctx?: AppServiceContext): Promise<void>;
};

export type AppServiceContext = {
  config?: Record<string, unknown>;
  logger?: Logger;
};

export type RouteRegistration = {
  path: string;
  handler: (req: IncomingMessage, res: ServerResponse) => Promise<boolean | void> | boolean | void;
  match?: "exact" | "prefix";
  auth?: "plugin" | "none";
};

export type QuantTradingRuntime = {
  id: string;
  name: string;
  source: "quant-trading";
  pluginConfig: Record<string, unknown>;
  config: { gateway?: { auth?: { token?: string } } };
  logger: Logger;
  runtime: { subagent: { run: (...args: any[]) => Promise<any>; waitForRun: (...args: any[]) => Promise<any>; getSessionMessages: (...args: any[]) => Promise<any> } };
  registerTool(tool: AnyAgentTool, options?: { names?: string[] }): void;
  registerCommand(command: Record<string, unknown>): void;
  registerCli(handler: unknown, options?: Record<string, unknown>): void;
  registerService(service: AppService): void;
  registerHttpRoute(route: RouteRegistration): void;
  getPositions?(): Promise<unknown>;
  notify?(message: string): Promise<void>;
  on(event: string, handler: (...args: unknown[]) => unknown): void;
};
