"""No emulator or desktop needed: shared memory and Android UI are simulated."""
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import adb_capture


class FakeMemory:
    def __init__(self, capacity=1024):
        self.buf = bytearray(adb_capture.OFFSET+capacity)
        self.close = Mock()
        self.unlink = Mock()


def screen(*nodes, package="com.tvt.superliveplus"):
    root = ET.Element("hierarchy")
    for values in nodes:
        ET.SubElement(root, "node", dict(package=package, **values))
    return root


BUTTON = {"resource-id": "com.tvt.superliveplus:id/iv_bottom_definition_land", "bounds": "[0,0][20,20]"}
HIGHEST = {"text": "最高", "bounds": "[0,0][10,10]"}
LOW = {"text": "低", "bounds": "[0,10][10,20]"}


class SharedFrameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.memory = FakeMemory()
        patcher = patch.object(adb_capture.shared_memory, "SharedMemory", return_value=self.memory)
        self.open_memory = patcher.start()
        self.addCleanup(patcher.stop)
        clock = patch.object(adb_capture.time, "time", return_value=1000)
        clock.start()
        self.addCleanup(clock.stop)
        self.writer = adb_capture.AdbStream(temporary.name)
        self.reader = adb_capture.SharedAdbReader(temporary.name)

    def head(self, sequence=2, stamp=1000, width=4, height=3, size=36):
        self.memory.buf[:adb_capture.HEADER.size] = adb_capture.HEADER.pack(sequence, stamp, width, height, size)

    def test_reader_copies_native_dimensions_and_handles_resize(self):
        for sequence, shape in ((0, (3, 4, 3)), (2, (7, 2, 3))):
            original = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
            self.writer.publish(original, sequence)
            captured = self.reader.capture()
            self.assertTrue(np.array_equal(captured, original))
            self.assertEqual(captured.shape, shape)
            # Consumers own the array; overlays cannot alter the shared evidence.
            captured[:] = 0
            self.assertTrue(np.array_equal(self.reader.capture(), original))
        self.reader.close()
        self.assertIsNone(self.reader.memory)
        self.memory.close.assert_called_once()

    def test_stale_missing_future_and_nonfinite_timestamp_rejected(self):
        for stamp in (0, 996.9, 1001.1, float("nan"), float("inf")):
            with self.subTest(stamp=stamp):
                self.head(stamp=stamp)
                with self.assertRaises(RuntimeError):
                    self.reader.capture()

    def test_odd_sequence_and_invalid_dimensions_never_return_frame(self):
        for values in (dict(sequence=3), dict(size=35), dict(width=0, size=0),
                       dict(width=3841, height=2160, size=3841*2160*3)):
            with self.subTest(values=values):
                self.head(**values)
                with self.assertRaises(RuntimeError):
                    self.reader.capture()

    def test_write_during_copy_retries_instead_of_returning_torn_pixels(self):
        class ChangingBuffer(bytearray):
            def __getitem__(buffer, key):
                result = super().__getitem__(key)
                if isinstance(key, slice) and key.start == adb_capture.OFFSET:
                    raw = super().__getitem__(slice(None, adb_capture.HEADER.size))
                    seq, stamp, width, height, size = adb_capture.HEADER.unpack(raw)
                    buffer[:adb_capture.HEADER.size] = adb_capture.HEADER.pack(seq+2, stamp, width, height, size)
                return result
        self.memory.buf = ChangingBuffer(self.memory.buf)
        self.head()
        with self.assertRaisesRegex(RuntimeError, "changed while reading"):
            self.reader.capture()

    def test_oversized_source_rejected_before_writing_shared_memory(self):
        with patch.object(adb_capture, "CAPACITY", 10):
            before = bytes(self.memory.buf)
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                self.writer.publish(np.zeros((3, 4, 3), np.uint8), 0)
            self.assertEqual(bytes(self.memory.buf), before)


class QualitySelectionTests(unittest.TestCase):
    def setUp(self):
        self.client = adb_capture.AdbClient({"adb_path": "mock-adb"})
        self.client.tap = Mock()
        self.client.run = Mock()
        self.client.quality_screenshot = Mock()
        sleeper = patch.object(adb_capture.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def menu_state(self, *, selected=False, accepts=True, closes=True, checked=False):
        state = {"open": False, "selected": selected}
        def ui():
            if state["open"]:
                highest = dict(HIGHEST)
                if checked and state["selected"]:
                    highest["selected"] = "true"
                return screen(highest, LOW)
            return screen(BUTTON)
        def tap(node):
            if node.get("resource-id") == BUTTON["resource-id"]:
                state["open"] = True
            elif node.get("text") == "最高" and accepts:
                state["selected"] = True
        def command(*args):
            if args == ("shell", "input", "keyevent", "4") and closes:
                state["open"] = False
        self.client.ui = Mock(side_effect=ui)
        self.client.tap.side_effect = tap
        self.client.run.side_effect = command
        self.client.quality_screenshot.side_effect = lambda: np.full(
            (20, 20, 3), (220, 190, 30) if state["selected"] else (255, 255, 255), np.uint8)
        return state

    def test_selected_highest_text_or_checked_parent_counts_as_confirmation(self):
        self.assertTrue(adb_capture.quality_selected(screen(dict(HIGHEST, selected="true"))))
        root = screen()
        row = ET.SubElement(root, "node", checked="true")
        ET.SubElement(row, "node", HIGHEST)
        self.assertTrue(adb_capture.quality_selected(root))
        self.assertFalse(adb_capture.quality_selected(screen(HIGHEST, dict(LOW, selected="true"))))

    def test_cyan_selection_pixels_confirm_but_white_or_blue_only_do_not(self):
        for color, expected in (((220, 190, 30), True), ((255, 255, 255), False), ((220, 10, 10), False)):
            with self.subTest(color=color):
                frame = np.zeros((10, 10, 3), np.uint8)
                frame[2, :5] = color
                self.assertEqual(adb_capture.selected_cyan_text(frame, [0, 0, 10, 10]), expected)
        frame = np.full((10, 10, 3), (220, 190, 30), np.uint8)
        self.assertFalse(adb_capture.selected_cyan_text(frame, []))
        self.assertFalse(adb_capture.selected_cyan_text(frame, [20, 20, 30, 30]))

    def test_visible_white_highest_is_not_a_selection_confirmation_and_menu_is_closed(self):
        state = self.menu_state(accepts=False)
        self.assertFalse(self.client.highest_quality())
        self.assertFalse(state["open"])
        self.client.run.assert_called_once_with("shell", "input", "keyevent", "4")
        self.assertEqual(self.client.tap.call_count, 2)

    def test_cyan_highest_confirmed_only_after_menu_has_disappeared(self):
        state = self.menu_state()
        self.assertTrue(self.client.highest_quality())
        self.assertFalse(state["open"])
        self.client.run.assert_called_once_with("shell", "input", "keyevent", "4")

    def test_selected_flag_still_fails_if_quality_menu_does_not_close(self):
        state = self.menu_state(selected=True, closes=False, checked=True)
        self.assertFalse(self.client.highest_quality())
        self.assertTrue(state["open"])
        self.client.quality_screenshot.assert_not_called()
        self.assertEqual(self.client.run.call_count, 2)

    def test_missing_highest_option_still_closes_menu_in_finally(self):
        self.client.ui = Mock(side_effect=[screen(BUTTON), screen(LOW), screen(LOW), screen(BUTTON)])
        with self.assertRaisesRegex(RuntimeError, "Highest quality option"):
            self.client.highest_quality()
        self.client.run.assert_called_once_with("shell", "input", "keyevent", "4")

    def test_unrelated_app_cannot_receive_quality_taps(self):
        other = screen(BUTTON, HIGHEST, LOW, package="example.unrelated")
        self.client.ui = Mock(return_value=other)
        with self.assertRaisesRegex(RuntimeError, "SuperLive live view"):
            self.client.highest_quality()
        self.client.tap.assert_not_called()
        self.client.run.assert_not_called()

    def test_already_selected_highest_does_not_get_tapped_again(self):
        state = self.menu_state(selected=True)
        self.assertTrue(self.client.highest_quality())
        self.assertFalse(state["open"])
        self.assertEqual(self.client.tap.call_count, 1)
        self.assertEqual(self.client.tap.call_args.args[0].get("resource-id"), BUTTON["resource-id"])

    def test_selected_container_with_both_rows_is_not_confirmation(self):
        root = screen()
        container = ET.SubElement(root, "node", selected="true")
        ET.SubElement(container, "node", HIGHEST)
        ET.SubElement(container, "node", LOW)
        self.assertFalse(adb_capture.quality_selected(root))


if __name__ == "__main__":
    unittest.main()
