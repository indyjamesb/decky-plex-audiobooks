"""A clean ffplay exit is the next chapter only at the known duration.

Stream drops and wake-from-sleep exit 0 because ffplay was started with
-autoexit. Those must resume the same chapter and must not scrobble it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

decky = types.ModuleType("decky")


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message % args if args else message)

    def error(self, *_args: object, **_kwargs: object) -> None:
        return None

    def info(self, *_args: object, **_kwargs: object) -> None:
        return None

    def debug(self, *_args: object, **_kwargs: object) -> None:
        return None

    def log(self, *_args: object, **_kwargs: object) -> None:
        return None

    def setLevel(self, *_args: object, **_kwargs: object) -> None:
        return None

    def isEnabledFor(self, *_args: object, **_kwargs: object) -> bool:
        return True


decky.logger = _Logger()
decky.DECKY_PLUGIN_SETTINGS_DIR = "/tmp"
decky.DECKY_PLUGIN_LOG_DIR = "/tmp"
sys.modules["decky"] = decky

from main import PAUSE_MESSAGE, PREMATURE_RETRY_LIMIT, Plugin  # noqa: E402
from progress import AudioTrack  # noqa: E402


class _Player:
    def __init__(self, position: int, exit_code: int | None) -> None:
        self.active = True
        self.paused = False
        self._position = position
        self._exit = exit_code

    def poll(self) -> int | None:
        return self._exit

    def position_ms(self) -> int:
        return self._position

    def mark_inactive(self) -> None:
        self.active = False

    def process_alive(self) -> bool:
        return self.active and self._exit is None

    def read_log(self, _secrets: list[str]) -> str:
        return ""


class _Plex:
    def __init__(self) -> None:
        self.scrobbles: list[tuple[str, bool]] = []
        self.stopped: list[tuple[str, int, bool]] = []

    def scrobble(self, key: str, played: bool) -> None:
        self.scrobbles.append((key, played))

    def timeline(self, key: str, state: str, position: int, _duration: int, finishing: bool = False, **_kwargs: object) -> None:
        if state == "stopped":
            self.stopped.append((key, position, finishing))


class PrematureExitTests(unittest.TestCase):
    def _plugin(self, position: int, exit_code: int | None = 0) -> tuple[Plugin, _Plex, list]:
        decky.logger.warnings.clear()
        plugin = Plugin()
        plugin._premature_backoff_s = (0.0, 0.0, 0.0)
        plugin.tracks = [
            AudioTrack("ch-2", "Chapter 2", duration_ms=3_600_000, view_offset_ms=5_000, part_key="2"),
            AudioTrack("ch-3", "Chapter 3", duration_ms=3_600_000, part_key="3"),
        ]
        plugin.index = 0
        plugin.player = _Player(position, exit_code)  # type: ignore[assignment]
        plex = _Plex()

        async def plex_call(func):
            return func(plex)

        starts: list[tuple[int, int]] = []

        async def start(index: int, offset: int, user_initiated: bool = True):
            starts.append((index, offset))
            plugin.index = index
            plugin.player.active = True  # type: ignore[union-attr]
            plugin.player._exit = 0  # type: ignore[attr-defined]
            plugin.player._position = offset  # type: ignore[attr-defined]
            return {"success": True}

        plugin._plex = plex_call  # type: ignore[method-assign]
        plugin._start_track_locked = start  # type: ignore[method-assign]
        return plugin, plex, starts

    def test_exit_at_the_real_end_advances_and_scrobbles(self) -> None:
        plugin, plex, starts = self._plugin(3_600_000 - 1000)
        asyncio.run(plugin._tick())
        self.assertEqual(starts, [(1, 0)])
        self.assertEqual(plex.scrobbles, [("ch-2", True)])
        self.assertEqual(plugin.tracks[0].view_offset_ms, 0)
        self.assertGreaterEqual(plugin.tracks[0].view_count, 1)
        self.assertEqual(plugin.tracks[1].view_count, 0)
        self.assertFalse(decky.logger.warnings)

    def test_early_clean_exit_resumes_without_scrobble(self) -> None:
        plugin, plex, starts = self._plugin(150_000)
        asyncio.run(self._two_ticks(plugin))
        self.assertEqual(starts, [(0, 150_000)])
        self.assertEqual(plex.scrobbles, [])
        self.assertEqual(plugin.index, 0)
        self.assertEqual(plugin.tracks[0].view_count, 0)
        self.assertEqual(plugin.tracks[0].view_offset_ms, 150_000)
        self.assertEqual(plex.stopped, [("ch-2", 150_000, False)])
        self.assertTrue(decky.logger.warnings)
        self.assertIn("150000 ms of 3600000 ms", decky.logger.warnings[-1])
        self.assertIn("ffplay exit 0", decky.logger.warnings[-1])
        self.assertNotIn("token", decky.logger.warnings[-1].lower())

    def test_repeated_early_exits_pause_without_advancing(self) -> None:
        plugin, plex, starts = self._plugin(150_000)

        async def drive() -> None:
            for _ in range(12):
                await plugin._tick()
                if plugin.last_error == PAUSE_MESSAGE:
                    return

        asyncio.run(drive())
        self.assertEqual(plugin.last_error, PAUSE_MESSAGE)
        self.assertEqual(len(starts), PREMATURE_RETRY_LIMIT)
        self.assertTrue(all(index == 0 and offset == 150_000 for index, offset in starts))
        self.assertEqual(plex.scrobbles, [])
        self.assertEqual(plugin.tracks[0].view_count, 0)
        self.assertEqual(plugin.tracks[0].view_offset_ms, 150_000)
        self.assertFalse(plugin.player.active)  # type: ignore[union-attr]

    async def _two_ticks(self, plugin: Plugin) -> None:
        decky.logger.warnings.clear()
        await plugin._tick()
        await plugin._tick()


if __name__ == "__main__":
    unittest.main()
