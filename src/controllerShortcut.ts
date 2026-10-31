import { Router } from "@decky/ui";

import { togglePlayback } from "./api";
import { logFrontend } from "./logClient";

/**
 * Play/pause listens to SteamClient.Input.RegisterForControllerInputMessages.
 *
 * On Steam from Oct 31 2026 (Chrome/126) RegisterForControllerStateChanges is
 * gone. The input-message callback is invoked as
 * (controllerIndex, action, pressed), which a rest parameter sees as
 * [15, 45, true]. Action 45 was confirmed as the Deck's R4. Other names come
 * from ControllerInputGamepadButton in @decky/ui (RBACK_UPPER = 45).
 *
 * The removed state-changes callback is only a fallback if that build still
 * has it. In-game delivery and Bluetooth pads were not tested.
 */

const ACTION_NAME: Record<number, string> = {
  0: "A",
  1: "B",
  2: "X",
  3: "Y",
  4: "D-pad up",
  5: "D-pad right",
  6: "D-pad down",
  7: "D-pad left",
  8: "Menu",
  9: "View",
  25: "L3",
  28: "L2",
  29: "R2",
  30: "L1",
  31: "R1",
  32: "L5",
  33: "R5",
  34: "Steam",
  35: "Select",
  36: "Start",
  41: "R3",
  44: "L4",
  45: "R4",
};

const STEAM_ACTION = 34;

const IGNORED_ACTIONS = new Set<number>([
  10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
  20, 21, 22, 23, 24,
  26, 27,
  38, 40, 42, 43,
  47, 48, 49, 50,
]);

type ProgramListener = (actions: number[] | null, reason: "captured" | "timeout" | "cancelled") => void;

let binding: number[] = [];
const held = new Map<number, Set<number>>();
let programming = false;
let programListener: ProgramListener | null = null;
let programController: number | null = null;
let peak: number[] = [];
let gestureDown = false;
let settleTimer: number | null = null;
let programTimer: number | null = null;
let unregister: { unregister: () => void } | null = null;
let retryTimer: number | null = null;
let retries = 0;
let comboController: number | null = null;
let comboArmed = false;
let comboFired = false;
let holdTimer: number | null = null;
let qamSuppressed = false;
let lastFire = 0;
let lastCallbackLog = 0;
let callbackLogsThisMinute = 0;
let callbackMinuteStart = 0;
let registrationReport = "controller registration not started";
let activeApi = "none";

export function actionName(code: number): string {
  return ACTION_NAME[code] || `Button ${code}`;
}

export function bindingLabel(actions: number[]): string {
  if (!actions.length) return "Not set";
  return actions.map((code) => actionName(code)).join(" + ");
}

export function registrationStatus(): string {
  return registrationReport;
}

export function setShortcutButtons(actions: number[]): void {
  binding = actions.filter((code) => Number.isInteger(code) && code >= 0 && code <= 60 && !IGNORED_ACTIONS.has(code));
  comboController = null;
  comboArmed = false;
  comboFired = false;
  if (holdTimer !== null) {
    window.clearTimeout(holdTimer);
    holdTimer = null;
  }
  logDebug(`shortcut config actions=${binding.join(",") || "none"} label=${bindingLabel(binding)}`, true);
}

function logDebug(message: string, force = false): void {
  const now = Date.now();
  if (now - callbackMinuteStart > 60_000) {
    callbackMinuteStart = now;
    callbackLogsThisMinute = 0;
  }
  if (callbackLogsThisMinute >= 120) return;
  if (!force && now - lastCallbackLog < 200) return;
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

function finishProgramming(reason: "captured" | "timeout" | "cancelled", actions: number[]): void {
  if (!programming) return;
  programming = false;
  clearProgramTimers();
  const listener = programListener;
  programListener = null;
  programController = null;
  peak = [];
  gestureDown = false;
  listener?.(reason === "captured" ? actions : null, reason);
}

export function startProgramming(listener: ProgramListener): void {
  finishProgramming("cancelled", []);
  programming = true;
  programListener = listener;
  peak = [];
  gestureDown = false;
  programController = null;
  programTimer = window.setTimeout(() => finishProgramming("timeout", []), 8000);
  logDebug("program button listening", true);
}

export function cancelProgramming(): void {
  finishProgramming("cancelled", []);
}

function noteProgramming(controller: number, actions: number[]): void {
  if (programController !== null && controller !== programController) return;
  if (!actions.length) {
    if (gestureDown && peak.length > 0) finishProgramming("captured", peak);
    gestureDown = false;
    return;
  }
  programController = controller;
  gestureDown = true;
  if (actions.length >= peak.length) peak = actions.slice();
  if (settleTimer !== null) window.clearTimeout(settleTimer);
  const snapshot = actions.slice();
  settleTimer = window.setTimeout(() => {
    settleTimer = null;
    if (programming) finishProgramming("captured", snapshot);
  }, 500);
}

function suppressMenuIfNeeded(): void {
  if (qamSuppressed || !binding.includes(STEAM_ACTION)) return;
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
  logDebug(`fire actions=${binding.join(",")} label=${bindingLabel(binding)}`, true);
  try {
    const result = await togglePlayback();
    logDebug(`fire result ${JSON.stringify(result)}`, true);
  } catch (error) {
    logDebug(`fire threw ${error instanceof Error ? error.message : String(error)}`, true);
  }
}

function clearCombo(): void {
  comboArmed = false;
  comboFired = false;
  comboController = null;
  if (holdTimer !== null) {
    window.clearTimeout(holdTimer);
    holdTimer = null;
  }
  releaseMenu();
}

function handleHeld(controller: number, current: number[], risingActions: number[]): void {
  if (programming) {
    noteProgramming(controller, current);
    return;
  }
  if (binding.length === 0) return;
  if (binding.length === 1) {
    if (risingActions.includes(binding[0])) void fire();
    return;
  }
  const pressed = new Set(current);
  const complete = binding.every((code) => pressed.has(code));
  if (!complete) {
    if (comboController === controller) clearCombo();
    return;
  }
  if (comboController !== null && comboController !== controller) return;
  comboController = controller;
  if (comboArmed) return;
  comboArmed = true;
  if (holdTimer !== null) window.clearTimeout(holdTimer);
  holdTimer = window.setTimeout(() => {
    holdTimer = null;
    if (comboController !== controller || !comboArmed || comboFired) return;
    const still = held.get(controller);
    if (!still || !binding.every((code) => still.has(code))) return;
    comboFired = true;
    suppressMenuIfNeeded();
    void fire();
  }, 250);
}

function applyEvent(controller: number, action: number, pressed: boolean): void {
  if (!Number.isInteger(controller) || !Number.isInteger(action)) return;
  const line = `controller callback controller=${controller} action=${action} pressed=${pressed} name=${actionName(action)}`;
  // Analog directions and touches can stream. Button press/release pairs are
  // about 100 ms apart, so those lines are not throttled.
  logDebug(line, !IGNORED_ACTIONS.has(action));
  if (IGNORED_ACTIONS.has(action)) return;
  let set = held.get(controller);
  if (!set) {
    set = new Set();
    held.set(controller, set);
  }
  if (pressed) set.add(action);
  else set.delete(action);
  handleHeld(controller, [...set], pressed ? [action] : []);
}

function onInputMessage(...args: unknown[]): void {
  if (args.length >= 3 && typeof args[0] === "number" && typeof args[1] === "number") {
    applyEvent(args[0], args[1], Boolean(args[2]));
    return;
  }
  const first = args[0];
  if (Array.isArray(first) && first.length >= 3 && typeof first[0] === "number") {
    applyEvent(Number(first[0]), Number(first[1]), Boolean(first[2]));
    return;
  }
  if (Array.isArray(first)) {
    for (const item of first) {
      if (Array.isArray(item) && item.length >= 3) {
        applyEvent(Number(item[0]), Number(item[1]), Boolean(item[2]));
      }
    }
    return;
  }
  logDebug(`controller callback unrecognized args=${args.length} type0=${typeof first}`, true);
}

type SteamInput = {
  RegisterForControllerInputMessages?: unknown;
  RegisterForControllerStateChanges?: unknown;
};

function steamInput(): SteamInput | undefined {
  const fromWindow = (window as { SteamClient?: { Input?: SteamInput } }).SteamClient?.Input;
  if (fromWindow) return fromWindow;
  return (globalThis as { SteamClient?: { Input?: SteamInput } }).SteamClient?.Input;
}

export function startControllerShortcut(): void {
  if (unregister || retryTimer !== null) return;
  const input = steamInput();
  const messages = input?.RegisterForControllerInputMessages;
  const legacy = input?.RegisterForControllerStateChanges;
  const api = typeof messages === "function" ? "RegisterForControllerInputMessages" : typeof legacy === "function" ? "RegisterForControllerStateChanges" : "none";
  activeApi = api;
  registrationReport = `controller registration api=${api} windowSteamClient=${typeof (window as { SteamClient?: unknown }).SteamClient}`;
  logDebug(registrationReport, true);
  if (api === "none" || !input) {
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
    const register = (api === "RegisterForControllerInputMessages" ? messages : legacy) as (
      callback: (...args: unknown[]) => void,
    ) => { unregister: () => void };
    unregister = register.call(input, api === "RegisterForControllerInputMessages" ? onInputMessage : onLegacyState);
    registrationReport = `controller registration succeeded api=${api} unregister=${typeof unregister?.unregister}`;
    logDebug(registrationReport, true);
  } catch (error) {
    unregister = null;
    registrationReport = `controller registration threw ${error instanceof Error ? error.message : String(error)}`;
    logDebug(registrationReport, true);
  }
}

function onLegacyState(value: unknown): void {
  const changes = Array.isArray(value) ? value : value && typeof value === "object" ? [value] : [];
  const actions: number[] = [];
  for (const change of changes as { ulButtons?: unknown; ulUpperButtons?: unknown }[]) {
    const low = Number(change.ulButtons) >>> 0;
    const high = Number(change.ulUpperButtons) >>> 0;
    const pairs: [number, number, boolean][] = [
      [7, 0, false], [5, 1, false], [6, 2, false], [4, 3, false],
      [3, 30, false], [2, 31, false], [15, 32, false], [16, 33, false],
      [13, 34, false], [12, 35, false], [14, 36, false], [22, 25, false], [26, 41, false],
      [9, 44, true], [10, 45, true],
    ];
    for (const [bit, action, upper] of pairs) {
      const word = upper ? high : low;
      if ((word & (1 << bit)) !== 0 && !actions.includes(action)) actions.push(action);
    }
    logDebug(`controller legacy callback ulButtons=${String(change.ulButtons)} ulUpperButtons=${String(change.ulUpperButtons)} actions=${actions.join(",") || "none"}`);
  }
  const previous = held.get(-1) ?? new Set<number>();
  const rising = actions.filter((code) => !previous.has(code));
  held.set(-1, new Set(actions));
  handleHeld(-1, actions, rising);
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
  held.clear();
  cancelProgramming();
  releaseMenu();
}

export function replayRegistrationLog(): void {
  logDebug(registrationReport, true);
  logDebug(`controller api=${activeApi}`, true);
  logDebug(`shortcut config actions=${binding.join(",") || "none"} label=${bindingLabel(binding)}`, true);
}
