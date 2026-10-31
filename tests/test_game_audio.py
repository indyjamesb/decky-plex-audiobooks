import os
import tempfile
import unittest

from game_audio import (
    GameMixer,
    VolumeBook,
    at_percent,
    is_own_stream,
    is_system_chatter,
    parse_sink_inputs,
    plan_volumes,
    playing_level,
)

CHANNELS = [
    "front-left",
    "front-right",
    "rear-left",
    "rear-right",
    "front-center",
    "lfe",
    "side-left",
    "side-right",
    "top-front-left",
    "top-front-right",
    "top-rear-left",
    "top-rear-right",
]


def _volume_block(values: list[int]) -> str:
    parts = [f"{name}: {value} / {round(value / 65536 * 100)}% / 0.00 dB" for name, value in zip(CHANNELS, values)]
    first = ", ".join(parts[:4])
    second = ", ".join(parts[4:8])
    third = ", ".join(parts[8:])
    return f"\tVolume: {first}\n\t        {second}\n\t        {third}\n"


LISTING = """Sink Input #10
\tDriver: PipeWire
\tVolume: front-left: 65536 / 100% / 0.00 dB, front-right: 65536 / 100% / 0.00 dB
\tMute: no
\tProperties:
\t\tapplication.name = "FINAL FANTASY RESONANCE"
\t\tapplication.process.binary = "wine64-preloader"
\t\tapplication.process.id = "4000"
Sink Input #11
\tVolume: front-left: 65536 / 100% / 0.00 dB, front-right: 32768 / 50% / -6.00 dB
\tProperties:
\t\tapplication.name = "FINAL FANTASY RESONANCE"
\t\tapplication.process.binary = "wine64-preloader"
\t\tapplication.process.id = "4001"
Sink Input #12
\tVolume: front-left: 52428 / 80% / -1.00 dB
\tProperties:
\t\tapplication.name = "ffplay"
\t\tapplication.process.binary = "ffplay"
\t\tapplication.process.id = "222"
Sink Input #13
\tVolume: front-left: 65536 / 100% / 0.00 dB
\tProperties:
\t\tapplication.name = "speech-dispatcher-dummy"
\t\tapplication.process.binary = "sd_dummy"
\t\tapplication.process.id = "50"
""" + (
    "Sink Input #14\n" + _volume_block([65536 - index * 1000 for index in range(12)]) +
    "\tProperties:\n\t\tapplication.name = \"Surround Game\"\n"
    "\t\tapplication.process.binary = \"game\"\n"
    "\t\tapplication.process.id = \"9\"\n"
)


class ParseTests(unittest.TestCase):
    def test_parses_games_book_and_twelve_channels(self):
        streams = parse_sink_inputs(LISTING)
        by_index = {stream.index: stream for stream in streams}
        self.assertEqual(by_index["10"].name, "FINAL FANTASY RESONANCE")
        self.assertEqual(by_index["10"].binary_name, "wine64-preloader")
        self.assertEqual(by_index["10"].volumes, [65536, 65536])
        self.assertEqual(by_index["11"].volumes, [65536, 32768])
        self.assertEqual(by_index["12"].pid, "222")
        self.assertTrue(is_own_stream(by_index["12"], {"222"}))
        self.assertFalse(is_own_stream(by_index["10"], {"222"}))
        self.assertTrue(is_system_chatter(by_index["13"]))
        self.assertEqual(len(by_index["14"].volumes), 12)
        self.assertEqual(by_index["14"].volumes[0], 65536)
        self.assertEqual(by_index["14"].volumes[11], 65536 - 11000)

    def test_duck_skips_our_ffplay_and_speech_dispatcher(self):
        streams = parse_sink_inputs(LISTING)
        actions, saved = plan_volumes(
            streams,
            {"222"},
            playing=True,
            game_volume=100,
            duck_level=30,
            auto_lower=True,
            originals={},
        )
        indexes = [action.index for action in actions]
        self.assertEqual(indexes, ["10", "11", "14"])
        self.assertTrue(all(action.percent == 30 for action in actions))
        self.assertEqual(saved["FINAL FANTASY RESONANCE"], [65536, 65536])
        self.assertEqual(len(saved["Surround Game"]), 12)

    def test_game_volume_100_sets_every_channel_even_without_originals(self):
        streams = parse_sink_inputs(LISTING)
        twelve = next(stream for stream in streams if stream.index == "14")
        self.assertEqual(len(twelve.volumes), 12)
        self.assertEqual(len(twelve.percents), 12)
        actions, saved = plan_volumes(
            streams,
            {"222"},
            playing=False,
            game_volume=100,
            duck_level=50,
            auto_lower=True,
            originals={},
        )
        by_index = {action.index: action for action in actions}
        self.assertEqual(set(by_index), {"10", "11", "14"})
        self.assertTrue(all(action.percent == 100 and action.raw is None for action in actions))
        self.assertEqual(saved, {})
        self.assertFalse(at_percent(twelve, 50))

    def test_pause_uses_game_volume_when_it_is_below_100(self):
        streams = parse_sink_inputs(LISTING)
        actions, saved = plan_volumes(
            streams,
            {"222"},
            playing=False,
            game_volume=70,
            duck_level=30,
            auto_lower=True,
            originals={"FINAL FANTASY RESONANCE": [65536, 65536]},
        )
        self.assertTrue(all(action.percent == 70 for action in actions))
        self.assertIn("FINAL FANTASY RESONANCE", saved)

    def test_duck_uses_the_lower_of_game_volume_and_duck_level(self):
        self.assertEqual(playing_level(100, 30, True), 30)
        self.assertEqual(playing_level(20, 30, True), 20)
        streams = parse_sink_inputs(LISTING)
        actions, _saved = plan_volumes(
            streams,
            {"222"},
            playing=True,
            game_volume=20,
            duck_level=30,
            auto_lower=True,
            originals={},
        )
        self.assertEqual(actions[0].percent, 20)


class MixerTests(unittest.TestCase):
    def test_sync_and_state_file_round_trip(self):
        calls: list[list[str]] = []

        def run(args: list[str]):
            calls.append(args)
            if args[:2] == ["list", "sink-inputs"]:
                return 0, LISTING
            return 0, ""

        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "game-audio.json")
            mixer = GameMixer(run, path, lambda _message: None)
            mixer.sync(playing=True, own_pids={"222"}, game_volume=100, duck_level=30, auto_lower=True)
            self.assertEqual(calls[-1], ["set-sink-input-volume", "14", "30%"])
            stored = VolumeBook(path)
            self.assertEqual(stored.apps["FINAL FANTASY RESONANCE"], [65536, 65536])
            calls.clear()
            mixer.sync(playing=False, own_pids={"222"}, game_volume=100, duck_level=30, auto_lower=True)
            sets = [call for call in calls if call[0] == "set-sink-input-volume"]
            self.assertEqual(sets[0], ["set-sink-input-volume", "10", "100%"])
            self.assertEqual(sets[-1], ["set-sink-input-volume", "14", "100%"])
            self.assertEqual(VolumeBook(path).apps, {})

    def test_exit_restores_originals_before_clearing_the_state_file(self):
        calls: list[list[str]] = []
        fail = {"set": True}

        def run(args: list[str]):
            calls.append(args)
            if args[:2] == ["list", "sink-inputs"]:
                return 0, LISTING
            if fail["set"]:
                return 1, ""
            return 0, ""

        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "game-audio.json")
            book = VolumeBook(path)
            book.replace({"FINAL FANTASY RESONANCE": [65536, 32768]})
            mixer = GameMixer(run, path, lambda _message: None)
            self.assertFalse(mixer.restore_on_exit(100))
            self.assertEqual(VolumeBook(path).apps["FINAL FANTASY RESONANCE"], [65536, 32768])
            fail["set"] = False
            calls.clear()
            self.assertTrue(mixer.restore_on_exit(100))
            raw = [call for call in calls if call[0] == "set-sink-input-volume"][0]
            self.assertEqual(raw, ["set-sink-input-volume", "10", "65536", "32768"])
            self.assertEqual(VolumeBook(path).apps, {})

    def test_startup_lifts_streams_left_at_the_duck_level(self):
        stuck = """Sink Input #4921
\tVolume: front-left: 32768 / 50% / -6.00 dB, front-right: 32768 / 50% / -6.00 dB, rear-left: 32768 / 50% / -6.00 dB, rear-right: 32768 / 50% / -6.00 dB
\t        front-center: 32768 / 50% / -6.00 dB, lfe: 32768 / 50% / -6.00 dB, side-left: 32768 / 50% / -6.00 dB, side-right: 32768 / 50% / -6.00 dB
\t        top-front-left: 32768 / 50% / -6.00 dB, top-front-right: 32768 / 50% / -6.00 dB, top-rear-left: 32768 / 50% / -6.00 dB, top-rear-right: 32768 / 50% / -6.00 dB
\tProperties:
\t\tapplication.name = "FINAL FANTASY RESONANCE"
\t\tapplication.process.binary = "wine64-preloader"
\t\tapplication.process.id = "4000"
Sink Input #4926
\tVolume: front-left: 64225 / 98% / -0.17 dB, front-right: 64225 / 98% / -0.17 dB
\tProperties:
\t\tapplication.name = "FINAL FANTASY RESONANCE"
\t\tapplication.process.binary = "wine64-preloader"
\t\tapplication.process.id = "4001"
"""
        notes: list[str] = []
        calls: list[list[str]] = []

        def run(args: list[str]):
            calls.append(args)
            if args[:2] == ["list", "sink-inputs"]:
                return 0, stuck
            return 0, ""

        with tempfile.TemporaryDirectory() as root:
            mixer = GameMixer(run, os.path.join(root, "game-audio.json"), notes.append)
            self.assertEqual(mixer.book.apps, {})
            self.assertEqual(mixer.recover_stuck(game_volume=100, duck_level=50, own_pids=set()), 1)
            sets = [call for call in calls if call[0] == "set-sink-input-volume"]
            self.assertEqual(sets, [["set-sink-input-volume", "4921", "100%"]])
            self.assertTrue(any("50%" in note and "100%" in note for note in notes))

    def test_missing_pactl_does_not_raise(self):
        notes: list[str] = []
        mixer = GameMixer(lambda _args: None, os.path.join(tempfile.gettempdir(), "missing-game-audio.json"), notes.append)
        self.assertFalse(mixer.sync(playing=True, own_pids=set(), game_volume=100, duck_level=30, auto_lower=True))
        self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
