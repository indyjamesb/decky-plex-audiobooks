"""ffplay playback for Game Mode.

The Steam UI's web view is not a reliable place to keep audio running once a
game is focused. Museck (MIT, https://github.com/Nezreka/Museck) shows the
approach that works on SteamOS: a long-lived ffplay process, started with a
clean environment, pointed at the logged-in user's Pulse/PipeWire socket.
Decky's Python build puts its own libraries on ``LD_LIBRARY_PATH``; a system
ffplay that inherits that path exits immediately. This module reimplements
that idea. It does not copy Museck's source.

Position is estimated in ``progress.compute_position_ms``. Pause is SIGSTOP,
which freezes the decoder so the clock math still matches the audio. Seek and
speed changes restart ffplay at the new offset.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
import ctypes

from progress import atempo_filters, compute_position_ms

# Offsets smaller than this are not worth an input seek. The report layer can
# still tell Plex about a 1ms anchor while audio starts at the beginning.
_SEEK_THRESHOLD_MS = 500

_BUNDLE_PATH = re.compile(r"(?:^|/)_MEI[0-9A-Za-z_]{6,}(?:/|$)")

_ENV_KEYS = (
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TZ",
    "XDG_CONFIG_HOME",
)


class PlayerError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def redact_text(text: str, secrets: list[str]) -> str:
    """Replace secret values. Short strings are ignored so we don't blank the log."""

    redacted = text or ""
    for secret in secrets:
        if secret and len(secret) >= 8:
            redacted = redacted.replace(secret, "***")
    return redacted


def _bundle_tainted(entry: str) -> bool:
    if not entry:
        return False
    # sys._MEIPASS is set when Decky's PyInstaller build is the parent process.
    import sys

    bundle = getattr(sys, "_MEIPASS", "") or ""
    if bundle and (entry == bundle or entry.startswith(bundle + os.sep)):
        return True
    return _BUNDLE_PATH.search(entry) is not None


def _die_with_parent() -> None:
    """Ask the kernel to signal this ffplay if the plugin process dies.

    A paused ffplay is stopped with SIGSTOP, so it also has to be continued
    before that signal is delivered. ``start_new_session`` keeps it out of
    the plugin's process group.
    """

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        # PR_SET_PDEATHSIG, SIGTERM
        libc.prctl(1, signal.SIGTERM)
    except (OSError, AttributeError):
        pass


def find_orphaned_ffplay(proc_root: str, keep_pids: set[int]) -> list[tuple[int, bool]]:
    """ffplay processes started by this plugin that are not the current child.

    Returns ``(pid, stopped)``. Stopped means /proc state T, the SIGSTOP pause.
    """

    found: list[tuple[int, bool]] = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid in keep_pids:
            continue
        try:
            with open(os.path.join(proc_root, name, "cmdline"), "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        if "ffplay" not in text or "PlexAudiobooks/" not in text:
            continue
        stopped = False
        try:
            with open(os.path.join(proc_root, name, "status"), "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("State:"):
                        parts = line.split()
                        stopped = len(parts) > 1 and parts[1].startswith("T")
                        break
        except OSError:
            stopped = False
        found.append((pid, stopped))
    return found


def stop_ffplay_pid(pid: int, stopped: bool, kill, still_alive) -> None:
    """SIGCONT a stopped ffplay, then SIGTERM, then SIGKILL if it is still there."""

    if stopped:
        kill(pid, signal.SIGCONT)
    kill(pid, signal.SIGTERM)
    if still_alive(pid):
        kill(pid, signal.SIGKILL)


def reap_orphaned_ffplay(proc_root: str, keep_pids: set[int], log, kill=None, still_alive=None) -> list[int]:
    """Stop leftover plugin ffplay processes. Returns the pids that were signalled."""

    killer = kill or os.kill

    def alive(pid: int) -> bool:
        if still_alive is not None:
            return still_alive(pid)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    stopped_pids: list[int] = []
    for pid, stopped in find_orphaned_ffplay(proc_root, keep_pids):
        log("Stopping leftover ffplay pid %s" % pid)
        stop_ffplay_pid(pid, stopped, killer, alive)
        stopped_pids.append(pid)
    return stopped_pids


def _clean_path_list(value: str) -> str:
    kept = [entry for entry in value.split(":") if entry and not _bundle_tainted(entry)]
    return ":".join(kept)


FFPLAY_MISSING = "ffplay not found. Install ffmpeg to play books."
AUDIO_NOT_READY = "Audio isn't ready yet. Try again in a few seconds."
SERVER_UNREACHABLE_PLAYBACK = (
    "Playback stopped immediately. The server may be unreachable, "
    "or this file isn't one ffplay can play."
)


def find_ffplay() -> str:
    for candidate in ("/usr/bin/ffplay", "/bin/ffplay", "/usr/local/bin/ffplay"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("ffplay") or ""


def ffplay_missing_message(path: str) -> str:
    """Empty when ffplay is available. The sentence is shown in the panel."""

    return "" if path else FFPLAY_MISSING


def is_pulse_audio_error(log: str) -> bool:
    """True when ffplay died because SDL could not reach PulseAudio or PipeWire."""

    text = (log or "").lower()
    if "could not setup connection to pulseaudio" in text:
        return True
    if "could not initialize sdl" in text and "pulse" in text:
        return True
    return False


def playback_failure_message(log: str) -> str:
    """Panel text for an ffplay process that exited immediately.

    A missing audio service is not a Plex failure. The server sentence is
    only for other early exits.
    """

    if is_pulse_audio_error(log):
        return AUDIO_NOT_READY
    return SERVER_UNREACHABLE_PLAYBACK


def connect_user_audio(env: dict[str, str], source: dict[str, str], run_info=None) -> bool:
    """Point ``env`` at the desktop user's Pulse socket once it answers.

    ``run_info`` returns True when ``pactl info`` succeeds with the candidate
    environment, False when it fails, and None when pactl is not installed.
    A socket that accepts a connection is enough if pactl is absent. The
    updated environment sets ``XDG_RUNTIME_DIR`` and ``PULSE_SERVER``.
    """

    socket_path = find_pulse_socket()
    if not socket_path:
        return False
    fresh = build_child_env(source, socket_path)
    if run_info is not None:
        info = run_info(fresh)
        if info is False:
            return False
    elif not _socket_answers(socket_path):
        return False
    env.clear()
    env.update(fresh)
    return True


def wait_for_audio(ready, *, budget_s: float = 20.0, sleep=time.sleep, clock=time.monotonic) -> bool:
    """Call ``ready`` until it is true or ``budget_s`` has passed.

    The pause between tries starts at 0.25 s and doubles up to 2 s.
    ``sleep`` and ``clock`` are injectable so tests do not wait on a reboot.
    """

    delay = 0.25
    started = clock()
    while True:
        if ready():
            return True
        elapsed = clock() - started
        if elapsed >= budget_s:
            return False
        sleep(min(delay, budget_s - elapsed))
        delay = min(delay * 2, 2.0)


def _socket_answers(path: str) -> bool:
    import socket
    import stat

    try:
        mode = os.stat(path).st_mode
    except OSError:
        return False
    if not stat.S_ISSOCK(mode):
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.3)
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def find_pulse_socket() -> str:
    """Prefer a Pulse socket that accepts a connection, else the first candidate.

    Decky may run as root while PipeWire runs as the desktop user, so the
    socket is discovered under ``/run/user/*/pulse/native`` rather than assumed
    from the plugin process environment.
    """

    import glob

    candidates = sorted(glob.glob("/run/user/*/pulse/native"))
    if not candidates:
        return ""
    own = f"/run/user/{os.getuid()}/pulse/native"
    candidates.sort(key=lambda path: (path != own, path))
    for candidate in candidates:
        if _socket_answers(candidate):
            return candidate
    return candidates[0]


def build_child_env(source: dict[str, str], pulse_socket: str) -> dict[str, str]:
    """Environment for ffplay and pactl.

    Built from a whitelist. ``LD_LIBRARY_PATH`` is not copied from the parent,
    because on Decky that value points at the loader's bundled libraries.
    ``LD_LIBRARY_PATH_ORIG`` is the user's path from before the bundle started,
    so it is kept when present.
    """

    env: dict[str, str] = {}
    path = source.get("PATH") or ""
    if path:
        cleaned = _clean_path_list(path)
        if cleaned:
            env["PATH"] = cleaned
    for key in _ENV_KEYS:
        if source.get(key):
            env[key] = source[key]
    original_ld = source.get("LD_LIBRARY_PATH_ORIG") or ""
    if original_ld:
        cleaned = _clean_path_list(original_ld)
        if cleaned:
            env["LD_LIBRARY_PATH"] = cleaned
    env["SDL_AUDIODRIVER"] = "pulse"
    if pulse_socket:
        parts = pulse_socket.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "run" and parts[1] == "user" and parts[2].isdigit():
            uid = parts[2]
            env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
            env["PULSE_RUNTIME_PATH"] = f"/run/user/{uid}/pulse"
        env["PULSE_SERVER"] = pulse_socket
    return env


def build_ffplay_args(
    binary: str,
    url: str,
    headers: str,
    offset_ms: int,
    speed: float,
    volume: int,
) -> list[str]:
    """Argument list for one ffplay invocation. ``headers`` may contain a token."""

    args = [
        binary,
        "-nodisp",
        "-autoexit",
        "-loglevel",
        "error",
        "-volume",
        str(max(0, min(100, int(volume)))),
    ]
    if offset_ms > _SEEK_THRESHOLD_MS:
        args.extend(["-ss", f"{offset_ms / 1000.0:.3f}"])
    filters = atempo_filters(speed)
    if filters:
        args.extend(["-af", ",".join(filters)])
    if url.startswith(("http://", "https://")):
        args.extend(["-user_agent", "PlexAudiobooks/0.5.2"])
        if headers:
            args.extend(["-headers", headers])
    args.append(url)
    return args


def set_process_volume(env: dict[str, str], pid: int, volume: int) -> bool:
    """Set the live Pulse volume for this ffplay, if pactl is available."""

    if not pid:
        return False
    pactl = shutil.which("pactl", path=env.get("PATH"))
    if not pactl:
        return False
    try:
        listed = subprocess.run(
            [pactl, "list", "sink-inputs"],
            capture_output=True,
            text=True,
            env=env,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if listed.returncode != 0:
        return False
    index = _sink_input_index(listed.stdout, pid)
    if not index:
        return False
    try:
        result = subprocess.run(
            [pactl, "set-sink-input-volume", index, f"{int(volume)}%"],
            capture_output=True,
            text=True,
            env=env,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _sink_input_index(listing: str, pid: int) -> str:
    found = ""
    needle = f'application.process.id = "{pid}"'
    for block in listing.split("Sink Input #"):
        if not block.strip():
            continue
        sink_id = block.splitlines()[0].strip()
        if needle in block:
            return sink_id
        if 'application.name = "ffplay"' in block and not found:
            found = sink_id
    return found


class Player:
    """One ffplay process and the clock used to estimate its position."""

    def __init__(self, ffplay: str, log_path: str, env: dict[str, str]):
        self.ffplay = ffplay
        self.log_path = log_path
        self.env = env
        self.proc: subprocess.Popen[bytes] | None = None
        self.base_ms = 0
        self.started_at: float | None = None
        self.paused_total = 0.0
        self.pause_started: float | None = None
        self.paused = False
        self.speed = 1.0
        self.duration_ms = 0
        self.active = False

    def position_ms(self) -> int:
        if not self.active:
            return max(0, self.base_ms)
        return compute_position_ms(
            self.base_ms,
            self.started_at,
            time.monotonic(),
            self.paused_total,
            self.pause_started,
            self.speed,
            self.duration_ms,
        )

    def process_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def poll(self) -> int | None:
        if self.proc is None:
            return None
        return self.proc.poll()

    def start(
        self,
        url: str,
        headers: str,
        offset_ms: int,
        speed: float,
        volume: int,
        duration_ms: int,
    ) -> None:
        if not self.ffplay:
            raise PlayerError("ffplay was not found. Install ffmpeg on the Deck, then try again.")
        self.stop()
        self.speed = speed if speed > 0 else 1.0
        self.duration_ms = max(0, int(duration_ms))
        # Match the seek threshold in build_ffplay_args so the clock starts
        # where the audio actually starts.
        self.base_ms = int(offset_ms) if int(offset_ms) > _SEEK_THRESHOLD_MS else 0
        args = build_ffplay_args(self.ffplay, url, headers, int(offset_ms), self.speed, volume)
        log_dir = os.path.dirname(self.log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8", errors="replace") as log_file:
            self.proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_file,
                env=self.env,
                start_new_session=True,
                preexec_fn=_die_with_parent,
            )
        self.started_at = time.monotonic()
        self.paused_total = 0.0
        self.pause_started = None
        self.paused = False
        self.active = True

    def pause(self) -> None:
        if not self.process_alive() or self.paused or self.proc is None:
            return
        self.proc.send_signal(signal.SIGSTOP)
        self.paused = True
        self.pause_started = time.monotonic()

    def resume(self) -> None:
        if not self.paused or self.proc is None:
            return
        self.proc.send_signal(signal.SIGCONT)
        if self.pause_started is not None:
            self.paused_total += time.monotonic() - self.pause_started
        self.pause_started = None
        self.paused = False

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        self.active = False
        self.paused = False
        self.pause_started = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                # A stopped process ignores SIGTERM until it is continued.
                proc.send_signal(signal.SIGCONT)
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass

    def mark_inactive(self) -> None:
        """The process already exited. Forget it without signalling."""

        self.proc = None
        self.active = False
        self.paused = False
        self.pause_started = None

    def read_log(self, secrets: list[str]) -> str:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as handle:
                original = handle.read()
        except OSError:
            return ""
        redacted = redact_text(original, secrets)
        if redacted != original and not self.process_alive():
            # ffplay has closed the file, so rewriting it cannot race its writes.
            try:
                with open(self.log_path, "w", encoding="utf-8") as handle:
                    handle.write(redacted)
            except OSError:
                pass
        return "\n".join(redacted.splitlines()[-20:]).strip()
