"""Bounded, fail-closed navigation back to one configured SuperLive camera.

Device names are runtime configuration, never diagnostic output. The selectors
below were observed in SuperLive Plus; an unknown/login screen stops recovery.
"""
import re
import threading
import time

import numpy as np


PACKAGE = "com.tvt.superliveplus"
PREFIX = PACKAGE + ":id/"


def bounds(node):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if not match:
        raise RuntimeError("Camera control bounds are unavailable")
    left, top, right, bottom = map(int, match.groups())
    if right <= left or bottom <= top:
        raise RuntimeError("Camera control bounds are empty")
    return left, top, right, bottom


def channel_selected(node, frame):
    """The real channel grid does not expose checked/selected accessibility state.

    Its selected channel has a cyan filled circle; unselected channels have only
    cyan digits on grey. Require a substantial filled area, not a few glyphs.
    """
    if node.get("selected") == "true" or node.get("checked") == "true":
        return True
    left, top, right, bottom = bounds(node)
    if (not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] < 3
            or right > frame.shape[1] or bottom > frame.shape[0]):
        return False
    patch = frame[top:bottom, left:right, :3].astype(np.int16)
    blue, green, red = patch[..., 0], patch[..., 1], patch[..., 2]
    cyan = ((blue > 160) & (green > 120) & (red < 110)
            & (blue - red > 80) & (green - red > 60))
    return bool(cyan.mean() > 0.30)


class SuperLiveRecovery:
    """Restart only SuperLive, select an exact device/channel, restore fullscreen.

    The app's Landscape operation preference and Android display rotation may
    need changing to restore the camera's full native landscape frame. Those
    display-only changes are retained on success, and rolled back on failure.
    The caller must confirm fresh frames and recheck stream quality afterwards.
    """

    def __init__(self, client, device_name, channel, stop_event=None):
        if not isinstance(device_name, str) or not device_name.strip():
            raise ValueError("Recovery requires a configured camera device")
        if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 64:
            raise ValueError("Recovery channel must be an integer between 1 and 64")
        self.client = client
        self.device_name = device_name.strip()
        self.channel = channel
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self._deadline = None
        self._rotation_before = {}
        self._landscape_changed = False
        self._stage = "initialization"

    def _check(self):
        if self.stop_event.is_set():
            raise InterruptedError("Camera recovery stopped")
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise RuntimeError("Camera recovery timed out")

    def _wait(self, seconds=0.4):
        self._check()
        if self.stop_event.wait(seconds):
            raise InterruptedError("Camera recovery stopped")
        self._check()

    def _run(self, *args, timeout=12):
        self._check()
        return self.client.run(*args, timeout=timeout)

    @staticmethod
    def _text(value):
        return value.decode("utf-8", "replace").strip() if isinstance(value, bytes) else str(value).strip()

    @staticmethod
    def _nodes(tree, name):
        return [n for n in tree.iter("node") if n.get("package") == PACKAGE
                and n.get("resource-id") == PREFIX + name]

    def _node(self, tree, name, required=True):
        nodes = self._nodes(tree, name)
        if len(nodes) == 1:
            return nodes[0]
        if required or len(nodes) > 1:
            raise RuntimeError("Expected camera control is unavailable or ambiguous")
        return None

    def _ui(self):
        self._check()
        return self.client.ui()

    def _tap(self, node):
        self._check()
        if node.get("package") != PACKAGE or node.get("enabled") == "false":
            raise RuntimeError("Camera control is not available")
        bounds(node)
        self.client.tap(node)

    def _wait_for(self, predicate, attempts=6):
        for attempt in range(attempts):
            tree = self._ui()
            if predicate(tree):
                return tree
            if attempt + 1 < attempts:
                self._wait()
        raise RuntimeError("Expected SuperLive screen did not appear during " + self._stage)

    def _scroll(self, node):
        left, top, right, bottom = bounds(node)
        x = (left + right) // 2
        margin = max(1, (bottom - top) // 8)
        self._run("shell", "input", "swipe", str(x), str(bottom - margin),
                  str(x), str(top + margin), "350")

    def _portrait_main(self, tree):
        if self._node(tree, "ll_channel_parent", False) is not None:
            return tree
        if self._node(tree, "video_menu_title_land", False) is None:
            video = self._node(tree, "live_video")
            self._tap(video)
            tree = self._wait_for(lambda t: bool(self._nodes(t, "video_menu_title_land")), 3)
        self._tap(self._node(tree, "video_menu_title_land"))
        return self._wait_for(lambda t: bool(self._nodes(t, "ll_channel_parent")))

    def _select_device(self, tree):
        # The channel drawer's exact device title binds its channel buttons to
        # the configured recorder. The picker can omit temporarily offline
        # recorders, so retain an already verified title and reconnect its
        # explicit channel instead of accidentally choosing an online neighbor.
        if self._device_matches(tree):
            return tree
        self._tap(self._node(tree, "ll_channel_parent"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, "lv_device_list")))
        if not self._nodes(tree, "tv_device_name"):
            tree = self._wait_for(lambda t: bool(self._nodes(t, "tv_device_name")), 4)
        for page in range(6):
            matches = [n for n in self._nodes(tree, "tv_device_name")
                       if n.get("text", "").strip() == self.device_name]
            if len(matches) > 1:
                raise RuntimeError("Configured camera device is ambiguous")
            if matches:
                self._tap(matches[0])
                return self._wait_for(self._device_matches)
            if page < 5:
                self._scroll(self._node(tree, "lv_device_list"))
                tree = self._ui()
        raise RuntimeError("Configured camera device was not found")

    def _device_matches(self, tree):
        title = self._node(tree, "tv_tab_channel_title", False)
        return title is not None and title.get("text", "").strip() == self.device_name

    def _channel_node(self, tree):
        candidates = [n for n in self._nodes(tree, "tv_name")
                      if n.get("text", "").strip() in (str(self.channel), f"{self.channel:02d}")]
        if len(candidates) != 1:
            raise RuntimeError("Configured camera channel was not found or is ambiguous")
        return candidates[0]

    def _channel_available(self, tree):
        return self._device_matches(tree) and any(
            n.get("text", "").strip() in (str(self.channel), f"{self.channel:02d}")
            for n in self._nodes(tree, "tv_name"))

    def _verify_channel(self, tree):
        if not self._device_matches(tree):
            raise RuntimeError("Configured camera device was not verified")
        if not channel_selected(self._channel_node(tree), self.client.screenshot()):
            raise RuntimeError("Configured camera channel was not verified")

    def _select_channel(self, tree):
        self._tap(self._node(tree, "iv_bottom_display"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, "iv_mode_1")))
        self._tap(self._node(tree, "iv_mode_1"))
        tree = self._wait_for(self._channel_available, 12)
        self._tap(self._channel_node(tree))
        self._wait(0.5)
        tree = self._ui()
        self._verify_channel(tree)
        return tree

    def _landscape_control(self, tree, name):
        control = self._node(tree, name, False)
        if control is not None:
            return control
        self._tap(self._node(tree, "live_video"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, name)), 3)
        return self._node(tree, name)

    def _select_landscape_camera(self, tree):
        # With Landscape operation enabled, a fresh app launch uses a different
        # main view with no portrait Back button. Its device drawer exposes the
        # same exact device title and channel grid as the portrait view.
        self._stage = "landscape single-camera layout"
        self._tap(self._landscape_control(tree, "iv_bottom_display_land"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, "iv_mode_1")))
        self._tap(self._node(tree, "iv_mode_1"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, "live_video")))
        self._stage = "landscape device selection"
        self._tap(self._landscape_control(tree, "iv_bottom_device_land"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, "ll_channel_parent")))
        tree = self._select_device(tree)
        self._stage = "landscape channel verification"
        tree = self._wait_for(self._channel_available, 12)
        self._tap(self._channel_node(tree))
        self._wait(0.5)
        tree = self._ui()
        self._verify_channel(tree)
        # BACK is only sent while the verified channel drawer is visible.
        self._run("shell", "input", "keyevent", "4")
        self._stage = "landscape fullscreen verification"
        self._verify_fullscreen()

    def _landscape_switch(self):
        tree = self._wait_for(self._device_matches, 3)
        self._tap(self._node(tree, "iv_common_title_bar_back"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, "cl_local_config")), 3)
        self._tap(self._node(tree, "cl_local_config"))
        tree = self._wait_for(lambda t: bool(self._nodes(t, "slContainer")), 3)
        for page in range(4):
            toggle = self._node(tree, "sw_landscape", False)
            if toggle is not None:
                return tree, toggle
            if page < 3:
                self._scroll(self._node(tree, "slContainer"))
                tree = self._ui()
        raise RuntimeError("SuperLive landscape control is unavailable")

    def _enable_landscape(self):
        tree, toggle = self._landscape_switch()
        if toggle.get("checked") not in ("true", "false"):
            raise RuntimeError("SuperLive landscape preference cannot be verified")
        if toggle.get("checked") != "true":
            self._landscape_changed = True
            self._tap(toggle)
            tree = self._wait_for(lambda t: any(n.get("checked") == "true"
                                              for n in self._nodes(t, "sw_landscape")), 3)
        self._tap(self._node(tree, "iv_common_title_bar_back"))
        tree = self._wait_for(self._device_matches)
        self._verify_channel(tree)

    def _remember_rotation(self):
        for name in ("accelerometer_rotation", "user_rotation"):
            value = self._text(self._run("shell", "settings", "get", "system", name))
            allowed = ("0", "1") if name == "accelerometer_rotation" else ("0", "1", "2", "3")
            if value not in allowed:
                raise RuntimeError("Android display rotation cannot be verified")
            self._rotation_before[name] = value

    def _enter_fullscreen(self):
        self._enable_landscape()
        size = self._text(self._run("shell", "wm", "size"))
        matches = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", size)
        if not matches:
            raise RuntimeError("Android display size cannot be verified")
        width, height = map(int, matches[-1])
        if width <= 0 or height <= 0 or width == height:
            raise RuntimeError("Android landscape dimensions are unavailable")
        target_rotation = "0" if width > height else "1"
        self._run("shell", "settings", "put", "system", "accelerometer_rotation", "0")
        self._run("shell", "settings", "put", "system", "user_rotation", target_rotation)
        self._wait(0.5)
        self._verify_fullscreen()

    def _verify_fullscreen(self):
        for attempt in range(4):
            tree = self._ui()
            video = self._node(tree, "live_video", False)
            frame = self.client.screenshot()
            if video is not None and frame.shape[1] > frame.shape[0]:
                left, top, right, bottom = bounds(video)
                if (left, top, right, bottom) == (0, 0, frame.shape[1], frame.shape[0]):
                    drawer_open = any(self._nodes(tree, name) for name in (
                        "ll_channel_parent", "lv_device_list", "cl_local_config",
                        "sw_landscape", "iv_mode_1"))
                    if not drawer_open and not self._nodes(tree, "video_menu_layout_land"):
                        return
                    if not drawer_open:
                        self._tap(video)
            if attempt < 3:
                self._wait()
        raise RuntimeError("Configured camera fullscreen view was not verified")

    def _rollback_display(self):
        # Best effort and bounded; never mask the original failure. Do not keep
        # navigating when the user has requested the worker to stop.
        previous_deadline = self._deadline
        self._deadline = time.monotonic() + 25
        try:
            if self._landscape_changed and not self.stop_event.is_set():
                try:
                    tree = self._ui()
                    toggle = self._node(tree, "sw_landscape", False)
                    if toggle is None:
                        tree = self._portrait_main(tree)
                        if not self._device_matches(tree):
                            raise RuntimeError("Camera device cannot be verified for display cleanup")
                        tree, toggle = self._landscape_switch()
                    if toggle.get("checked") == "true":
                        self._tap(toggle)
                    self._tap(self._node(tree, "iv_common_title_bar_back"))
                except Exception:
                    pass
            for name, value in self._rotation_before.items():
                try:
                    # AdbClient honors its own stop flag; do not bypass it.
                    self.client.run("shell", "settings", "put", "system", name, value, timeout=5)
                except Exception:
                    pass
        finally:
            self._deadline = previous_deadline

    def restore(self):
        self._deadline = time.monotonic() + 120
        self._rotation_before = {}
        self._landscape_changed = False
        try:
            self._check()
            self._stage = "launcher verification"
            resolved = self._text(self._run("shell", "cmd", "package", "resolve-activity",
                                          "--brief", PACKAGE)).splitlines()
            component = resolved[-1] if resolved else ""
            if not re.fullmatch(re.escape(PACKAGE) + r"/[A-Za-z0-9_.]+", component):
                raise RuntimeError("SuperLive launcher could not be verified")
            self._remember_rotation()
            self._run("shell", "am", "force-stop", PACKAGE)
            self._run("shell", "am", "start", "-n", component)
            self._stage = "app startup"
            tree = self._wait_for(lambda t: bool(self._nodes(t, "live_video")), 16)
            if self._node(tree, "iv_device", False) is not None:
                self._select_landscape_camera(tree)
            else:
                self._stage = "portrait camera controls"
                tree = self._portrait_main(tree)
                self._stage = "device selection"
                tree = self._select_device(tree)
                self._stage = "channel verification"
                self._select_channel(tree)
                self._stage = "fullscreen restoration"
                self._enter_fullscreen()
            self._check()
            return True
        except Exception:
            self._rollback_display()
            raise
        finally:
            self._deadline = None
