import { callable } from "@decky/api";

export type LogLevelName = "error" | "warning" | "info" | "debug";

const RANK: Record<LogLevelName, number> = {
  error: 0,
  warning: 1,
  info: 2,
  debug: 3,
};

const logFrontendCall = callable<[level: string, message: string], { success: boolean; logged?: boolean }>(
  "log_frontend",
);

let current: LogLevelName = "info";

export function clientLogLevel(): LogLevelName {
  return current;
}

export function setClientLogLevel(level: string): void {
  current = level === "error" || level === "warning" || level === "debug" ? level : "info";
}

export function logFrontend(level: LogLevelName, message: string): void {
  if (RANK[level] > RANK[current]) return;
  const text = message.replace(/\s+/g, " ").slice(0, 500);
  if (!text) return;
  void logFrontendCall(level, text).catch(() => undefined);
}
