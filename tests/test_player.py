import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
import unittest.mock

from player import (
    AUDIO_NOT_READY,
    FFPLAY_MISSING,
    Player,
    SERVER_UNREACHABLE_PLAYBACK,
    build_child_env,
    build_ffplay_args,
    connect_user_audio,
    ffplay_missing_message,
    find_ffplay,
    find_orphaned_ffplay,
    playback_failure_message,
    reap_orphaned_ffplay,
    redact_text,
    stop_ffplay_pid,
    wait_for_audio,
)

class EnvTests(unittest.TestCase):
    def test_bundle_paths_and_unknown_variables_stay_out(self):
        env = build_child_env(
            {
                "PATH": "/tmp/_MEI123456/bin:/usr/bin",
                "HOME": "/home/deck",
                "LD_LIBRARY_PATH": "/tmp/_MEI123456",
                "LD_LIBRARY_PATH_ORIG": "/opt/custom/lib:/tmp/_MEI123456/lib",
                "SECRET": "should-not-leak",
            },
            "",
        )
        self.assertNotIn("_MEI", env.get("PATH", ""))
        self.assertIn("/usr/bin", env["PATH"])
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/custom/lib")
        self.assertNotIn("SECRET", env)
        self.assertEqual(env["SDL_AUDIODRIVER"], "pulse")
        self.assertNotIn("PULSE_SERVER", env)

    def test_pulse_socket_sets_runtime_dir(self):
        env = build_child_env({"PATH": "/usr/bin", "HOME": "/home/deck"}, "/run/user/1000/pulse/native")
        self.assertEqual(env["PULSE_SERVER"], "/run/user/1000/pulse/native")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")


class FfplayLookupTests(unittest.TestCase):
    def test_orphaned_plugin_ffplay_is_continued_then_killed(self):
        with tempfile.TemporaryDirectory() as root:
            self._proc(root, "42", b"ffplay\x00-user_agent\x00PlexAudiobooks/0.5.0\x00", "T (stopped)")
            self._proc(root, "7", b"ffplay\x00-user_agent\x00Chrome\x00", "S (sleeping)")
            self._proc(root, "9", b"ffplay\x00-user_agent\x00PlexAudiobooks/0.5.1\x00", "S (sleeping)")
            found = find_orphaned_ffplay(root, {9})
            self.assertEqual(found, [(42, True)])
            signals: list[tuple[int, int]] = []
            alive = {"42": True}

            def kill(pid, sig):
                signals.append((pid, sig))
                if sig == signal.SIGKILL:
                    alive[str(pid)] = False

            reap_orphaned_ffplay(root, {9}, lambda _message: None, kill=kill, still_alive=lambda pid: alive.get(str(pid), False))
            self.assertEqual(signals[0], (42, signal.SIGCONT))
            self.assertEqual(signals[1], (42, signal.SIGTERM))
            self.assertEqual(signals[2], (42, signal.SIGKILL))

    def test_stop_sequence_skips_continue_when_running(self):
        signals: list[int] = []
        stop_ffplay_pid(3, False, lambda _pid, sig: signals.append(sig), lambda _pid: False)
        self.assertEqual(signals, [signal.SIGTERM])

    def _proc(self, root: str, pid: str, cmdline: bytes, state: str) -> None:
        folder = os.path.join(root, pid)
        os.makedirs(folder)
        with open(os.path.join(folder, "cmdline"), "wb") as handle:
            handle.write(cmdline)
        with open(os.path.join(folder, "status"), "w", encoding="utf-8") as handle:
            handle.write(f"State:\t{state}\n")

    def test_audio_wait_retries_until_the_socket_answers(self):
        attempts = {"n": 0, "t": 0.0}
        env: dict[str, str] = {}

        def fake_socket():
            attempts["n"] += 1
            if attempts["n"] < 3:
                return ""
            return "/run/user/1000/pulse/native"

        def ready():
            return connect_user_audio(env, {"PATH": "/usr/bin", "HOME": "/home/deck"}, lambda _fresh: True)

        def clock():
            return attempts["t"]

        def sleep(delay):
            attempts["t"] += delay

        with unittest.mock.patch("player.find_pulse_socket", fake_socket):
            self.assertTrue(wait_for_audio(ready, budget_s=20, sleep=sleep, clock=clock))
        self.assertGreaterEqual(attempts["n"], 3)
        self.assertLess(attempts["t"], 20)
        self.assertEqual(env["PULSE_SERVER"], "/run/user/1000/pulse/native")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")

    def test_audio_never_ready_is_not_a_server_error(self):
        def clock_and_sleep():
            now = {"t": 0.0}

            def clock():
                return now["t"]

            def sleep(delay):
                now["t"] += delay

            return clock, sleep

        clock, sleep = clock_and_sleep()
        self.assertFalse(wait_for_audio(lambda: False, budget_s=20, sleep=sleep, clock=clock))
        self.assertGreaterEqual(clock(), 20)
        self.assertNotIn("unreachable", AUDIO_NOT_READY.lower())
        self.assertNotIn("server", AUDIO_NOT_READY.lower())
        self.assertNotEqual(AUDIO_NOT_READY, SERVER_UNREACHABLE_PLAYBACK)

    def test_ffplay_pulse_stderr_maps_to_the_audio_message(self):
        log = "Could not initialize SDL - Could not setup connection to PulseAudio"
        self.assertEqual(playback_failure_message(log), AUDIO_NOT_READY)
        self.assertNotIn("unreachable", playback_failure_message(log))
        other = "Server returned 404 Not Found"
        self.assertEqual(playback_failure_message(other), SERVER_UNREACHABLE_PLAYBACK)
        self.assertIn("unreachable", SERVER_UNREACHABLE_PLAYBACK)

    def test_missing_ffplay_has_a_panel_message(self):
        self.assertEqual(ffplay_missing_message(""), FFPLAY_MISSING)
        self.assertEqual(ffplay_missing_message("/usr/bin/ffplay"), "")
        self.assertIn("ffplay", FFPLAY_MISSING)
        self.assertIn("ffmpeg", FFPLAY_MISSING)


class ArgTests(unittest.TestCase):
    def test_stream_url_does_not_carry_the_token(self):
        token = "super-secret-token-value"
        url = "http://10.0.0.91:32400/library/parts/5/6/file.m4b"
        headers = f"X-Plex-Token: {token}\r\n"
        args = build_ffplay_args("ffplay", url, headers, 125_000, 1.5, 40)
        self.assertNotIn(token, url)
        joined_without_headers = " ".join(arg for arg in args if token not in arg)
        self.assertNotIn(token, joined_without_headers)
        self.assertIn(headers, args)
        self.assertIn("-ss", args)
        self.assertIn("125.000", args)
        self.assertIn("atempo=1.5000", ",".join(args))
        self.assertEqual(args[-1], url)

    def test_tiny_offset_does_not_seek(self):
        args = build_ffplay_args("ffplay", "/tmp/a.wav", "", 1, 1.0, 80)
        self.assertNotIn("-ss", args)
        self.assertNotIn("-af", args)

    def test_redact_replaces_the_token_only(self):
        token = "super-secret-token-value"
        text = f"error opening http://host/file?X-Plex-Token={token}"
        self.assertNotIn(token, redact_text(text, [token]))
        self.assertIn("***", redact_text(text, [token]))
        self.assertEqual(redact_text("ok", ["short"]), "ok")

    def test_stopped_player_log_is_scrubbed_on_disk(self):
        token = "super-secret-token-value"
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "ffplay.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"X-Plex-Token: {token}\n")
            player = Player("", path, {})
            shown = player.read_log([token])
            self.assertNotIn(token, shown)
            with open(path, encoding="utf-8") as handle:
                self.assertNotIn(token, handle.read())


@unittest.skipUnless(find_ffplay() and shutil.which("ffmpeg"), "ffmpeg/ffplay not installed")
class PlaybackTests(unittest.TestCase):
    """Runs ffplay against a generated tone with a dummy audio driver.

    This does not talk to Plex and does not exercise the Deck's PipeWire socket.
    """

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.mkdtemp(prefix="plex-audiobooks-")
        cls.tone = os.path.join(cls._dir, "tone.wav")
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                "-ar", "44100", cls.tone,
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._dir, ignore_errors=True)

    def test_play_pause_and_speed_seek(self):
        env = build_child_env({"PATH": os.environ.get("PATH", ""), "HOME": "/tmp"}, "")
        env["SDL_AUDIODRIVER"] = "dummy"
        env["SDL_VIDEODRIVER"] = "dummy"
        player = Player(find_ffplay(), os.path.join(self._dir, "ffplay.log"), env)
        try:
            player.start(self.tone, "", 0, 1.0, 50, 4_000)
            time.sleep(0.4)
            self.assertIsNone(player.poll(), player.read_log([]))
            time.sleep(0.35)
            moving = player.position_ms()
            self.assertGreater(moving, 200)
            player.pause()
            held = player.position_ms()
            time.sleep(0.35)
            self.assertLess(abs(player.position_ms() - held), 80)
            player.resume()
            player.stop()

            player.start(self.tone, "", 1_500, 2.0, 50, 4_000)
            time.sleep(0.4)
            self.assertIsNone(player.poll(), player.read_log([]))
            # Started 1.5s in, then ~0.4s at 2x, so the estimate is past 2s.
            self.assertGreater(player.position_ms(), 2_000)
        finally:
            player.stop()


if __name__ == "__main__":
    unittest.main()
