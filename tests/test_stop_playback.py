"""Stop and save progress unloads the book only after Plex accepts the position.

A book left loaded can later play, reconnect, or report progress and write this
Deck's position over listening done in another Plex app. A failed save keeps
the book loaded so that position is not dropped.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Other test modules install a decky stub before importing main. Replacing it
# here would make those tests log to a different object than the plugin uses.
if "decky" not in sys.modules:
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

import main  # noqa: E402
from main import SAVE_FAILED, Plugin  # noqa: E402
from progress import AudioTrack  # noqa: E402


class _Player:
    def __init__(self, position: int) -> None:
        self.active = True
        self.paused = False
        self.stopped = 0
        self.ffplay = "/usr/bin/ffplay"
        self._position = position

    def position_ms(self) -> int:
        return self._position

    def stop(self) -> None:
        self.stopped += 1
        self.active = False
        self.paused = False

    def process_alive(self) -> bool:
        return self.active

    def poll(self) -> int | None:
        return None if self.active else 0


class _Client:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, int, bool]] = []
        self.scrobbles: list[tuple[str, bool]] = []
        self.fail_stopped = False
        self.fresh_offset_ms = 500_000

    def timeline(
        self,
        key: str,
        state: str,
        position: int,
        _duration: int,
        finishing: bool = False,
        play_queue_item_id: str = "",
    ) -> None:
        if self.fail_stopped and state == "stopped":
            raise RuntimeError("plex down")
        self.events.append((state, key, position, finishing))

    def scrobble(self, key: str, played: bool) -> None:
        self.scrobbles.append((key, played))

    def get_book(self, album_key: str) -> tuple[dict, list[AudioTrack]]:
        track = AudioTrack(
            "ch-1",
            "Chapter 1",
            duration_ms=3_600_000,
            view_offset_ms=self.fresh_offset_ms,
            part_key="/library/parts/1",
        )
        return (
            {"rating_key": album_key, "title": "The Book", "author": "Author", "thumb": ""},
            [track],
        )


class _KindError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class _Probe:
    def timeline(self, *_args: object, **_kwargs: object) -> None:
        raise _KindError("timeline")

    def scrobble(self, *_args: object, **_kwargs: object) -> None:
        raise _KindError("scrobble")

    def clear_progress(self, *_args: object, **_kwargs: object) -> None:
        raise _KindError("scrobble")


def _call_kind(func) -> str:
    try:
        func(_Probe())
    except _KindError as err:
        return err.kind
    return "other"


class StopPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        warnings = getattr(main.decky.logger, "warnings", None)
        if isinstance(warnings, list):
            warnings.clear()

    def _plugin(self, position: int = 120_000) -> tuple[Plugin, _Client, _Player, list[int]]:
        plugin = Plugin()
        plugin.book = {"rating_key": "album-1", "title": "The Book", "author": "Author"}
        plugin.tracks = [
            AudioTrack("ch-1", "Chapter 1", duration_ms=3_600_000, view_offset_ms=5_000, part_key="1"),
        ]
        plugin.index = 0
        plugin.rest_offset_ms = 1
        plugin.cover = "cover"
        plugin._play_queue_item_id = "queue-9"
        plugin._resume_at = time.monotonic() + 30
        plugin._resume_index = 0
        plugin._resume_offset = position
        plugin._drop_retries = 2
        plugin.last_error = "Connection dropped. Reconnecting to this chapter."
        player = _Player(position)
        plugin.player = player  # type: ignore[assignment]
        client = _Client()
        kicks: list[int] = []

        async def plex_call(func):
            return func(client)

        plugin._plex = plex_call  # type: ignore[method-assign]
        plugin._kick_game_audio = lambda: kicks.append(1)  # type: ignore[method-assign]
        return plugin, client, player, kicks

    def test_successful_stop_unloads_and_does_not_report_again(self) -> None:
        plugin, client, player, kicks = self._plugin()

        async def run() -> None:
            await plugin._tick()
            result = await plugin.stop_playback()
            self.assertEqual(result, {"success": True})
            status = await plugin.get_status()
            self.assertFalse(status["has_book"])
            self.assertEqual(status["title"], "")
            self.assertEqual(status["book_position_ms"], 0)
            self.assertIsNone(status["last_error"])
            await plugin._tick()
            toggle = await plugin.toggle_playback()
            self.assertFalse(toggle["success"])

        asyncio.run(run())
        self.assertEqual(player.stopped, 1)
        self.assertEqual(
            client.events,
            [("playing", "ch-1", 120_000, False), ("stopped", "ch-1", 120_000, False)],
        )
        self.assertEqual(plugin.tracks, [])
        self.assertIsNone(plugin.book)
        self.assertEqual(plugin.cover, "")
        self.assertEqual(plugin._play_queue_item_id, "")
        self.assertEqual(plugin.rest_offset_ms, 0)
        self.assertEqual(plugin._resume_at, 0.0)
        self.assertEqual(plugin._drop_retries, 0)
        self.assertEqual(kicks, [1])

    def test_failed_save_keeps_the_book_and_the_position(self) -> None:
        plugin, client, player, kicks = self._plugin()
        client.fail_stopped = True

        async def run() -> tuple[dict, dict]:
            result = await plugin.stop_playback()
            status = await plugin.get_status()
            await plugin._tick()
            return result, status

        result, status = asyncio.run(run())
        self.assertFalse(result["success"])
        self.assertEqual(result["message"], SAVE_FAILED)
        self.assertEqual(player.stopped, 1)
        self.assertEqual(len(plugin.tracks), 1)
        self.assertEqual(plugin.book["title"], "The Book")  # type: ignore[index]
        self.assertEqual(plugin.cover, "cover")
        self.assertEqual(plugin._play_queue_item_id, "queue-9")
        self.assertEqual(plugin.rest_offset_ms, 120_000)
        self.assertEqual(plugin.tracks[0].view_offset_ms, 5_000)
        self.assertEqual(plugin._resume_at, 0.0)
        self.assertTrue(status["has_book"])
        self.assertFalse(status["playing"])
        self.assertEqual(status["chapter_position_ms"], 120_000)
        self.assertEqual(status["last_error"], SAVE_FAILED)
        self.assertEqual(client.events, [])
        self.assertEqual(kicks, [1])
        warnings = getattr(main.decky.logger, "warnings", [])
        self.assertTrue(warnings)
        self.assertNotIn("token", warnings[-1].lower())

    def test_retry_after_a_failed_save_reports_the_kept_position(self) -> None:
        plugin, client, player, _kicks = self._plugin()
        client.fail_stopped = True

        async def run() -> None:
            failed = await plugin.stop_playback()
            self.assertFalse(failed["success"])
            client.fail_stopped = False
            saved = await plugin.stop_playback()
            self.assertEqual(saved, {"success": True})

        asyncio.run(run())
        self.assertEqual(client.events, [("stopped", "ch-1", 120_000, False)])
        self.assertEqual(plugin.tracks, [])
        self.assertEqual(player.stopped, 2)

    def test_next_play_reads_the_plex_position(self) -> None:
        plugin, client, _player, _kicks = self._plugin()
        plugin.settings["server_url"] = "http://plex.local:32400"
        plugin.settings["token"] = "secret-token"
        plugin.settings["library_key"] = "1"
        client.fresh_offset_ms = 500_000
        started: list[tuple[int, int]] = []

        async def start(index: int, offset: int, user_initiated: bool = True) -> dict:
            started.append((index, offset))
            plugin.index = index
            plugin.rest_offset_ms = offset
            return {"success": True}

        plugin._start_track_locked = start  # type: ignore[method-assign]

        async def run() -> None:
            saved = await plugin.stop_playback()
            self.assertTrue(saved["success"])
            result = await plugin.play_book("album-1", -1, 0)
            self.assertTrue(result["success"])

        asyncio.run(run())
        self.assertEqual(started, [(0, 500_000)])
        self.assertEqual(plugin.tracks[0].view_offset_ms, 500_000)

    def test_stop_cancels_chapter_align_before_it_can_scrobble(self) -> None:
        plugin, client, _player, _kicks = self._plugin()
        plugin._running = True
        plugin.tracks = [
            AudioTrack("ch-1", "Chapter 1", duration_ms=3_600_000, part_key="1"),
            AudioTrack("ch-2", "Chapter 2", duration_ms=3_600_000, view_offset_ms=120_000, part_key="2"),
        ]
        plugin.index = 1
        release = asyncio.Event()
        started = asyncio.Event()

        async def plex_call(func):
            if _call_kind(func) == "scrobble":
                started.set()
                await release.wait()
            return func(client)

        plugin._plex = plex_call  # type: ignore[method-assign]

        async def run() -> None:
            plugin._schedule_align(1)
            task = plugin._align_task
            self.assertIsNotNone(task)
            await started.wait()
            result = await plugin.stop_playback()
            self.assertEqual(result, {"success": True})
            assert task is not None
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())

        asyncio.run(run())
        self.assertEqual(client.scrobbles, [])
        stopped = [event for event in client.events if event[0] == "stopped"]
        self.assertEqual(stopped, [("stopped", "ch-2", 120_000, False)])
        self.assertEqual(plugin.tracks, [])

    def test_nothing_loaded_does_not_report(self) -> None:
        plugin, client, player, kicks = self._plugin()
        plugin.tracks = []
        plugin.book = None

        async def run() -> dict:
            return await plugin.stop_playback()

        result = asyncio.run(run())
        self.assertEqual(result, {"success": True})
        self.assertEqual(player.stopped, 0)
        self.assertEqual(client.events, [])
        self.assertEqual(kicks, [])

    def test_sign_out_stop_still_leaves_the_book_loaded(self) -> None:
        plugin, client, player, kicks = self._plugin()
        client.fail_stopped = True

        async def run() -> dict:
            return await plugin._stop_locked()

        result = asyncio.run(run())
        self.assertTrue(result["success"])
        self.assertEqual(player.stopped, 1)
        self.assertEqual(len(plugin.tracks), 1)
        self.assertEqual(plugin.book["title"], "The Book")  # type: ignore[index]
        self.assertGreater(plugin._resume_at, 0)
        self.assertEqual(kicks, [1])
        self.assertEqual(client.events, [])


if __name__ == "__main__":
    unittest.main()
