"""Reveal private videos without replay, shell execution, or broad searches."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from record_protocol import atomic_json
from video_location import VideoLocationError, resolve_video_location, reveal_video


class VideoLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.records = self.root/"recordings"

    def video(self, relative):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        return path.resolve()

    def manifest(self, path, name="a"):
        info = dict(path=str(path), size=7, mtime_ns=123)
        atomic_json(self.root/"runtime/plate_replay/manifest"/(name+".json"),
                    dict(identity=info, state="complete"))
        return hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()

    def resolve(self, event, **kwargs):
        return resolve_video_location(self.root, event, self.records, **kwargs)

    def test_explicit_full_path_is_authoritative_and_reveal_never_runs_video(self):
        path = self.video("elsewhere/中文 name & (clip).mp4")
        self.video("recordings/2026-10-21/"+path.name)
        event = dict(source_video=path.name, source_video_path=str(path))
        runner = Mock()
        self.assertEqual(reveal_video(self.root, event, self.records, runner=runner), path)
        runner.assert_called_once_with(["explorer.exe", "/select,", str(path)], shell=False)

    def test_missing_explicit_original_does_not_open_another_namesake(self):
        path = self.video("recordings/2026-10-21/clip.mp4")
        with self.assertRaisesRegex(VideoLocationError, "找不到原始影片"):
            self.resolve(dict(source_video=path.name, source_video_path=str(self.root/"missing"/path.name)))

    def test_basename_legacy_row_resolves_through_manifest(self):
        path = self.video("desktop/example.mp4")
        self.manifest(path)
        self.assertEqual(self.resolve(dict(source_video=path.name)), path)

    def test_legacy_summary_and_confirmed_evidence_disambiguate_manifest(self):
        first = self.video("one/clip.mp4")
        second = self.video("two/clip.mp4")
        digest = self.manifest(first, "a")
        self.manifest(second, "b")
        self.assertEqual(self.resolve(dict(id="replay-summary:"+digest, source_video="clip.mp4")), first)
        self.assertEqual(self.resolve(dict(source_video="clip.mp4",
            evidence=[dict(sample_id="replay:"+digest+":42")])), first)
        with self.assertRaisesRegex(VideoLocationError, "多個同名影片"):
            self.resolve(dict(source_video="clip.mp4"))

    def test_manifest_and_known_root_duplicates_are_ambiguous(self):
        self.manifest(self.video("desktop/clip.mp4"))
        self.video("recordings/2026-10-21/clip.mp4")
        with self.assertRaisesRegex(VideoLocationError, "多個同名影片"):
            self.resolve(dict(source_video="clip.mp4"))

    def test_record_config_and_only_direct_date_folders_are_searched(self):
        path = self.video("recordings/2026-10-21/clip.mp4")
        atomic_json(self.root/"config/recording_config.json", dict(record_root=str(self.records)))
        self.assertEqual(resolve_video_location(self.root, dict(source_video="clip.mp4")), path)
        self.video("recordings/unrelated/nested.mp4")
        self.video("recordings/2026-10-21/deeper/nested.mp4")
        with self.assertRaisesRegex(VideoLocationError, "找不到原始影片"):
            self.resolve(dict(source_video="nested.mp4"))

    def test_explicit_known_root_is_shallow_and_multiple_matches_fail(self):
        path = self.video("examples/clip.mp4")
        self.assertEqual(self.resolve(dict(source_video="clip.mp4"), known_roots=[path.parent]), path)
        other = self.video("other/clip.mp4")
        with self.assertRaisesRegex(VideoLocationError, "多個同名影片"):
            self.resolve(dict(source_video="clip.mp4"), known_roots=[path.parent, other.parent])

    def test_invalid_paths_and_unreadable_manifest_do_not_launch_explorer(self):
        runner = Mock()
        for event in ({}, dict(source_video="program.exe"),
                      dict(source_video="clip.mp4", source_video_path="relative/clip.mp4")):
            with self.assertRaises(VideoLocationError):
                reveal_video(self.root, event, self.records, runner=runner)
        path = self.video("recordings/2026-10-21/clip.mp4")
        bad = self.root/"runtime/plate_replay/manifest/bad.json"
        bad.parent.mkdir(parents=True)
        bad.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(VideoLocationError, "索引暫時無法讀取"):
            reveal_video(self.root, dict(source_video=path.name), self.records, runner=runner)
        runner.assert_not_called()

    def test_launch_error_is_readable_and_safe(self):
        path = self.video("clip.mp4")
        with self.assertRaisesRegex(VideoLocationError, "無法開啟檔案總管"):
            reveal_video(self.root, dict(source_video_path=str(path)), self.records,
                         runner=Mock(side_effect=OSError("fixture")))


if __name__ == "__main__":
    unittest.main()
