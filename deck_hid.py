"""Controller buttons from hidraw, plus a generic evdev fallback.

In-game button events do not reach SteamClient.Input. Devices are matched
by USB vendor and product from the hidraw uevent ``HID_ID``, never by the
hidraw number, which changes as devices connect. Bus 0003 is USB and 0005
is Bluetooth; both are accepted.

Deck reports follow Linux drivers/hid/hid-steam.c (ID_CONTROLLER_DECK_STATE
= 9) and SDL's SteamDeckButtons in SDL_hidapi_steamdeck.c. Device-verified
on a Deck (Oct 31 2026): L4 is byte 13 bit 0x02 and R4 is byte 13 bit 0x04.
Byte 13 bit 0x80 was seen and is the right-stick touch bit; touch bits are
ignored. Other Deck names are from those sources and were not pressed in
that capture.

Also device-verified that day, and decoded only for the bits that were
captured: Steam Controller wireless puck (28DE:1304, report 0x42), Xbox
Wireless Controller over Bluetooth (045E:0B13, report 0x01), DualSense over
Bluetooth (054C:0CE6, report 0x31), and Switch Pro Controller over USB
(057E:2009, report 0x30). DualSense USB (report 0x01, buttons one byte
earlier) and Switch Pro over Bluetooth (same 0x30 report) are implemented
from those layouts and were not captured. The generic evdev path was not
tried on hardware.

R4 is both the Deck R4 and the Steam Controller right back button. L4 is
both the Deck L4 and the Steam Controller left back button.
"""

from __future__ import annotations

import os
import re
import select
import struct
import threading
import time

DECK_VENDOR = "28DE"
DECK_PRODUCT = "1205"
REPORT_LEN = 256
DECK_INPUT_TYPE = 0x09
BUTTON_OFFSET = 8
DEBOUNCE_S = 0.4
CAPTURE_HOLD_S = 1.5
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

# Names after the Deck list are other controllers. R4 and L4 are shared with
# the Steam Controller's back buttons, so the default ["R4"] fires from either.
EXTRA_BUTTONS = (
    "XBOX_A", "XBOX_B", "XBOX_X", "XBOX_Y", "XBOX_LB", "XBOX_RB", "XBOX_VIEW", "XBOX_MENU", "XBOX_SHARE",
    "DS_SQUARE", "DS_CROSS", "DS_CIRCLE", "DS_TRIANGLE", "DS_L1", "DS_R1", "DS_CREATE", "DS_OPTIONS",
    "DS_PS", "DS_TOUCH", "DS_MUTE",
    "NS_Y", "NS_X", "NS_B", "NS_A", "NS_R", "NS_ZR", "NS_MINUS", "NS_PLUS", "NS_HOME", "NS_CAPTURE",
    "NS_L", "NS_ZL",
    "EV_SOUTH", "EV_EAST", "EV_NORTH", "EV_WEST", "EV_TL", "EV_TR", "EV_TL2", "EV_TR2",
    "EV_SELECT", "EV_START", "EV_MODE", "EV_THUMBL", "EV_THUMBR",
)
BUTTON_ORDER = [name for name, _mask, _verified in BUTTONS] + list(EXTRA_BUTTONS)
_BUTTON_RANK = {name: index for index, name in enumerate(BUTTON_ORDER)}
_BUTTON_MASK = {name: mask for name, mask, _verified in BUTTONS}
LOGICAL_BUTTONS = set(BUTTON_ORDER)

# Shown in the Quick Access panel. Logical names stay in settings.
FRIENDLY = {
    "R4": "Steam Controller right back / Deck R4",
    "L4": "Steam Controller left back / Deck L4",
    "A": "Deck A",
    "B": "Deck B",
    "X": "Deck X",
    "Y": "Deck Y",
    "L1": "Deck L1",
    "R1": "Deck R1",
    "L2": "Deck L2",
    "R2": "Deck R2",
    "L3": "Deck L3",
    "R3": "Deck R3",
    "L5": "Deck L5",
    "R5": "Deck R5",
    "View": "Deck View",
    "Steam": "Deck Steam",
    "Start": "Deck Start",
    "Up": "Deck Up",
    "Down": "Deck Down",
    "Left": "Deck Left",
    "Right": "Deck Right",
    "LPad": "Deck left pad",
    "RPad": "Deck right pad",
    "QAM": "Deck QAM",
    "XBOX_A": "Xbox A",
    "XBOX_B": "Xbox B",
    "XBOX_X": "Xbox X",
    "XBOX_Y": "Xbox Y",
    "XBOX_LB": "Xbox LB",
    "XBOX_RB": "Xbox RB",
    "XBOX_VIEW": "Xbox View",
    "XBOX_MENU": "Xbox Menu",
    "XBOX_SHARE": "Xbox Share",
    "DS_SQUARE": "DualSense Square",
    "DS_CROSS": "DualSense Cross",
    "DS_CIRCLE": "DualSense Circle",
    "DS_TRIANGLE": "DualSense Triangle",
    "DS_L1": "DualSense L1",
    "DS_R1": "DualSense R1",
    "DS_CREATE": "DualSense Create",
    "DS_OPTIONS": "DualSense Options",
    "DS_PS": "DualSense PS",
    "DS_TOUCH": "DualSense Touchpad",
    "DS_MUTE": "DualSense Mute",
    "NS_Y": "Switch Y",
    "NS_X": "Switch X",
    "NS_B": "Switch B",
    "NS_A": "Switch A",
    "NS_R": "Switch R",
    "NS_ZR": "Switch ZR",
    "NS_MINUS": "Switch Minus",
    "NS_PLUS": "Switch Plus",
    "NS_HOME": "Switch Home",
    "NS_CAPTURE": "Switch Capture",
    "NS_L": "Switch L",
    "NS_ZL": "Switch ZL",
    "EV_SOUTH": "Gamepad A",
    "EV_EAST": "Gamepad B",
    "EV_NORTH": "Gamepad X",
    "EV_WEST": "Gamepad Y",
    "EV_TL": "Gamepad LB",
    "EV_TR": "Gamepad RB",
    "EV_TL2": "Gamepad LT",
    "EV_TR2": "Gamepad RT",
    "EV_SELECT": "Gamepad View",
    "EV_START": "Gamepad Menu",
    "EV_MODE": "Gamepad Guide",
    "EV_THUMBL": "Gamepad L3",
    "EV_THUMBR": "Gamepad R3",
}


def friendly_label(buttons: list[str]) -> str:
    return " + ".join(FRIENDLY.get(name, name) for name in buttons)

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
                if name not in LOGICAL_BUTTONS:
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
    phase waits until the held set is empty. Every button pressed in the
    next 1.5 seconds is the combo, including presses that do not overlap.
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
            "label": friendly_label(self.buttons),
            "seconds_left": seconds_left,
        }


def _mask(data: bytes, index: int, bits: int, names: dict[int, str]) -> set[str]:
    if len(data) <= index:
        return set()
    value = data[index] & bits
    return {name for mask, name in names.items() if value & mask}


def decode_hidraw(data: bytes, family: str) -> set[str] | None:
    """Buttons in one report. None means this report is not button input."""

    if not data:
        return None
    if family == "deck":
        return decode_report(data)
    if family == "steam-controller":
        # Report 0x42 only. 0x7b and 0x43 were seen on the same node and are not buttons.
        # d[1] is a rolling counter. d[5] bits 0x10 and 0x20 toggle alone (grip/touch): ignored.
        # Only the two back buttons were captured. Do not invent the rest.
        if data[0] != 0x42:
            return None
        found: set[str] = set()
        if len(data) > 2 and data[2] & 0x80:
            found.add("R4")
        if len(data) > 4 and data[4] & 0x02:
            found.add("L4")
        return found
    if family == "xbox":
        # Bluetooth capture, 17-byte report ID 0x01. USB for this PID was not a separate capture.
        if data[0] != 0x01 or len(data) < 17:
            return None
        found = _mask(data, 14, 0xFF, {0x01: "XBOX_A", 0x02: "XBOX_B", 0x08: "XBOX_X", 0x10: "XBOX_Y", 0x40: "XBOX_LB", 0x80: "XBOX_RB"})
        found |= _mask(data, 15, 0x0C, {0x04: "XBOX_VIEW", 0x08: "XBOX_MENU"})
        found |= _mask(data, 16, 0x01, {0x01: "XBOX_SHARE"})
        return found
    if family == "dualsense":
        # Bluetooth capture, 78-byte report ID 0x31. Low nibble of d[9] is the d-pad (8 = neutral).
        if data[0] != 0x31 or len(data) < 12:
            return None
        return _dualsense_buttons(data, 9)
    if family == "dualsense-usb":
        # UNTESTED. Published USB DualSense input uses report ID 0x01 with the same
        # button bytes shifted one earlier than the Bluetooth 0x31 report. Not captured.
        if data[0] != 0x01 or len(data) < 11:
            return None
        return _dualsense_buttons(data, 8)
    if family in ("switch-pro", "switch-pro-bt"):
        # USB capture is report ID 0x30. Bluetooth is expected to use the same report
        # and was not captured (family switch-pro-bt). d[4] bit 0x80 is always set on
        # the wired capture (wired/grip flag) and is ignored.
        if data[0] != 0x30 or len(data) < 6:
            return None
        found = _mask(data, 3, 0xFF, {0x01: "NS_Y", 0x02: "NS_X", 0x04: "NS_B", 0x08: "NS_A", 0x40: "NS_R", 0x80: "NS_ZR"})
        found |= _mask(data, 4, 0x7F, {0x01: "NS_MINUS", 0x02: "NS_PLUS", 0x10: "NS_HOME", 0x20: "NS_CAPTURE"})
        found |= _mask(data, 5, 0xC0, {0x40: "NS_L", 0x80: "NS_ZL"})
        return found
    return None


def _dualsense_buttons(data: bytes, start: int) -> set[str]:
    found = _mask(data, start, 0xF0, {0x10: "DS_SQUARE", 0x20: "DS_CROSS", 0x40: "DS_CIRCLE", 0x80: "DS_TRIANGLE"})
    found |= _mask(data, start + 1, 0xFF, {0x01: "DS_L1", 0x02: "DS_R1", 0x10: "DS_CREATE", 0x20: "DS_OPTIONS"})
    found |= _mask(data, start + 2, 0x07, {0x01: "DS_PS", 0x02: "DS_TOUCH", 0x04: "DS_MUTE"})
    return found


_HID_ID = re.compile(r"HID_ID=([0-9A-F]{4}):([0-9A-F]{8}):([0-9A-F]{8})")
# VID, PID -> family before the bus adjusts DualSense USB and Switch Pro Bluetooth.
_HID_FAMILIES = {
    ("28DE", "1205"): "deck",
    ("28DE", "1304"): "steam-controller",
    ("045E", "0B13"): "xbox",
    ("054C", "0CE6"): "dualsense",
    ("057E", "2009"): "switch-pro",
}


def parse_hid_id(text: str) -> tuple[str, str, str] | None:
    match = _HID_ID.search(text.upper())
    if match is None:
        return None
    bus, vendor, product = match.group(1), match.group(2)[-4:], match.group(3)[-4:]
    return bus, vendor, product


def family_for_hid(bus: str, vendor: str, product: str) -> str | None:
    base = _HID_FAMILIES.get((vendor, product))
    if base is None:
        return None
    if base == "dualsense" and bus == "0003":
        return "dualsense-usb"
    if base == "switch-pro" and bus == "0005":
        return "switch-pro-bt"
    return base


def list_controller_hidraw(sysfs_root: str = "/sys/class/hidraw") -> list[tuple[str, str]]:
    """``(device path, family)`` for hidraw nodes this plugin decodes."""

    if not os.path.isdir(sysfs_root):
        return []
    found: list[tuple[str, str]] = []
    for name in sorted(os.listdir(sysfs_root)):
        if not name.startswith("hidraw"):
            continue
        uevent_path = os.path.join(sysfs_root, name, "device", "uevent")
        try:
            with open(uevent_path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        parsed = parse_hid_id(text)
        if parsed is None:
            continue
        family = family_for_hid(*parsed)
        if family is None:
            continue
        found.append((os.path.join("/dev", name), family))
    return found


# Linux evdev gamepad keys. The generic path is untested on hardware.
EV_KEY = 0x01
GAMEPAD_CODES = {
    0x130: "EV_SOUTH",
    0x131: "EV_EAST",
    0x133: "EV_NORTH",
    0x134: "EV_WEST",
    0x136: "EV_TL",
    0x137: "EV_TR",
    0x138: "EV_TL2",
    0x139: "EV_TR2",
    0x13A: "EV_SELECT",
    0x13B: "EV_START",
    0x13C: "EV_MODE",
    0x13D: "EV_THUMBL",
    0x13E: "EV_THUMBR",
}
# Hidraw already covers these. Every 28DE device is excluded too, including
# Steam's virtual "Microsoft X-Box 360 pad" (28DE:11FF), so one press cannot
# be reported twice.
_HIDRAW_VID_PID = {
    (0x28DE, 0x1205),
    (0x28DE, 0x1304),
    (0x045E, 0x0B13),
    (0x054C, 0x0CE6),
    (0x057E, 0x2009),
}


def should_open_evdev(vendor: int, product: int, key_codes: set[int]) -> bool:
    if vendor == 0x28DE or (vendor, product) in _HIDRAW_VID_PID:
        return False
    return bool(key_codes & GAMEPAD_CODES.keys())


def key_codes_from_bitmap(text: str, bits_per_word: int = 64) -> set[int]:
    """Parse ``/sys/class/input/.../capabilities/key`` (highest word first)."""

    codes: set[int] = set()
    words = text.split()
    for index, word in enumerate(reversed(words)):
        try:
            value = int(word, 16)
        except ValueError:
            continue
        for bit in range(bits_per_word):
            if value & (1 << bit):
                codes.add(index * bits_per_word + bit)
    return codes


def parse_input_events(data: bytes) -> list[tuple[int, int, int]]:
    """Return ``(type, code, value)`` from a raw evdev read.

    64-bit input_event is 24 bytes. 32-bit is 16. Repeats (value 2) are kept
    for the caller to ignore.
    """

    if not data:
        return []
    size = 24 if len(data) % 24 == 0 else 16
    fmt = "llHHi" if size == 24 else "iiHHi"
    if struct.calcsize(fmt) != size or len(data) % size:
        return []
    events = []
    for offset in range(0, len(data), size):
        _sec, _usec, typ, code, value = struct.unpack_from(fmt, data, offset)
        events.append((typ, code, value))
    return events


def apply_evdev(held: set[str], data: bytes) -> set[str]:
    updated = set(held)
    for typ, code, value in parse_input_events(data):
        name = GAMEPAD_CODES.get(code)
        if typ != EV_KEY or name is None or value == 2:
            continue
        if value:
            updated.add(name)
        else:
            updated.discard(name)
    return updated


def list_evdev_gamepads(sysfs_root: str = "/sys/class/input") -> list[str]:
    """event device paths for gamepads that hidraw is not already reading."""

    if not os.path.isdir(sysfs_root):
        return []
    found: list[str] = []
    for name in sorted(os.listdir(sysfs_root)):
        if not name.startswith("event") or not name[5:].isdigit():
            continue
        device = os.path.join(sysfs_root, name, "device")
        try:
            with open(os.path.join(device, "id", "vendor"), "r", encoding="utf-8") as handle:
                vendor = int(handle.read().strip(), 16)
            with open(os.path.join(device, "id", "product"), "r", encoding="utf-8") as handle:
                product = int(handle.read().strip(), 16)
            with open(os.path.join(device, "capabilities", "key"), "r", encoding="utf-8") as handle:
                keys = handle.read()
        except (OSError, ValueError):
            continue
        if should_open_evdev(vendor, product, key_codes_from_bitmap(keys)):
            found.append(os.path.join("/dev/input", name))
    return found


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


class _OpenNode:
    def __init__(self, path: str, family: str, kind: str):
        self.path = path
        self.family = family
        self.kind = kind
        self.held: set[str] = set()


class DeckHidWatcher:
    """select() loop over controller hidraw nodes and generic evdev gamepads.

    Open is read-only and non-blocking. Nothing is grabbed, so Steam keeps
    its own reader. Nodes are scanned again every few seconds and after an
    open or read failure. One ShortcutEdge covers every device, so the
    400 ms debounce is global and a Deck R4 plus a Steam Controller right
    back button cannot fire twice.
    """

    def __init__(self, on_fire, log_debug, sysfs_root: str = "/sys/class/hidraw", input_root: str = "/sys/class/input"):
        self._on_fire = on_fire
        self._log = log_debug
        self._sysfs = sysfs_root
        self._input = input_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._edge = ShortcutEdge(["R4"])
        self._capture: ButtonCapture | None = None
        self._held: set[str] = set()
        self.active = False
        self.nodes: list[str] = []
        self._announced: list[str] | None = None
        self._open: dict[int, _OpenNode] = {}

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
        open_fds: dict[int, _OpenNode] = {}
        logged_input: set[str] = set()
        next_scan = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_scan:
                    open_fds = self._rescan(open_fds)
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
                    open_fds = self._close_all(open_fds)
                    next_scan = 0.0
                    continue
                for fd in readable:
                    node = open_fds.get(fd)
                    if node is None:
                        continue
                    try:
                        data = os.read(fd, REPORT_LEN)
                    except OSError:
                        self._drop_fd(fd, open_fds)
                        if not open_fds:
                            self._publish("none", "hidraw")
                        next_scan = 0.0
                        continue
                    if not data:
                        self._drop_fd(fd, open_fds)
                        if not open_fds:
                            self._publish("none", "hidraw")
                        next_scan = 0.0
                        continue
                    if node.kind == "evdev":
                        updated = apply_evdev(node.held, data)
                        if updated == node.held:
                            continue
                        node.held = updated
                    else:
                        decoded = decode_hidraw(data, node.family)
                        if decoded is None:
                            continue
                        node.held = decoded
                    if node.path not in logged_input:
                        logged_input.add(node.path)
                        self._log(f"hidraw input {os.path.basename(node.path)}")
                    self._publish(node.family, node.kind)
        finally:
            self._close_all(open_fds)
            self.active = False

    def _publish(self, family: str, via: str) -> None:
        with self._lock:
            held: set[str] = set()
            for node in self._open.values():
                held |= node.held
            if held != self._held:
                self._log("hidraw buttons " + ("+".join(order_buttons(held)) or "none"))
                self._held = set(held)
            capturing = self._capture is not None and self._capture.phase in ("wait_release", "listen")
            if capturing and self._capture is not None:
                self._capture.feed(held, time.monotonic())
                self._edge.observe(held)
                return
            fired = self._edge.feed(held, time.monotonic())
            label = "+".join(self._edge.buttons) or "shortcut"
        if fired and not self._stop.is_set():
            self._log(f"shortcut fire {label} from {family}")
            try:
                self._on_fire(via)
            except TypeError:
                self._on_fire()

    def _rescan(self, open_fds: dict[int, _OpenNode]) -> dict[int, _OpenNode]:
        wanted: dict[str, tuple[str, str]] = {}
        for path, family in list_controller_hidraw(self._sysfs):
            wanted[path] = (family, "hidraw")
        for path in list_evdev_gamepads(self._input):
            wanted[path] = ("evdev", "evdev")
        for fd, node in list(open_fds.items()):
            if node.path not in wanted:
                self._drop_fd(fd, open_fds)
        for path, (family, kind) in wanted.items():
            if any(node.path == path for node in open_fds.values()):
                continue
            try:
                # Read-only and not grabbed. Steam and the desktop keep working.
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as err:
                self._log(f"hidraw open failed {os.path.basename(path)}: {err.__class__.__name__}")
                continue
            open_fds[fd] = _OpenNode(path, family, kind)
        self._open = open_fds
        names = [f"{os.path.basename(node.path)}:{node.family}" for node in open_fds.values()]
        names.sort()
        self.nodes = names
        if names != self._announced:
            self._announced = names
            self._log("hidraw nodes " + (" ".join(names) or "none"))
        self.active = bool(open_fds)
        return open_fds

    def _drop_fd(self, fd: int, open_fds: dict[int, _OpenNode]) -> None:
        open_fds.pop(fd, None)
        self._open = open_fds
        try:
            os.close(fd)
        except OSError:
            pass

    def _close_all(self, open_fds: dict[int, _OpenNode]) -> dict[int, _OpenNode]:
        for fd in list(open_fds):
            self._drop_fd(fd, open_fds)
        self._open = {}
        return {}
