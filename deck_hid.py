"""Steam Deck built-in controller, read from /dev/hidraw.

In-game button events do not reach SteamClient.Input. The Deck's controller
still publishes 64-byte HID reports. Linux documents that layout in
drivers/hid/hid-steam.c (ID_CONTROLLER_DECK_STATE = 9, the comment above
steam_do_deck_input_event): byte 2 is the message type, and bytes 8-15 are
a little-endian button bitmask. SDL's SteamDeckButtons in
src/joystick/hidapi/SDL_hidapi_steamdeck.c uses the same masks, split into
the low and high 32 bits of that u64 (ulButtonsL / ulButtonsH).

Device-verified on a Deck in a game (Oct 31 2026): L4 is byte 13 bit 0x02
and R4 is byte 13 bit 0x04. Byte 13 bit 0x80 was also seen and is the
right-stick touch bit (hid-steam.c 13.7); touch bits are ignored. Every
other name below is from those two sources and was not pressed in that
capture.

TODO: Bluetooth Xbox and PlayStation pads are evdev devices, not this
28DE:1205 hidraw node. Out of scope for 0.1.4.
"""

from __future__ import annotations

import os
import select
import threading
import time

DECK_VENDOR = "28DE"
DECK_PRODUCT = "1205"
REPORT_LEN = 64
DECK_INPUT_TYPE = 0x09
BUTTON_OFFSET = 8
DEBOUNCE_S = 0.4
CAPTURE_HOLD_S = 2.0
CAPTURE_OVERALL_S = 8.0
RESCAN_S = 3.0
SELECT_TIMEOUT_S = 0.25


def _bit(byte: int, bit: int) -> int:
    """Mask inside the u64 at report bytes 8-15. `byte` is the report offset."""

    return 1 << ((byte - BUTTON_OFFSET) * 8 + bit)


# (name, mask, device_verified). Order is the order shown for a combo.
# hid-steam.c calls bytes 9.0-9.3 left-pad directions; SDL calls them the d-pad.
BUTTONS: tuple[tuple[str, int, bool], ...] = (
    ("R2", _bit(8, 0), False),
    ("L2", _bit(8, 1), False),
    ("R1", _bit(8, 2), False),
    ("L1", _bit(8, 3), False),
    ("Y", _bit(8, 4), False),
    ("B", _bit(8, 5), False),
    ("X", _bit(8, 6), False),
    ("A", _bit(8, 7), False),
    ("Up", _bit(9, 0), False),
    ("Right", _bit(9, 1), False),
    ("Left", _bit(9, 2), False),
    ("Down", _bit(9, 3), False),
    ("View", _bit(9, 4), False),
    ("Steam", _bit(9, 5), False),
    ("Start", _bit(9, 6), False),
    ("L5", _bit(9, 7), False),
    ("R5", _bit(10, 0), False),
    ("LPad", _bit(10, 1), False),
    ("RPad", _bit(10, 2), False),
    ("L3", _bit(10, 6), False),
    ("R3", _bit(11, 2), False),
    ("L4", _bit(13, 1), True),
    ("R4", _bit(13, 2), True),
    ("QAM", _bit(14, 2), False),
)

# Capacitive bits. Not shortcut buttons. 13.7 was seen on device (d[13] & 0x80).
TOUCH_BITS = (
    _bit(10, 3),  # left pad touched
    _bit(10, 4),  # right pad touched
    _bit(13, 6),  # left stick touched
    _bit(13, 7),  # right stick touched
)

BUTTON_ORDER = [name for name, _mask, _verified in BUTTONS]
_BUTTON_RANK = {name: index for index, name in enumerate(BUTTON_ORDER)}
_BUTTON_MASK = {name: mask for name, mask, _verified in BUTTONS}

# 0.1.3 ControllerInputGamepadButton codes. 45 was confirmed as R4 on the
# Steam menu; the rest follow that enum and were not re-checked on hidraw.
ACTION_TO_BUTTON = {
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
}

ALIASES = {
    "Select": "View",
    "Menu": "Start",
    "D-pad up": "Up",
    "D-pad right": "Right",
    "D-pad down": "Down",
    "D-pad left": "Left",
}


def order_buttons(names: set[str] | list[str]) -> list[str]:
    return sorted(set(names), key=lambda name: _BUTTON_RANK.get(name, 100))


def decode_report(data: bytes) -> set[str] | None:
    """Button names in one report. None when it is not a Deck input report."""

    if len(data) < BUTTON_OFFSET + 8 or data[2] != DECK_INPUT_TYPE:
        return None
    bits = int.from_bytes(data[BUTTON_OFFSET : BUTTON_OFFSET + 8], "little")
    for touch in TOUCH_BITS:
        bits &= ~touch
    return {name for name, mask, _verified in BUTTONS if bits & mask}


def report_with_buttons(button_hex: str) -> bytes:
    """64-byte type-0x09 report whose bytes 8-15 are `button_hex` (16 hex digits)."""

    raw = bytes.fromhex(button_hex)
    if len(raw) != 8:
        raise ValueError("button field is 8 bytes")
    data = bytearray(REPORT_LEN)
    data[0] = 0x01
    data[1] = 0x00
    data[2] = DECK_INPUT_TYPE
    data[BUTTON_OFFSET : BUTTON_OFFSET + 8] = raw
    return bytes(data)


def normalize_buttons(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    found: list[str] = []
    for item in items:
        if isinstance(item, bool):
            continue
        name: str | None
        if isinstance(item, int):
            name = ACTION_TO_BUTTON.get(item)
        else:
            text = str(item).strip()
            if text.lstrip("-").isdigit():
                name = ACTION_TO_BUTTON.get(int(text))
            else:
                name = ALIASES.get(text, text)
                if name not in _BUTTON_MASK:
                    name = None
        if name and name not in found:
            found.append(name)
    return order_buttons(found)


def migrate_shortcut(settings: dict) -> list[str]:
    """Hidraw button names for the play/pause shortcut.

    A 0.1.4 file is marked ``play_pause_source=hidraw`` and an empty list
    stays off. Older files migrate: action code 45 becomes R4, and a missing
    or empty shortcut becomes the new default, R4.
    """

    if settings.get("play_pause_source") == "hidraw":
        return normalize_buttons(settings.get("play_pause_buttons"))
    if isinstance(settings.get("play_pause_actions"), list):
        return normalize_buttons(settings.get("play_pause_actions")) or ["R4"]
    if isinstance(settings.get("play_pause_buttons"), list):
        return normalize_buttons(settings.get("play_pause_buttons")) or ["R4"]
    mode = str(settings.get("play_pause_mode") or "")
    if mode == "back":
        return normalize_buttons([settings.get("play_pause_back")]) or ["R4"]
    if mode == "combo":
        return normalize_buttons(settings.get("play_pause_combo") or []) or ["R4"]
    return ["R4"]


class ShortcutEdge:
    """Fire when the configured buttons newly become held together.

    One name fires on its press. Several names fire when the last one goes
    down while the others are already down. A repeat inside `debounce_s` is
    ignored. `observe` updates the held set without firing, used while the
    Program button is capturing.
    """

    def __init__(self, buttons: list[str] | None = None, debounce_s: float = DEBOUNCE_S):
        self.buttons = order_buttons(buttons or [])
        self.debounce_s = debounce_s
        self._prev: set[str] = set()
        self._last_fire = -1e9

    def set_buttons(self, buttons: list[str]) -> None:
        self.buttons = order_buttons(buttons)
        self._prev = set()

    def observe(self, held: set[str]) -> None:
        self._prev = set(held)

    def feed(self, held: set[str], now: float) -> bool:
        want = set(self.buttons)
        if not want:
            self._prev = set(held)
            return False
        was = want.issubset(self._prev)
        down = want.issubset(held)
        self._prev = set(held)
        if not down or was:
            return False
        if now - self._last_fire < self.debounce_s:
            return False
        self._last_fire = now
        return True


class ButtonCapture:
    """Record the next gesture after every button has been released.

    The A press that opens Program is still down at the start, so the first
    phase waits until the held set is empty. The gesture ends when every
    button is released again, or 2 seconds after the first press.
    """

    def __init__(self, now: float):
        self.phase = "wait_release"
        self.buttons: list[str] = []
        self.started = now
        self.first_press: float | None = None
        self._seen: set[str] = set()

    def cancel(self) -> None:
        if self.phase not in ("done", "timeout", "cancelled"):
            self.phase = "cancelled"

    def feed(self, held: set[str], now: float) -> None:
        self._advance(now)
        if self.phase == "wait_release":
            if not held:
                self.phase = "listen"
            return
        if self.phase != "listen":
            return
        if held:
            if self.first_press is None:
                self.first_press = now
            self._seen.update(held)
            self.buttons = order_buttons(self._seen)
            self._advance(now)
            return
        if self.first_press is not None and self._seen:
            self.phase = "done"

    def _advance(self, now: float) -> None:
        if self.phase in ("wait_release", "listen") and not self._seen and now - self.started >= CAPTURE_OVERALL_S:
            self.phase = "timeout"
            return
        if self.phase == "listen" and self.first_press is not None and now - self.first_press >= CAPTURE_HOLD_S:
            self.phase = "done"

    def status(self, now: float) -> dict:
        self._advance(now)
        seconds_left = None
        if self.phase == "listen" and self.first_press is not None:
            seconds_left = max(0.0, CAPTURE_HOLD_S - (now - self.first_press))
        return {
            "phase": self.phase,
            "buttons": list(self.buttons),
            "label": " + ".join(self.buttons),
            "seconds_left": seconds_left,
        }


def list_deck_hidraw(sysfs_root: str = "/sys/class/hidraw") -> list[str]:
    """hidraw device paths whose uevent HID_ID is the Deck controller (28DE:1205)."""

    if not os.path.isdir(sysfs_root):
        return []
    found: list[str] = []
    for name in sorted(os.listdir(sysfs_root)):
        if not name.startswith("hidraw"):
            continue
        uevent_path = os.path.join(sysfs_root, name, "device", "uevent")
        try:
            with open(uevent_path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read().upper()
        except OSError:
            continue
        # uevent line: HID_ID=0003:000028DE:00001205
        if f"{DECK_VENDOR}:0000{DECK_PRODUCT}" in text:
            found.append(os.path.join("/dev", name))
    return found


class DeckHidWatcher:
    """select() loop over every Deck hidraw node. Cheap when idle.

    Steam keeps its own reader; opening the same nodes read-only does not
    stop it. Nodes are scanned again every few seconds and after an open or
    read failure, so a late controller still gets picked up.
    """

    def __init__(self, on_fire, log_debug, sysfs_root: str = "/sys/class/hidraw"):
        self._on_fire = on_fire
        self._log = log_debug
        self._sysfs = sysfs_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._edge = ShortcutEdge(["R4"])
        self._capture: ButtonCapture | None = None
        self._held: set[str] = set()
        self.active = False
        self.nodes: list[str] = []
        self._announced: list[str] | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="deck-hidraw", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.5)
        self._thread = None
        self.active = False

    def set_shortcut(self, buttons: list[str]) -> None:
        with self._lock:
            self._edge.set_buttons(buttons)
            self._edge.observe(self._held)

    def start_capture(self) -> dict:
        with self._lock:
            self._capture = ButtonCapture(time.monotonic())
            return self._capture_status_locked()

    def cancel_capture(self) -> None:
        with self._lock:
            if self._capture is not None:
                self._capture.cancel()

    def capture_state(self) -> dict:
        with self._lock:
            return self._capture_status_locked()

    def _capture_status_locked(self) -> dict:
        if self._capture is None:
            return {
                "phase": "idle",
                "buttons": [],
                "label": "",
                "seconds_left": None,
                "hidraw": self.active,
            }
        state = self._capture.status(time.monotonic())
        state["hidraw"] = self.active
        return state

    def _run(self) -> None:
        open_fds: dict[int, str] = {}
        per_fd: dict[int, set[str]] = {}
        logged_input: set[str] = set()
        next_scan = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_scan:
                    open_fds, per_fd = self._rescan(open_fds, per_fd)
                    next_scan = time.monotonic() + RESCAN_S
                if not open_fds:
                    self.active = False
                    self.nodes = []
                    self._stop.wait(0.5)
                    continue
                self.active = True
                try:
                    readable, _, _ = select.select(list(open_fds), [], [], SELECT_TIMEOUT_S)
                except (OSError, ValueError):
                    open_fds, per_fd = self._close_all(open_fds, per_fd)
                    next_scan = 0.0
                    continue
                for fd in readable:
                    path = open_fds.get(fd)
                    if path is None:
                        continue
                    try:
                        data = os.read(fd, REPORT_LEN)
                    except OSError:
                        self._drop_fd(fd, open_fds, per_fd)
                        if not open_fds:
                            self._publish(set())
                        next_scan = 0.0
                        continue
                    if not data:
                        self._drop_fd(fd, open_fds, per_fd)
                        if not open_fds:
                            self._publish(set())
                        next_scan = 0.0
                        continue
                    decoded = decode_report(data)
                    if decoded is None:
                        continue
                    if path not in logged_input:
                        logged_input.add(path)
                        self._log(f"hidraw input {os.path.basename(path)}")
                    per_fd[fd] = decoded
                    self._publish(set().union(*per_fd.values()) if per_fd else set())
        finally:
            self._close_all(open_fds, per_fd)
            self.active = False

    def _publish(self, held: set[str]) -> None:
        with self._lock:
            if held != self._held:
                self._log("hidraw buttons " + ("+".join(order_buttons(held)) or "none"))
                self._held = set(held)
            capturing = self._capture is not None and self._capture.phase in ("wait_release", "listen")
            if capturing and self._capture is not None:
                self._capture.feed(held, time.monotonic())
                self._edge.observe(held)
                return
            fired = self._edge.feed(held, time.monotonic())
        if fired and not self._stop.is_set():
            with self._lock:
                label = "+".join(self._edge.buttons) or "shortcut"
            self._log(f"hidraw shortcut fire {label}")
            self._on_fire()

    def _rescan(self, open_fds: dict[int, str], per_fd: dict[int, set[str]]) -> tuple[dict[int, str], dict[int, set[str]]]:
        wanted = list_deck_hidraw(self._sysfs)
        by_path = {path: fd for fd, path in open_fds.items()}
        for path, fd in list(by_path.items()):
            if path not in wanted:
                self._drop_fd(fd, open_fds, per_fd)
                by_path.pop(path, None)
        for path in wanted:
            if path in by_path:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as err:
                self._log(f"hidraw open failed {os.path.basename(path)}: {err.__class__.__name__}")
                continue
            open_fds[fd] = path
            by_path[path] = fd
        names = [os.path.basename(path) for path in sorted(by_path)]
        self.nodes = names
        if names != self._announced:
            self._announced = names
            self._log("hidraw nodes " + (" ".join(names) or "none"))
        self.active = bool(open_fds)
        return open_fds, per_fd

    def _drop_fd(self, fd: int, open_fds: dict[int, str], per_fd: dict[int, set[str]]) -> None:
        open_fds.pop(fd, None)
        per_fd.pop(fd, None)
        try:
            os.close(fd)
        except OSError:
            pass

    def _close_all(self, open_fds: dict[int, str], per_fd: dict[int, set[str]]) -> tuple[dict[int, str], dict[int, set[str]]]:
        for fd in list(open_fds):
            self._drop_fd(fd, open_fds, per_fd)
        return {}, {}
