import os
import shutil
import subprocess
import tempfile
import time
import unittest

from player import Player, build_child_env, build_ffplay_args, find_ffplay, redact_text


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
