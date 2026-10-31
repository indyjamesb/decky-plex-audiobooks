import { Router } from "@decky/ui";

import { togglePlayback } from "./api";

/**
 * Button bits reported by SteamClient.Input.RegisterForControllerStateChanges.
 *
 * The low 32 are `ulButtons` and the next 32 are `ulUpperButtons`. L4 and R4
 * live in the upper word. This map is the one MagicPodsDecky uses
 * (github.com/steam3d/MagicPodsDecky src/input.tsx). Decky Recorder
 * (github.com/SDH-Stewardship/decky-recorder-fork) reads the same callback for
 * a Steam+Start shortcut while a recording is in progress.
 *
 * Whether those events still arrive while a game has focus, and whether an
 * external Steam Controller's paddles show up as L4/L5/R4/R5, is not verified
 * on hardware here.
 */

export const BACK_BUTTONS = ["L4", "L5", "R4", "R5"] as const;
export type BackButton = (typeof BACK_BUTTONS)[number];

export const COMBO_BUTTONS = [
  "Select",
  "Start",
  "Steam",
  "A",
  "B",
  "X",
  "Y",
  "L1",
  "R1",
  "L3",
  "R3",
  "L4",
  "L5",
  "R4",
  "R5",
] as const;

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

export type ShortcutConfig = {
  mode: "off" | "back" | "combo";
  backButton: BackButton;
  combo: string[];
};

type ControllerChange = {
  ulButtons?: number;
  ulUpperButtons?: number;
};

type HeldListener = (buttons: string[]) => void;

let config: ShortcutConfig = { mode: "off", backButton: "L4", combo: ["Select", "Y"] };
let recording = false;
let heldListener: HeldListener | null = null;
let unregister: { unregister: () => void } | null = null;
let backWasDown = false;
let comboWasComplete = false;
let comboFired = false;
let holdTimer: number | null = null;
let qamSuppressed = false;
let lastFire = 0;

export function pressedButtonNames(change: ControllerChange): string[] {
  const low = Number(change.ulButtons || 0) >>> 0;
  const high = Number(change.ulUpperButtons || 0) >>> 0;
  const names: string[] = [];
  for (const name of Object.keys(BIT)) {
    const bit = BIT[name];
    const down = bit < 32 ? (low & (1 << bit)) !== 0 : (high & (1 << (bit - 32))) !== 0;
    if (down) names.push(name);
  }
  return names;
}

export function setShortcutConfig(next: ShortcutConfig): void {
  config = {
    mode: next.mode,
    backButton: next.backButton,
    combo: next.combo.filter((name) => name in BIT),
  };
  backWasDown = false;
  comboWasComplete = false;
  comboFired = false;
  if (holdTimer !== null) {
    window.clearTimeout(holdTimer);
    holdTimer = null;
  }
}

export function setComboRecording(active: boolean, listener: HeldListener | null): void {
  recording = active;
  heldListener = listener;
}

function comboComplete(pressed: Set<string>): boolean {
  return config.combo.length >= 2 && config.combo.every((name) => pressed.has(name));
}

function suppressMenuIfNeeded(): void {
  if (qamSuppressed || !config.combo.includes("Steam")) return;
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
  try {
    await togglePlayback();
  } catch {
    // The backend reports its own error the next time the panel opens.
  }
}

function handlePressed(names: string[]): void {
  const pressed = new Set(names);
  if (recording) {
    if (names.length > 0) heldListener?.(names);
    return;
  }
  if (config.mode === "off") return;
  if (config.mode === "back") {
    const down = pressed.has(config.backButton);
    if (down && !backWasDown) void fire();
    backWasDown = down;
    return;
  }
  const complete = comboComplete(pressed);
  if (complete) {
    if (!comboWasComplete) {
      comboWasComplete = true;
      if (holdTimer !== null) window.clearTimeout(holdTimer);
      // State events are not a clock. Arm a short hold so a passing overlap
      // does not fire, and so we do not wait for another button event.
      holdTimer = window.setTimeout(() => {
        holdTimer = null;
        if (!comboWasComplete || comboFired) return;
        comboFired = true;
        suppressMenuIfNeeded();
        void fire();
      }, 250);
    }
    return;
  }
  comboWasComplete = false;
  comboFired = false;
  if (holdTimer !== null) {
    window.clearTimeout(holdTimer);
    holdTimer = null;
  }
  releaseMenu();
}

function onChanges(changes: ControllerChange[]): void {
  if (!Array.isArray(changes)) return;
  for (const change of changes) handlePressed(pressedButtonNames(change));
}

export function startControllerShortcut(): void {
  if (unregister) return;
  const steam = (globalThis as { SteamClient?: { Input?: { RegisterForControllerStateChanges?: unknown } } })
    .SteamClient;
  const register = steam?.Input?.RegisterForControllerStateChanges as
    | ((callback: (changes: ControllerChange[]) => void) => { unregister: () => void })
    | undefined;
  if (typeof register !== "function" || !steam?.Input) return;
  try {
    unregister = register.call(steam.Input, onChanges);
  } catch {
    unregister = null;
  }
}

export function stopControllerShortcut(): void {
  try {
    unregister?.unregister();
  } catch {
    // Already gone.
  }
  unregister = null;
  releaseMenu();
}
