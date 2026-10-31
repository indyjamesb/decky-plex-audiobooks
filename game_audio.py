"""Per-application game volume through pactl sink-inputs.

PipeWire on SteamOS remembers a volume for each application.name. This module
never changes the sink or the master volume. It only sets other programs'
sink-inputs, and it writes down the volume they had before the first change
so a duck does not stick after pause, unload, or a crash.

The book's own ffplay is skipped by process id. speech-dispatcher and
sd_dummy are skipped too. Tested with pactl on a Steam Deck OLED; the parser
and the restore rules are covered without a live Pulse server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

_VOLUME_PART = re.compile(r"([A-Za-z0-9_-]+):\s*(\d+)\s*/\s*(\d+)%")
_PROP = re.compile(r'^\s*([A-Za-z][^:=]*):\s*(.*)$')


def clamp_percent(value: object, default: int = 100) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, min(100, number))


def truthy(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return default


class SinkInput:
    def __init__(self, index: str, name: str, binary: str, pid: str, volumes: list[int]):
        self.index = index
        self.name = name
        self.binary = binary
        self.pid = pid
        self.volumes = volumes

    @property
    def binary_name(self) -> str:
        return os.path.basename(self.binary)


def parse_sink_inputs(listing: str) -> list[SinkInput]:
    """Parse ``pactl list sink-inputs``. Volume lines may wrap and have many channels."""

    streams: list[SinkInput] = []
    for raw in listing.split("Sink Input #"):
        if not raw.strip():
            continue
        lines = raw.splitlines()
        index = lines[0].strip().split()[0]
        if not index.isdigit():
            continue
        volumes = _volumes_from_block(lines[1:])
        name = _prop(raw, "application.name")
        binary = _prop(raw, "application.process.binary")
        pid = _prop(raw, "application.process.id")
        streams.append(SinkInput(index, name, binary, pid, volumes))
    return streams


def _prop(block: str, key: str) -> str:
    match = re.search(rf'{re.escape(key)} = "(.*)"', block)
    return match.group(1) if match else ""


def _volumes_from_block(lines: list[str]) -> list[int]:
    collected: list[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Volume:"):
            capture = True
            collected.append(stripped.split(":", 1)[1])
            continue
        if not capture:
            continue
        if _PROP.match(stripped) and not _VOLUME_PART.match(stripped):
            break
        collected.append(stripped)
    found: list[int] = []
    for _channel, raw, _percent in _VOLUME_PART.findall(" ".join(collected)):
        found.append(int(raw))
        if len(found) == 12:
            break
    return found


def is_own_stream(stream: SinkInput, own_pids: set[str]) -> bool:
    """True for this plugin's ffplay. Other ffplay processes are left alone."""

    if stream.pid and stream.pid in own_pids:
        return True
    return False


def is_system_chatter(stream: SinkInput) -> bool:
    binary = stream.binary_name.lower()
    name = stream.name.lower()
    if binary == "sd_dummy":
        return True
    if "speech-dispatcher" in name or "speech-dispatcher" in binary:
        return True
    return False


def controllable_streams(streams: list[SinkInput], own_pids: set[str]) -> list[SinkInput]:
    return [stream for stream in streams if not is_own_stream(stream, own_pids) and not is_system_chatter(stream)]


def playing_level(game_volume: int, duck_level: int, auto_lower: bool) -> int:
    """Volume to apply while the book is playing and ducking is on."""

    if auto_lower:
        return min(game_volume, duck_level)
    return game_volume


class VolumePlan:
    def __init__(self, index: str, app: str, percent: int | None, raw: list[int] | None):
        self.index = index
        self.app = app
        self.percent = percent
        self.raw = raw


def plan_volumes(
    streams: list[SinkInput],
    own_pids: set[str],
    *,
    playing: bool,
    game_volume: int,
    duck_level: int,
    auto_lower: bool,
    originals: dict[str, list[int]],
) -> tuple[list[VolumePlan], dict[str, list[int]]]:
    """Decide pactl changes.

    While the book plays and ducking is on, other streams go to the lower of
    ``duck_level`` and ``game_volume``. Pause, stop, and a game volume of 100
    put back the exact original channel values when we have them. A game
    volume below 100, with the book paused, sets that percent instead.
    """

    saved = {name: list(values) for name, values in originals.items()}
    actions: list[VolumePlan] = []
    forget: list[str] = []
    for stream in controllable_streams(streams, own_pids):
        if playing and auto_lower:
            if stream.name and stream.name not in saved and stream.volumes:
                saved[stream.name] = list(stream.volumes)
            actions.append(VolumePlan(stream.index, stream.name, playing_level(game_volume, duck_level, True), None))
            continue
        if game_volume >= 100:
            raw = saved.get(stream.name) if stream.name else None
            if raw:
                actions.append(VolumePlan(stream.index, stream.name, None, list(raw)))
                forget.append(stream.name)
            continue
        if stream.name and stream.name not in saved and stream.volumes:
            saved[stream.name] = list(stream.volumes)
        actions.append(VolumePlan(stream.index, stream.name, game_volume, None))
    for name in forget:
        saved.pop(name, None)
    return actions, saved


class VolumeBook:
    """Original volumes for application names we have changed."""

    def __init__(self, path: str):
        self.path = path
        self.apps: dict[str, list[int]] = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self.apps = {}
            return
        apps = payload.get("apps") if isinstance(payload, dict) else None
        cleaned: dict[str, list[int]] = {}
        if isinstance(apps, dict):
            for name, values in apps.items():
                if not isinstance(name, str) or not isinstance(values, list):
                    continue
                numbers = [int(value) for value in values if isinstance(value, int) and value >= 0]
                if numbers:
                    cleaned[name] = numbers[:12]
        self.apps = cleaned

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"apps": self.apps}, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)

    def replace(self, apps: dict[str, list[int]]) -> None:
        self.apps = {name: list(values) for name, values in apps.items() if values}
        self.save()


class GameMixer:
    """Applies a volume plan through pactl. ``run`` is injectable for tests."""

    def __init__(self, run, state_path: str, log):
        self._run = run
        self.book = VolumeBook(state_path)
        self._log = log
        self._missing = False

    def sync(
        self,
        *,
        playing: bool,
        own_pids: set[str],
        game_volume: int,
        duck_level: int,
        auto_lower: bool,
    ) -> bool:
        listing = self._call(["list", "sink-inputs"])
        if listing is None:
            return False
        streams = parse_sink_inputs(listing)
        actions, apps = plan_volumes(
            streams,
            own_pids,
            playing=playing,
            game_volume=game_volume,
            duck_level=duck_level,
            auto_lower=auto_lower,
            originals=self.book.apps,
        )
        ok = True
        for action in actions:
            if action.raw:
                args = ["set-sink-input-volume", action.index, *[str(value) for value in action.raw]]
            else:
                args = ["set-sink-input-volume", action.index, f"{int(action.percent)}%"]
            if self._call(args) is None:
                ok = False
        if apps != self.book.apps:
            self.book.replace(apps)
        return ok

    def restore_originals(self) -> bool:
        """Put every stream we changed back to the saved channel values."""

        return self.sync(playing=False, own_pids=set(), game_volume=100, duck_level=100, auto_lower=False)

    def _call(self, args: list[str]) -> str | None:
        try:
            result = self._run(args)
        except Exception:
            self._log("pactl failed")
            return None
        if result is None:
            if not self._missing:
                self._missing = True
                self._log("pactl is not available; game volume was not changed")
            return None
        code, output = result
        if code != 0:
            self._log("pactl %s failed" % args[0])
            return None
        return output


def pactl_run(env: dict[str, str], args: list[str]) -> tuple[int, str] | None:
    """Run pactl with the desktop user's Pulse environment. None if it is missing."""

    pactl = shutil.which("pactl", path=env.get("PATH"))
    if not pactl:
        return None
    try:
        completed = subprocess.run(
            [pactl, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.returncode, completed.stdout or ""
