import { Router } from "@decky/ui";

import { togglePlayback } from "./api";
import { logFrontend } from "./logClient";

/**
 * Button bits from SteamClient.Input.RegisterForControllerStateChanges.
 *
 * MagicPodsDecky (src/input.tsx) and Decky Recorder both call
 * `SteamClient.Input.RegisterForControllerStateChanges(callback)` as a method
 * and keep the returned `{ unregister }`. The low 32 bits are `ulButtons`
 * and the next 32 are `ulUpperButtons`. L4 is upper bit 9 and R4 is upper
 * bit 10. Touchpad and stick touches are ignored, as MagicPods ignores them.
 *
 * 0.1.1 looked SteamClient up only on globalThis and returned with no log if
 * that failed, and it dropped any callback that was not an array. 0.1.2
 * follows Decky Recorder and uses `window.SteamClient`, retries briefly, and
 * accepts one change object as well as an array. That registration path is
 * not verified on a Deck.
 */

const BIT: Record<string, number> = {
  R2: 0,
  L2: 1,
  R1: 2,
  L1: 3,
  Y: 4,
  B: 5,
  X: 6,
  A: 7,
  Select: 12,
  Steam: 13,
  Start: 14,
  L5: 15,
  R5: 16,
  L3: 22,
  R3: 26,
  L4: 9 + 32,
  R4: 10 + 32,
};

const IGNORED = new Set([17, 19, 20, 14 + 32, 15 + 32]);

type ControllerChange = {
  ulButtons?: unknown;
  ulUpperButtons?: unknown;
};

type ProgramListener = (buttons: string[] | null, reason: "captured" | "timeout" | "cancelled") => void;

let buttons: string[] = [];
let programming = false;
let programListener: ProgramListener | null = null;
let peak: string[] = [];
let gestureDown = false;
let settleTimer: number | null = null;
let programTimer: number | null = null;
let unregister: { unregister: () => void } | null = null;
let retryTimer: number | null = null;
let retries = 0;
let backWasDown = false;
let comboArmed = false;
let comboFired = false;
let holdTimer: number | null = null;
let qamSuppressed = false;
let lastFire = 0;
let freshCallbacks = 8;
let lastCallbackLog = 0;
let callbackLogsThisMinute = 0;
let callbackMinuteStart = 0;
let registrationReport = "controller registration not started";

export function bindingLabel(names: string[]): string {
  if (!names.length) return "Not set";
  return names.join(" + ");
}

export function registrationStatus(): string {
  return registrationReport;
}

function asUint32(value: unknown): number {
  if (typeof value === "bigint") {
    return Number(value & 0xffffffffn) >>> 0;
  }
  if (typeof value === "string" && /^-?\d+$/.test(value.trim())) {
    try {
      return Number(BigInt(value.trim()) & 0xffffffffn) >>> 0;
    } catch {
      return 0;
    }
  }
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return number >>> 0;
}

function describeRaw(value: unknown): string {
  if (typeof value === "bigint") return `${value}n (bigint)`;
  if (value === undefined) return "undefined";
  if (value === null) return "null";
  return `${String(value)} (${typeof value})`;
}

export function pressedButtonNames(change: ControllerChange): string[] {
  const low = asUint32(change.ulButtons);
  const high = asUint32(change.ulUpperButtons);
  const names: string[] = [];
  for (const name of Object.keys(BIT)) {
    const bit = BIT[name];
    const down = bit < 32 ? (low & (1 << bit)) !== 0 : (high & (1 << (bit - 32))) !== 0;
    if (down) names.push(name);
  }
  return names;
}

function ignoredBits(change: ControllerChange): boolean {
  const low = asUint32(change.ulButtons);
  const high = asUint32(change.ulUpperButtons);
  for (const bit of IGNORED) {
    const down = bit < 32 ? (low & (1 << bit)) !== 0 : (high & (1 << (bit - 32))) !== 0;
    if (down) return true;
  }
  return false;
}

export function setShortcutButtons(next: string[]): void {
  buttons = next.filter((name) => name in BIT);
  backWasDown = false;
  comboArmed = false;
  comboFired = false;
  freshCallbacks = 8;
  if (holdTimer !== null) {
    window.clearTimeout(holdTimer);
    holdTimer = null;
  }
  logDebug(`shortcut config buttons=${bindingLabel(buttons)}`, true);
}

function logDebug(message: string, force = false): void {
  const now = Date.now();
  if (now - callbackMinuteStart > 60_000) {
    callbackMinuteStart = now;
    callbackLogsThisMinute = 0;
  }
  if (callbackLogsThisMinute >= 40) return;
  if (!force && now - lastCallbackLog < 250) return;
  lastCallbackLog = now;
  callbackLogsThisMinute += 1;
  logFrontend("debug", message);
}

function clearProgramTimers(): void {
  if (settleTimer !== null) window.clearTimeout(settleTimer);
  if (programTimer !== null) window.clearTimeout(programTimer);
  settleTimer = null;
  programTimer = null;
}

function finishProgramming(reason: "captured" | "timeout" | "cancelled", names: string[]): void {
  if (!programming) return;
  programming = false;
  clearProgramTimers();
  const listener = programListener;
  programListener = null;
  peak = [];
  gestureDown = false;
  listener?.(reason === "captured" ? names : null, reason);
}

export function startProgramming(listener: ProgramListener): void {
  finishProgramming("cancelled", []);
  programming = true;
  programListener = listener;
  peak = [];
  gestureDown = false;
  programTimer = window.setTimeout(() => finishProgramming("timeout", []), 8000);
  logDebug("program button listening", true);
}

export function cancelProgramming(): void {
  finishProgramming("cancelled", []);
}

function noteProgramming(names: string[]): void {
  if (!names.length) {
    if (gestureDown && peak.length > 0) finishProgramming("captured", peak);
    gestureDown = false;
    return;
  }
  gestureDown = true;
  if (names.length >= peak.length) peak = names.slice();
  if (settleTimer !== null) window.clearTimeout(settleTimer);
  const snapshot = names.slice();
  settleTimer = window.setTimeout(() => {
    settleTimer = null;
    if (programming) finishProgramming("captured", snapshot);
  }, 500);
}

function suppressMenuIfNeeded(): void {
  if (qamSuppressed || !buttons.includes("Steam")) return;
  try {
    Router.DisableHomeAndQuickAccessButtons();
    qamSuppressed = true;
  } catch {
    // The menu buttons stay as Steam left them.
  }
}

function releaseMenu(): void {
  if (!qamSuppressed) return;
  qamSuppressed = false;
  window.setTimeout(() => {
    try {
      Router.EnableHomeAndQuickAccessButtons();
    } catch {
      // Already enabled.
    }
  }, 300);
}

async function fire(): Promise<void> {
  const now = Date.now();
  if (now - lastFire < 400) return;
  lastFire = now;
  logDebug(`fire buttons=${bindingLabel(buttons)}`, true);
  try {
    const result = await togglePlayback();
    logDebug(`fire result ${JSON.stringify(result)}`, true);
  } catch (error) {
    logDebug(`fire threw ${error instanceof Error ? error.message : String(error)}`, true);
  }
}

function handlePressed(names: string[]): void {
  if (programming) {
    noteProgramming(names);
    return;
  }
  if (buttons.length === 0) return;
  const pressed = new Set(names);
  if (buttons.length === 1) {
    const down = pressed.has(buttons[0]);
    if (down && !backWasDown) void fire();
    backWasDown = down;
    return;
  }
  const complete = buttons.every((name) => pressed.has(name));
  if (complete) {
    if (!comboArmed) {
      comboArmed = true;
      if (holdTimer !== null) window.clearTimeout(holdTimer);
      holdTimer = window.setTimeout(() => {
        holdTimer = null;
        if (!comboArmed || comboFired) return;
        comboFired = true;
        suppressMenuIfNeeded();
        void fire();
      }, 250);
    }
    return;
  }
  comboArmed = false;
  comboFired = false;
  if (holdTimer !== null) {
    window.clearTimeout(holdTimer);
    holdTimer = null;
  }
  releaseMenu();
}

function onChanges(value: unknown): void {
  const changes = Array.isArray(value) ? value as ControllerChange[] : value && typeof value === "object" ? [value as ControllerChange] : [];
  const interesting = changes.some((change) => asUint32(change.ulButtons) !== 0 || asUint32(change.ulUpperButtons) !== 0);
  if (freshCallbacks > 0 || interesting) {
    freshCallbacks = Math.max(0, freshCallbacks - 1);
    const first = changes[0];
    logDebug(
      `controller callback n=${changes.length} arg=${Array.isArray(value) ? "array" : typeof value} ` +
      `ulButtons=${first ? describeRaw(first.ulButtons) : "none"} ` +
      `ulUpperButtons=${first ? describeRaw(first.ulUpperButtons) : "none"} ` +
      `names=${changes.flatMap((change) => pressedButtonNames(change)).join(",") || "none"}` +
      `${changes.some((change) => ignoredBits(change)) ? " ignoredTouch=1" : ""}`,
    );
  }
  for (const change of changes) handlePressed(pressedButtonNames(change));
}

function steamInput(): { RegisterForControllerStateChanges?: unknown } | undefined {
  const fromWindow = (window as { SteamClient?: { Input?: { RegisterForControllerStateChanges?: unknown } } }).SteamClient?.Input;
  if (fromWindow) return fromWindow;
  return (globalThis as { SteamClient?: { Input?: { RegisterForControllerStateChanges?: unknown } } }).SteamClient?.Input;
}

export function startControllerShortcut(): void {
  if (unregister || retryTimer !== null) return;
  const input = steamInput();
  const register = input?.RegisterForControllerStateChanges;
  registrationReport = `controller registration exists=${typeof register} windowSteamClient=${typeof (window as { SteamClient?: unknown }).SteamClient}`;
  logDebug(registrationReport, true);
  if (typeof register !== "function" || !input) {
    if (retries < 10) {
      retries += 1;
      logDebug(`controller registration retry ${retries}`, true);
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        startControllerShortcut();
      }, 500);
    }
    return;
  }
  try {
    unregister = (register as (callback: (value: unknown) => void) => { unregister: () => void }).call(input, onChanges);
    registrationReport = `controller registration succeeded unregister=${typeof unregister?.unregister}`;
    logDebug(registrationReport, true);
  } catch (error) {
    unregister = null;
    registrationReport = `controller registration threw ${error instanceof Error ? error.message : String(error)}`;
    logFrontend("warning", registrationReport);
  }
}

export function stopControllerShortcut(): void {
  if (retryTimer !== null) window.clearTimeout(retryTimer);
  retryTimer = null;
  try {
    unregister?.unregister();
  } catch {
    // Already gone.
  }
  unregister = null;
  cancelProgramming();
  releaseMenu();
}

export function replayRegistrationLog(): void {
  freshCallbacks = 8;
  logDebug(registrationReport, true);
  logDebug(`shortcut config buttons=${bindingLabel(buttons)}`, true);
}
