import os
import tempfile
import unittest

from deck_hid import (
    ButtonCapture,
    ShortcutEdge,
    decode_report,
    list_deck_hidraw,
    migrate_shortcut,
    report_with_buttons,
)


class DeckReportTests(unittest.TestCase):
    def test_captured_r4_l4_and_ignored_touch(self):
        # Bytes 8-15 from an in-game capture. R4 is d[13] bit 0x04, L4 is bit 0x02.
        self.assertEqual(decode_report(report_with_buttons("0000000000040000")), {"R4"})
        self.assertEqual(decode_report(report_with_buttons("0000000000020000")), {"L4"})
        # 0x84 is R4 plus the right-stick touch bit 0x80, which is not a button.
        self.assertEqual(decode_report(report_with_buttons("0000000000840000")), {"R4"})
        self.assertEqual(decode_report(report_with_buttons("0000000000800000")), set())

    def test_ignores_non_input_reports(self):
        data = bytearray(report_with_buttons("0000000000040000"))
        data[2] = 0x01
        self.assertIsNone(decode_report(bytes(data)))
        self.assertIsNone(decode_report(b"\x01\x00\x09"))


class ShortcutEdgeTests(unittest.TestCase):
    def test_single_button_press_and_debounce(self):
        edge = ShortcutEdge(["R4"], debounce_s=0.4)
        self.assertTrue(edge.feed({"R4"}, 0.0))
        self.assertFalse(edge.feed({"R4"}, 0.1))
        self.assertFalse(edge.feed(set(), 0.2))
        self.assertFalse(edge.feed({"R4"}, 0.3))
        self.assertFalse(edge.feed(set(), 0.5))
        self.assertTrue(edge.feed({"R4"}, 0.8))

    def test_combo_fires_when_all_held_together(self):
        edge = ShortcutEdge(["R4", "L4"], debounce_s=0.4)
        self.assertEqual(edge.buttons, ["L4", "R4"])
        self.assertFalse(edge.feed({"L4"}, 0.0))
        self.assertTrue(edge.feed({"L4", "R4"}, 0.1))
        self.assertFalse(edge.feed({"L4", "R4"}, 0.2))
        self.assertFalse(edge.feed({"R4"}, 0.3))
        self.assertFalse(edge.feed({"L4", "R4"}, 0.35))
        self.assertFalse(edge.feed(set(), 1.0))
        self.assertTrue(edge.feed({"L4", "R4"}, 1.1))

    def test_other_buttons_do_not_fire(self):
        edge = ShortcutEdge(["R4"])
        self.assertFalse(edge.feed({"A", "B"}, 0.0))
        self.assertFalse(edge.feed(set(), 1.0))


class ButtonCaptureTests(unittest.TestCase):
    def test_ignores_the_button_that_opened_program(self):
        cap = ButtonCapture(0.0)
        cap.feed({"A"}, 0.0)
        self.assertEqual(cap.phase, "wait_release")
        self.assertEqual(cap.buttons, [])
        cap.feed(set(), 0.2)
        self.assertEqual(cap.phase, "listen")
        cap.feed({"R4"}, 0.4)
        cap.feed({"R4"}, 0.8)
        self.assertEqual(cap.phase, "listen")
        cap.feed(set(), 1.0)
        self.assertEqual(cap.phase, "done")
        self.assertEqual(cap.buttons, ["R4"])

    def test_combo_finishes_on_release_or_two_second_hold(self):
        cap = ButtonCapture(0.0)
        cap.feed(set(), 0.0)
        cap.feed({"R4"}, 1.0)
        cap.feed({"L4", "R4"}, 1.2)
        cap.feed(set(), 1.5)
        self.assertEqual(cap.status(1.5)["phase"], "done")
        self.assertEqual(cap.buttons, ["L4", "R4"])
        self.assertEqual(cap.status(1.5)["label"], "L4 + R4")

        held = ButtonCapture(0.0)
        held.feed(set(), 0.0)
        held.feed({"L4", "R4"}, 1.0)
        self.assertAlmostEqual(held.status(2.5)["seconds_left"], 0.5)
        self.assertEqual(held.status(3.0)["phase"], "done")
        self.assertEqual(held.buttons, ["L4", "R4"])


class MigrationTests(unittest.TestCase):
    def test_action_45_and_default_r4(self):
        self.assertEqual(migrate_shortcut({"play_pause_actions": [45]}), ["R4"])
        self.assertEqual(migrate_shortcut({"play_pause_actions": [44, 45]}), ["L4", "R4"])
        self.assertEqual(migrate_shortcut({"play_pause_actions": []}), ["R4"])
        self.assertEqual(migrate_shortcut({}), ["R4"])
        self.assertEqual(migrate_shortcut({"play_pause_mode": "off"}), ["R4"])
        self.assertEqual(migrate_shortcut({"play_pause_buttons": ["R4"]}), ["R4"])
        self.assertEqual(
            migrate_shortcut({"play_pause_mode": "combo", "play_pause_combo": ["Select", "Y"]}),
            ["Y", "View"],
        )
        self.assertEqual(
            migrate_shortcut({"play_pause_mode": "back", "play_pause_back": "L4"}),
            ["L4"],
        )
        self.assertEqual(
            migrate_shortcut({"play_pause_source": "hidraw", "play_pause_buttons": []}),
            [],
        )
        self.assertEqual(
            migrate_shortcut({"play_pause_source": "hidraw", "play_pause_buttons": ["R4", "L4"]}),
            ["L4", "R4"],
        )


class HidrawScanTests(unittest.TestCase):
    def test_lists_only_deck_controller_nodes(self):
        with tempfile.TemporaryDirectory() as root:
            def add(name: str, hid_id: str) -> None:
                device = os.path.join(root, name, "device")
                os.makedirs(device)
                with open(os.path.join(device, "uevent"), "w", encoding="utf-8") as handle:
                    handle.write(f"HID_ID={hid_id}\n")

            add("hidraw2", "0003:000028DE:00001205")
            add("hidraw0", "0003:000028DE:00001142")
            add("hidraw1", "0003:00000545:00000001")
            self.assertEqual(list_deck_hidraw(root), ["/dev/hidraw2"])


if __name__ == "__main__":
    unittest.main()
