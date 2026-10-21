"""A successful adb exit must not cause reuse of an old UI hierarchy."""
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import adb_capture


class UiDumpFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.client = adb_capture.AdbClient({"adb_path": "mock-adb"})
        self.paths = []
        self.output = "UI hierchary dumped to: {path}\n"
        self.xml = b'<hierarchy><node package="com.tvt.superliveplus" /></hierarchy>'
        self.cleanup_error = None
        def run(*args):
            if args[:3] == ("shell", "uiautomator", "dump"):
                self.paths.append(args[3])
                return self.output.format(path=args[3]).encode()
            if args[:2] == ("shell", "cat"):
                return self.xml
            if args[:3] == ("shell", "rm", "-f"):
                if self.cleanup_error:
                    raise self.cleanup_error
                return b""
            raise AssertionError(args)
        self.client.run = Mock(side_effect=run)

    def test_each_dump_uses_a_new_path_for_read_and_cleanup(self):
        for _ in range(2):
            self.assertEqual(self.client.ui().tag, "hierarchy")
        self.assertEqual(len(set(self.paths)), 2)
        for path in self.paths:
            self.assertRegex(path, r"^/sdcard/superlive_ai_ui_[0-9a-f]{32}\.xml$")
            self.client.run.assert_any_call("shell", "cat", path)
            self.client.run.assert_any_call("shell", "rm", "-f", path)
        self.assertFalse(any("/sdcard/superlive_ai_ui.xml" in call.args
                             for call in self.client.run.call_args_list))

    def test_empty_or_failed_dump_output_never_reads_stale_xml(self):
        for output in ("", "ERROR: could not get idle state.",
                       "UI hierchary dumped to: /sdcard/other.xml\n"):
            with self.subTest(output=output):
                self.output = output
                self.client.run.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                    self.client.ui()
                self.assertFalse(any(call.args[:2] == ("shell", "cat")
                                     for call in self.client.run.call_args_list))
                self.client.run.assert_called_with("shell", "rm", "-f", self.paths[-1])

    def test_correct_spelling_of_hierarchy_is_also_accepted(self):
        self.output = "UI hierarchy dumped to: {path}\n"
        self.assertEqual(self.client.ui().tag, "hierarchy")

    def test_transient_empty_dump_recovers_with_new_file_before_reading(self):
        original = self.client.run.side_effect
        def transient(*args):
            if args[:3] == ("shell", "uiautomator", "dump") and not self.paths:
                self.paths.append(args[3])
                return b""
            return original(*args)
        self.client.run.side_effect = transient
        self.assertEqual(self.client.ui().tag, "hierarchy")
        self.assertEqual(len(set(self.paths)), 2)
        reads = [call.args[2] for call in self.client.run.call_args_list if call.args[:2] == ("shell", "cat")]
        self.assertEqual(reads, [self.paths[-1]])

    def test_parse_error_is_preserved_even_if_cleanup_fails(self):
        self.xml = b"invalid xml"
        self.cleanup_error = RuntimeError("disconnected during cleanup")
        with self.assertRaises(ET.ParseError):
            self.client.ui()
        self.client.run.assert_called_with("shell", "rm", "-f", self.paths[-1])

    def test_dump_error_is_preserved_even_if_cleanup_fails(self):
        self.client.run.side_effect = [TimeoutError("dump timed out"), RuntimeError("cleanup failed")]
        with self.assertRaisesRegex(TimeoutError, "dump timed out"):
            self.client.ui()
        dump, cleanup = self.client.run.call_args_list
        self.assertEqual(cleanup.args, ("shell", "rm", "-f", dump.args[3]))

    def test_read_error_still_cleans_its_unique_path(self):
        original = self.client.run.side_effect
        def fail_read(*args):
            if args[:2] == ("shell", "cat"):
                raise RuntimeError("dump file missing")
            return original(*args)
        self.client.run.side_effect = fail_read
        with self.assertRaisesRegex(RuntimeError, "dump file missing"):
            self.client.ui()
        self.client.run.assert_called_with("shell", "rm", "-f", self.paths[-1])


if __name__ == "__main__":
    unittest.main()
