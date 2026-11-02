import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BackgroundWatchdogTests(unittest.TestCase):
    def test_both_watchdogs_hide_console_children(self):
        for name in ("watchdog.py", "recorder_watchdog.py"):
            with self.subTest(name=name):
                source = (ROOT / "src" / name).read_text(encoding="utf-8")
                tree = ast.parse(source)
                flags = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "CHILD_FLAGS"
                        for target in node.targets
                    )
                ]
                self.assertEqual(len(flags), 1)
                self.assertIn("CREATE_NO_WINDOW", ast.get_source_segment(source, flags[0]))
                self.assertIn("CREATE_NEW_PROCESS_GROUP", ast.get_source_segment(source, flags[0]))
                self.assertIn("creationflags=CHILD_FLAGS", source)


if __name__ == "__main__":
    unittest.main()
