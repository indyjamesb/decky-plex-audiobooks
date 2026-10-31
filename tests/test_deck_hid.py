import os
import tempfile
import unittest

from deck_hid import (
    ButtonCapture,
    ShortcutEdge,
    decode_hidraw,
    decode_report,
    key_codes_from_bitmap,
    list_controller_hidraw,
    list_deck_hidraw,
    migrate_shortcut,
    report_with_buttons,
    should_open_evdev,
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
        cap.feed(set(), 1.0)
        self.assertEqual(cap.phase, "listen")
        self.assertEqual(cap.buttons, ["R4"])
        self.assertEqual(cap.status(1.9)["phase"], "done")
        self.assertEqual(cap.buttons, ["R4"])

    def test_window_collects_presses_that_do_not_overlap(self):
        cap = ButtonCapture(0.0)
        cap.feed(set(), 0.0)
        cap.feed({"R4"}, 1.0)
        cap.feed(set(), 1.2)
        cap.feed({"L4"}, 1.4)
        self.assertEqual(cap.status(2.4)["phase"], "listen")
        done = cap.status(2.5)
        self.assertEqual(done["phase"], "done")
        self.assertEqual(cap.buttons, ["L4", "R4"])
        self.assertIn("Deck R4", done["label"])
        self.assertIn("left back", done["label"])

    def test_one_edge_debounces_across_devices(self):
        edge = ShortcutEdge(["R4"], debounce_s=0.4)
        self.assertTrue(edge.feed({"R4"}, 0.0))
        self.assertFalse(edge.feed(set(), 0.1))
        self.assertFalse(edge.feed({"R4"}, 0.2))
        self.assertFalse(edge.feed(set(), 0.3))
        self.assertTrue(edge.feed({"R4"}, 0.7))


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


class OtherControllerTests(unittest.TestCase):
    def test_steam_controller_back_buttons_only(self):
        right = bytearray(8)
        right[0] = 0x42
        right[1] = 0x99
        right[2] = 0x80
        self.assertEqual(decode_hidraw(bytes(right), "steam-controller"), {"R4"})
        left = bytearray(8)
        left[0] = 0x42
        left[4] = 0x02
        left[5] = 0x30
        self.assertEqual(decode_hidraw(bytes(left), "steam-controller"), {"L4"})
        touch = bytearray(8)
        touch[0] = 0x42
        touch[5] = 0x30
        self.assertEqual(decode_hidraw(bytes(touch), "steam-controller"), set())
        self.assertIsNone(decode_hidraw(bytes([0x7B, 0, 0x80]), "steam-controller"))
        self.assertIsNone(decode_hidraw(bytes([0x43, 0, 0, 0, 0x02]), "steam-controller"))

    def test_xbox_bluetooth_buttons(self):
        data = bytearray(17)
        data[0] = 0x01
        data[14] = 0x01
        self.assertEqual(decode_hidraw(bytes(data), "xbox"), {"XBOX_A"})
        data[14] = 0x80
        data[15] = 0x04
        data[16] = 0x01
        self.assertEqual(decode_hidraw(bytes(data), "xbox"), {"XBOX_RB", "XBOX_VIEW", "XBOX_SHARE"})

    def test_dualsense_bluetooth_masks_dpad(self):
        data = bytearray(78)
        data[0] = 0x31
        data[9] = 0x08 | 0x20
        self.assertEqual(decode_hidraw(bytes(data), "dualsense"), {"DS_CROSS"})
        data[9] = 0x18
        data[10] = 0x01
        data[11] = 0x04
        self.assertEqual(decode_hidraw(bytes(data), "dualsense"), {"DS_SQUARE", "DS_L1", "DS_MUTE"})

    def test_dualsense_usb_layout_is_one_byte_earlier(self):
        data = bytearray(64)
        data[0] = 0x01
        data[8] = 0x28
        data[9] = 0x02
        data[10] = 0x04
        self.assertEqual(decode_hidraw(bytes(data), "dualsense-usb"), {"DS_CROSS", "DS_R1", "DS_MUTE"})

    def test_switch_pro_ignores_wired_flag(self):
        data = bytearray(64)
        data[0] = 0x30
        data[3] = 0x08
        data[4] = 0x80 | 0x20
        data[5] = 0x40
        self.assertEqual(decode_hidraw(bytes(data), "switch-pro"), {"NS_A", "NS_CAPTURE", "NS_L"})
        self.assertEqual(decode_hidraw(bytes(data), "switch-pro-bt"), {"NS_A", "NS_CAPTURE", "NS_L"})

    def test_scan_uses_vid_pid_for_both_buses(self):
        with tempfile.TemporaryDirectory() as root:
            def add(name: str, hid_id: str) -> None:
                device = os.path.join(root, name, "device")
                os.makedirs(device)
                with open(os.path.join(device, "uevent"), "w", encoding="utf-8") as handle:
                    handle.write(f"HID_ID={hid_id}\n")

            add("hidraw9", "0003:000028DE:00001205")
            add("hidraw1", "0005:0000045E:00000B13")
            add("hidraw3", "0003:000028DE:00001304")
            add("hidraw4", "0003:000028DE:000011FF")
            found = dict(list_controller_hidraw(root))
            self.assertEqual(found["/dev/hidraw9"], "deck")
            self.assertEqual(found["/dev/hidraw1"], "xbox")
            self.assertEqual(found["/dev/hidraw3"], "steam-controller")
            self.assertNotIn("/dev/hidraw4", found)

    def test_evdev_skips_hidraw_pads_and_valve_devices(self):
        self.assertFalse(should_open_evdev(0x28DE, 0x11FF, {0x130}))
        self.assertFalse(should_open_evdev(0x28DE, 0x1205, {0x130}))
        self.assertFalse(should_open_evdev(0x045E, 0x0B13, {0x130}))
        self.assertFalse(should_open_evdev(0x1234, 0x0001, set()))
        self.assertTrue(should_open_evdev(0x045E, 0x028E, {0x130}))
        words = ["0"] * 5
        words[0] = format(1 << 48, "x")
        self.assertIn(0x130, key_codes_from_bitmap(" ".join(words)))


if __name__ == "__main__":
    unittest.main()
