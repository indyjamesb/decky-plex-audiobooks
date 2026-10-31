import { Router } from "@decky/ui";

import { hidrawStatus, shortcutToggle } from "./api";
import { logFrontend } from "./logClient";

/**
 * Steam UI listener. RegisterForControllerInputMessages only runs while the
 * Steam UI has focus, so it does not see in-game presses. When the backend
 * hidraw reader is open, this path logs buttons but does not toggle; the
 * backend toggles, and both share one 400 ms debounce if they overlap.
 *
 * Program button, when hidraw is unavailable: ignore every held button
 * (including the A press that opened Program) until all are released, then
 * record the next gesture.
 */

const ACTION_NAME: Record<number, string> = {
  0: "A",
  1: "B",
  2: "X",
  3: "Y",
  4: "Up",
  5: "Right",
  6: "Down",
  7: "Left",
  8: "Start",
  9: "View",
  25: "L3",
  28: "L2",
  29: "R2",
  30: "L1",
  31: "R1",
  32: "L5",
  33: "R5",
  34: "Steam",
  35: "View",
  36: "Start",
  41: "R3",
  44: "L4",
  45: "R4",
};

const BUTTON_ORDER = [
  "R2", "L2", "R1", "L1", "Y", "B", "X", "A",
  "Up", "Right", "Left", "Down", "View", "Steam", "Start",
  "L5", "R5", "LPad", "RPad", "L3", "R3", "L4", "R4", "QAM",
  "XBOX_A", "XBOX_B", "XBOX_X", "XBOX_Y", "XBOX_LB", "XBOX_RB", "XBOX_VIEW", "XBOX_MENU", "XBOX_SHARE",
  "DS_SQUARE", "DS_CROSS", "DS_CIRCLE", "DS_TRIANGLE", "DS_L1", "DS_R1", "DS_CREATE", "DS_OPTIONS",
  "DS_PS", "DS_TOUCH", "DS_MUTE",
  "NS_Y", "NS_X", "NS_B", "NS_A", "NS_R", "NS_ZR", "NS_MINUS", "NS_PLUS", "NS_HOME", "NS_CAPTURE",
  "NS_L", "NS_ZL",
  "EV_SOUTH", "EV_EAST", "EV_NORTH", "EV_WEST", "EV_TL", "EV_TR", "EV_TL2", "EV_TR2",
  "EV_SELECT", "EV_START", "EV_MODE", "EV_THUMBL", "EV_THUMBR",
];

const FRIENDLY: Record<string, string> = {
  R4: "Steam Controller right back / Deck R4",
  L4: "Steam Controller left back / Deck L4",
  XBOX_A: "Xbox A",
  XBOX_B: "Xbox B",
  XBOX_X: "Xbox X",
  XBOX_Y: "Xbox Y",
  XBOX_LB: "Xbox LB",
  XBOX_RB: "Xbox RB",
  XBOX_VIEW: "Xbox View",
  XBOX_MENU: "Xbox Menu",
  XBOX_SHARE: "Xbox Share",
  DS_SQUARE: "DualSense Square",
  DS_CROSS: "DualSense Cross",
  DS_CIRCLE: "DualSense Circle",
  DS_TRIANGLE: "DualSense Triangle",
  DS_L1: "DualSense L1",
  DS_R1: "DualSense R1",
  DS_CREATE: "DualSense Create",
  DS_OPTIONS: "DualSense Options",
  DS_PS: "DualSense PS",
  DS_TOUCH: "DualSense Touchpad",
  DS_MUTE: "DualSense Mute",
  NS_Y: "Switch Y",
  NS_X: "Switch X",
  NS_B: "Switch B",
  NS_A: "Switch A",
  NS_R: "Switch R",
  NS_ZR: "Switch ZR",
  NS_MINUS: "Switch Minus",
  NS_PLUS: "Switch Plus",
  NS_HOME: "Switch Home",
  NS_CAPTURE: "Switch Capture",
  NS_L: "Switch L",
  NS_ZL: "Switch ZL",
  EV_SOUTH: "Gamepad A",
  EV_EAST: "Gamepad B",
  EV_NORTH: "Gamepad X",
  EV_WEST: "Gamepad Y",
  EV_TL: "Gamepad LB",
  EV_TR: "Gamepad RB",
  EV_TL2: "Gamepad LT",
  EV_TR2: "Gamepad RT",
  EV_SELECT: "Gamepad View",
  EV_START: "Gamepad Menu",
  EV_MODE: "Gamepad Guide",
  EV_THUMBL: "Gamepad L3",
  EV_THUMBR: "Gamepad R3",
};

const KNOWN = new Set(BUTTON_ORDER);

const IGNORED_ACTIONS = new Set<number>([
  10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
  20, 21, 22, 23, 24,
  26, 27,
  38, 40, 42, 43,
  47, 48, 49, 50,
]);

type CapturePhase = "idle" | "wait_release" | "listen" | "done" | "timeout" | "cancelled";
type ProgramListener = (buttons: string[] | null, reason: "captured" | "timeout" | "cancelled") => void;

let binding: string[] = [];
const held = new Map<number, Set<number>>();
const prevNames = new Map<number, Set<string>>();
let hidrawActive = false;
let hidrawTimer: number | null = null;
let capPhase: CapturePhase = "idle";
let capButtons: string[] = [];
let capStarted = 0;
let capFirst: number | null = null;
let capTimer: number | null = null;
let capListener: ProgramListener | null = null;
let unregister: { unregister: () => void } | null = null;
let retryTimer: number | null = null;
let retries = 0;
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

export function bindingLabel(buttons: string[]): string {
  if (!buttons.length) return "Not set";
  return orderButtons(buttons).map((name) => FRIENDLY[name] || name).join(" + ");
}

export function registrationStatus(): string {
  return registrationReport;
}

export function hidrawIsActive(): boolean {
  return hidrawActive;
}

function orderButtons(buttons: string[]): string[] {
  const rank = new Map(BUTTON_ORDER.map((name, index) => [name, index]));
  return [...new Set(buttons)].sort((a, b) => (rank.get(a) ?? 100) - (rank.get(b) ?? 100));
}

export function setShortcutButtons(buttons: string[]): void {
  binding = orderButtons(buttons.filter((name) => KNOWN.has(name)));
  prevNames.clear();
  for (const [controller, actions] of held) {
    prevNames.set(controller, namesFrom(actions));
  }
  logDebug(`shortcut config buttons=${binding.join("+") || "none"} label=${bindingLabel(binding)}`, true);
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

function namesFrom(actions: Set<number>): Set<string> {
  const names = new Set<string>();
  for (const code of actions) {
    const name = ACTION_NAME[code];
    if (name) names.add(name);
  }
  return names;
}

export function captureSnapshot(): { phase: CapturePhase; buttons: string[]; label: string; seconds_left: number | null } {
  const now = Date.now();
  if ((capPhase === "wait_release" || capPhase === "listen") && capButtons.length === 0 && now - capStarted >= 8000) {
    finishCapture("timeout");
  }
  if (capPhase === "listen" && capFirst !== null && now - capFirst >= 1500) {
    finishCapture("captured");
  }
  const secondsLeft = capPhase === "listen" && capFirst !== null ? Math.max(0, 1.5 - (now - capFirst) / 1000) : null;
  return {
    phase: capPhase,
    buttons: capButtons.slice(),
    label: capButtons.join(" + "),
    seconds_left: secondsLeft,
  };
}

function finishCapture(reason: "captured" | "timeout" | "cancelled"): void {
  if (capPhase === "idle" || capPhase === "done" || capPhase === "timeout" || capPhase === "cancelled") return;
  const buttons = reason === "captured" ? capButtons.slice() : [];
  capPhase = reason === "captured" ? "done" : reason;
  if (capTimer !== null) window.clearTimeout(capTimer);
  capTimer = null;
  const listener = capListener;
  capListener = null;
  listener?.(reason === "captured" ? buttons : null, reason);
}

export function startProgramming(listener: ProgramListener): void {
  finishCapture("cancelled");
  capPhase = "wait_release";
  capButtons = [];
  capFirst = null;
  capStarted = Date.now();
  capListener = listener;
  capTimer = window.setTimeout(() => finishCapture("timeout"), 8000);
  logDebug("program button listening", true);
}

export function cancelProgramming(): void {
  finishCapture("cancelled");
}

function feedCapture(names: Set<string>): void {
  if (capPhase === "wait_release") {
    if (names.size === 0) capPhase = "listen";
    return;
  }
  if (capPhase !== "listen") return;
  if (names.size > 0) {
    if (capFirst === null) capFirst = Date.now();
    for (const name of names) {
      if (!capButtons.includes(name)) capButtons.push(name);
    }
    capButtons = orderButtons(capButtons);
  }
  if (capFirst !== null && Date.now() - capFirst >= 1500) finishCapture("captured");
}

function suppressMenuIfNeeded(): void {
  if (qamSuppressed || !binding.includes("Steam")) return;
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
  if (hidrawActive) return;
  const now = Date.now();
  if (now - lastFire < 400) return;
  lastFire = now;
  logDebug(`fire buttons=${binding.join("+")} label=${bindingLabel(binding)}`, true);
  try {
    const result = await shortcutToggle();
    logDebug(`fire result ${JSON.stringify(result)}`, true);
  } catch (error) {
    logDebug(`fire threw ${error instanceof Error ? error.message : String(error)}`, true);
  }
}

function handleHeld(controller: number, names: Set<string>): void {
  const prev = prevNames.get(controller) ?? new Set<string>();
  prevNames.set(controller, names);
  const steamNames = new Set(Object.values(ACTION_NAME));
  const wanted = binding.filter((name) => steamNames.has(name));
  if (hidrawActive || wanted.length === 0 || wanted.length !== binding.length) {
    if (!names.has("Steam")) releaseMenu();
    return;
  }
  const was = wanted.every((name) => prev.has(name));
  const down = wanted.every((name) => names.has(name));
  if (down && !was) {
    suppressMenuIfNeeded();
    void fire();
  }
  if (!names.has("Steam")) releaseMenu();
}

function applyEvent(controller: number, action: number, pressed: boolean): void {
  if (!Number.isInteger(controller) || !Number.isInteger(action)) return;
  logDebug(
    `controller callback controller=${controller} action=${action} pressed=${pressed} name=${actionName(action)}`,
    !IGNORED_ACTIONS.has(action),
  );
  if (IGNORED_ACTIONS.has(action)) return;
  let set = held.get(controller);
  if (!set) {
    set = new Set();
    held.set(controller, set);
  }
  if (pressed) set.add(action);
  else set.delete(action);
  if (capPhase === "wait_release" || capPhase === "listen") {
    const union = new Set<string>();
    for (const actions of held.values()) {
      for (const name of namesFrom(actions)) union.add(name);
    }
    feedCapture(union);
    return;
  }
  handleHeld(controller, namesFrom(set));
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

function refreshHidraw(): void {
  void hidrawStatus()
    .then((status) => {
      hidrawActive = Boolean(status.active);
    })
    .catch(() => undefined);
}

export function startControllerShortcut(): void {
  if (hidrawTimer === null) {
    refreshHidraw();
    hidrawTimer = window.setInterval(refreshHidraw, 2000);
  }
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
  held.set(-1, new Set(actions));
  const names = namesFrom(new Set(actions));
  if (capPhase === "wait_release" || capPhase === "listen") {
    feedCapture(names);
    return;
  }
  handleHeld(-1, names);
}

export function stopControllerShortcut(): void {
  if (hidrawTimer !== null) window.clearInterval(hidrawTimer);
  hidrawTimer = null;
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
  logDebug(`controller api=${activeApi} hidraw=${hidrawActive}`, true);
  logDebug(`shortcut config buttons=${binding.join("+") || "none"} label=${bindingLabel(binding)}`, true);
}
