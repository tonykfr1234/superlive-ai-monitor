"""Synthetic UI evidence only; no camera names, account data, or live device."""
import sys
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from superlive_recovery import PACKAGE, PREFIX, SuperLiveRecovery, channel_selected


def node(name, text="", area="[0,0][20,20]", **attrs):
    return ET.Element("node", {"resource-id": PREFIX + name, "package": PACKAGE,
                              "text": text, "bounds": area, "enabled": "true", **attrs})


class CameraClient:
    def __init__(self):
        self.state = "main"
        self.calls = []
        self.taps = []
        self.target = "Example recorder"
        self.current = "Other recorder"
        self.landscape = False
        self.selected = False
        self.settings = {"accelerometer_rotation": "0", "user_rotation": "3"}
        self.launcher = f"{PACKAGE}/com.tvt.launch.MainActivity"
        self.device_visible = True
        self.channel_visible = True
        self.device_duplicate = False
        self.channel_duplicate = False
        self.accept_channel = True
        self.valid_fullscreen = True
        self.startup_unknown = False
        self.startup_landscape = False
        self.scrolls = 0

    def run(self, *args, **kwargs):
        self.calls.append(args)
        if args[:4] == ("shell", "cmd", "package", "resolve-activity"):
            return self.launcher.encode()
        if args[:3] == ("shell", "settings", "get"):
            return self.settings[args[4]].encode()
        if args[:3] == ("shell", "settings", "put"):
            self.settings[args[4]] = args[5]
            if args[4:] == ("user_rotation", "0") and self.landscape:
                self.state = "fullscreen"
        if args[:3] == ("shell", "am", "start"):
            self.state = "unknown" if self.startup_unknown else "fullscreen" if self.startup_landscape else "main"
        if args == ("shell", "input", "keyevent", "4") and self.state == "land_channels":
            self.state = "fullscreen"
        if args[:3] == ("shell", "input", "swipe"):
            self.scrolls += 1
        if args == ("shell", "wm", "size"):
            return b"Physical size: 400x200\n"
        return b""

    def ui(self):
        root = ET.Element("hierarchy")
        nodes = []
        if self.state in ("main", "land_channels"):
            nodes = [node("live_video", area="[0,0][200,150]"),
                     node("ll_channel_parent"), node("tv_tab_channel_title", self.current),
                     node("iv_bottom_display"), node("iv_common_title_bar_back")]
            if self.channel_visible:
                nodes.append(node("tv_name", "04", "[20,200][60,240]"))
                if self.channel_duplicate:
                    nodes.append(node("tv_name", "4", "[80,200][120,240]"))
        elif self.state == "picker":
            nodes = [node("lv_device_list", area="[0,0][200,400]")]
            if self.device_visible:
                nodes.append(node("tv_device_name", self.target))
                if self.device_duplicate:
                    nodes.append(node("tv_device_name", self.target))
        elif self.state == "display":
            nodes = [node("iv_mode_1")]
        elif self.state == "drawer":
            nodes = [node("cl_local_config")]
        elif self.state == "settings":
            nodes = [node("slContainer", area="[0,0][200,400]"),
                     node("sw_landscape", checked=str(self.landscape).lower()),
                     node("iv_common_title_bar_back")]
        elif self.state in ("fullscreen", "controls"):
            nodes = [node("live_video", area="[0,0][400,200]")]
            if self.startup_landscape:
                nodes.append(node("iv_device"))
            if self.state == "controls":
                nodes.extend([node("video_menu_layout_land"), node("iv_bottom_display_land"),
                              node("iv_bottom_device_land")])
                if not self.startup_landscape:
                    nodes.append(node("video_menu_title_land"))
        root.extend(nodes)
        return root

    def tap(self, item):
        name = item.get("resource-id").removeprefix(PREFIX)
        self.taps.append(name)
        if name == "ll_channel_parent":
            self.state = "picker"
        elif name == "tv_device_name":
            self.current = item.get("text")
            self.state = "land_channels" if self.startup_landscape else "main"
        elif name in ("iv_bottom_display", "iv_bottom_display_land"):
            self.state = "display"
        elif name == "iv_mode_1":
            self.state = "fullscreen" if self.startup_landscape else "main"
        elif name == "iv_bottom_device_land":
            self.state = "land_channels"
        elif name == "tv_name":
            self.selected = self.accept_channel
        elif name == "iv_common_title_bar_back":
            self.state = "drawer" if self.state == "main" else "main"
        elif name == "cl_local_config":
            self.state = "settings"
        elif name == "sw_landscape":
            self.landscape = not self.landscape
        elif name == "live_video":
            self.state = "controls" if self.state == "fullscreen" else "fullscreen"
        elif name == "video_menu_title_land":
            self.state = "main"
            self.settings["user_rotation"] = "3"

    def screenshot(self):
        if self.state in ("fullscreen", "controls") and self.valid_fullscreen:
            return np.zeros((200, 400, 3), dtype=np.uint8)
        result = np.full((400, 200, 3), 240, dtype=np.uint8)
        if self.selected:
            result[205:235, 25:55] = (250, 185, 0)
        return result


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = CameraClient()
        self.recovery = SuperLiveRecovery(self.client, "Example recorder", 4)
        wait = patch.object(self.recovery, "_wait", side_effect=lambda *args: self.recovery._check())
        wait.start()
        self.addCleanup(wait.stop)

    def test_verified_single_channel_restored_fullscreen(self):
        self.assertTrue(self.recovery.restore())
        self.assertEqual(self.client.current, "Example recorder")
        self.assertEqual(self.client.state, "fullscreen")
        self.assertTrue(self.client.selected)
        self.assertTrue(self.client.landscape)
        self.assertEqual(self.client.settings["user_rotation"], "0")
        self.assertEqual(self.client.calls.count(("shell", "am", "force-stop", PACKAGE)), 1)
        self.assertIn("iv_mode_1", self.client.taps)
        self.assertNotIn("iv_CloseAllVideo", self.client.taps)

    def test_launcher_cannot_restart_another_app_or_accept_shell_syntax(self):
        for component in ("", "other.app/.Main", f"{PACKAGE}/.Main;bad",
                          f"{PACKAGE}/.Main$VALUE", "No activity found"):
            with self.subTest(component=component):
                self.client.launcher = component
                self.client.calls.clear()
                with self.assertRaisesRegex(RuntimeError, "launcher"):
                    self.recovery.restore()
                self.assertFalse(any(c[:3] == ("shell", "am", "force-stop") for c in self.client.calls))

    def test_landscape_only_startup_verifies_same_exact_device_and_channel(self):
        self.client.startup_landscape = True
        self.client.landscape = True
        self.assertTrue(self.recovery.restore())
        self.assertEqual(self.client.current, "Example recorder")
        self.assertTrue(self.client.selected)
        self.assertEqual(self.client.state, "fullscreen")
        self.assertIn("iv_bottom_device_land", self.client.taps)
        self.assertIn("iv_mode_1", self.client.taps)
        self.assertNotIn("cl_local_config", self.client.taps)
        self.assertNotIn("video_menu_title_land", self.client.taps)

    def test_landscape_startup_does_not_dismiss_unverified_channel(self):
        self.client.startup_landscape = True
        self.client.accept_channel = False
        with self.assertRaisesRegex(RuntimeError, "channel was not verified"):
            self.recovery.restore()
        self.assertNotIn(("shell", "input", "keyevent", "4"), self.client.calls)

    def test_already_verified_device_does_not_require_online_only_picker(self):
        self.client.current = self.client.target
        self.client.startup_landscape = True
        self.client.device_visible = False
        self.assertTrue(self.recovery.restore())
        self.assertIn("tv_name", self.client.taps)
        self.assertNotIn("tv_device_name", self.client.taps)
        self.assertNotIn("ll_channel_parent", self.client.taps)

    def test_fullsize_video_under_channel_drawer_is_not_fullscreen(self):
        root = ET.Element("hierarchy")
        root.extend([node("live_video", area="[0,0][400,200]"), node("ll_channel_parent")])
        self.client.state = "fullscreen"
        with patch.object(self.client, "ui", return_value=root), self.assertRaisesRegex(RuntimeError, "fullscreen"):
            self.recovery._verify_fullscreen()
        self.assertEqual(self.client.taps, [])

    def test_unknown_login_screen_never_receives_input(self):
        self.client.startup_unknown = True
        with self.assertRaisesRegex(RuntimeError, "screen"):
            self.recovery.restore()
        self.assertEqual(self.client.taps, [])

    def test_exact_device_required_and_scrolling_is_bounded(self):
        self.client.target = "Example recorder backup"
        with self.assertRaisesRegex(RuntimeError, "not found"):
            self.recovery.restore()
        self.assertEqual(self.client.scrolls, 5)
        self.assertNotIn("tv_device_name", self.client.taps)
        self.assertEqual(self.client.settings["user_rotation"], "3")

    def test_ambiguous_device_or_channel_is_rejected(self):
        for attribute in ("device_duplicate", "channel_duplicate"):
            with self.subTest(attribute=attribute):
                client = CameraClient()
                setattr(client, attribute, True)
                recovery = SuperLiveRecovery(client, client.target, 4)
                with patch.object(recovery, "_wait"), self.assertRaisesRegex(RuntimeError, "ambiguous"):
                    recovery.restore()

    def test_missing_or_unselected_channel_cannot_resume_capture(self):
        for attribute in ("channel_visible", "accept_channel"):
            with self.subTest(attribute=attribute):
                client = CameraClient()
                setattr(client, attribute, False)
                recovery = SuperLiveRecovery(client, client.target, 4)
                with patch.object(recovery, "_wait"), self.assertRaises(RuntimeError):
                    recovery.restore()
                self.assertNotIn("cl_local_config", client.taps)

    def test_failed_fullscreen_restores_both_display_preferences(self):
        self.client.valid_fullscreen = False
        with self.assertRaisesRegex(RuntimeError, "fullscreen"):
            self.recovery.restore()
        self.assertFalse(self.client.landscape)
        self.assertEqual(self.client.settings, {"accelerometer_rotation": "0", "user_rotation": "3"})

    def test_preserves_existing_landscape_preference_on_failure(self):
        self.client.landscape = True
        self.client.valid_fullscreen = False
        with self.assertRaises(RuntimeError):
            self.recovery.restore()
        self.assertTrue(self.client.landscape)
        self.assertNotIn("sw_landscape", self.client.taps)

    def test_expired_recovery_deadline_does_not_prevent_bounded_cleanup(self):
        self.client.state = "settings"
        self.client.landscape = True
        self.recovery._landscape_changed = True
        self.recovery._deadline = -1
        self.recovery._rotation_before = dict(self.client.settings)
        self.recovery._rollback_display()
        self.assertFalse(self.client.landscape)
        self.assertEqual(self.recovery._deadline, -1)

    def test_stop_before_restart_does_no_work(self):
        self.recovery.stop_event.set()
        with self.assertRaises(InterruptedError):
            self.recovery.restore()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.client.taps, [])

    def test_stop_during_wait_is_interruptible(self):
        event = threading.Event()
        event.set()
        recovery = SuperLiveRecovery(self.client, self.client.target, 4, event)
        with self.assertRaises(InterruptedError):
            recovery._wait(20)

    def test_invalid_target_configuration_is_rejected(self):
        for name, channel in (("", 4), (None, 4), ("Example", True), ("Example", 0),
                              ("Example", 65), ("Example", "4")):
            with self.subTest(name=name, channel=channel), self.assertRaises(ValueError):
                SuperLiveRecovery(self.client, name, channel)

    def test_selected_fill_requires_more_than_cyan_digits(self):
        candidate = node("tv_name", "04", "[0,0][40,40]")
        frame = np.full((40, 40, 3), 240, dtype=np.uint8)
        frame[10:13, 10:30] = (250, 185, 0)
        self.assertFalse(channel_selected(candidate, frame))
        frame[5:35, 5:35] = (250, 185, 0)
        self.assertTrue(channel_selected(candidate, frame))
        self.assertFalse(channel_selected(candidate, frame[:10]))


if __name__ == "__main__":
    unittest.main()
