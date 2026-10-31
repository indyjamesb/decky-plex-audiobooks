import os
import tempfile
import unittest

from game_audio import (
    GameMixer,
    VolumeBook,
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

    def test_pause_restores_original_when_game_volume_is_100(self):
        streams = parse_sink_inputs(LISTING)
        originals = {"FINAL FANTASY RESONANCE": [65536, 32768], "Surround Game": [111] * 12}
        actions, saved = plan_volumes(
            streams,
            {"222"},
            playing=False,
            game_volume=100,
            duck_level=30,
            auto_lower=True,
            originals=originals,
        )
        restored = {action.index: action.raw for action in actions}
        self.assertEqual(restored["10"], [65536, 32768])
        self.assertEqual(restored["11"], [65536, 32768])
        self.assertEqual(len(restored["14"]), 12)
        self.assertEqual(saved, {})

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
            raw_calls = [call for call in calls if call[0] == "set-sink-input-volume"]
            self.assertEqual(raw_calls[0][:3], ["set-sink-input-volume", "10", "65536"])
            self.assertEqual(raw_calls[0][3], "65536")
            self.assertEqual(VolumeBook(path).apps, {})

    def test_missing_pactl_does_not_raise(self):
        notes: list[str] = []
        mixer = GameMixer(lambda _args: None, os.path.join(tempfile.gettempdir(), "missing-game-audio.json"), notes.append)
        self.assertFalse(mixer.sync(playing=True, own_pids=set(), game_volume=100, duck_level=30, auto_lower=True))
        self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
