import io
from pathlib import Path
import sys
import threading
import unittest
from email.parser import BytesParser
from email.policy import default
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from telegram_upload import CHUNK_SIZE, MultipartUploadBody


class TrackedStream(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        if not 0 < size <= CHUNK_SIZE:
            raise AssertionError("Unbounded file read")
        return super().read(size)


class MultipartUploadTests(unittest.TestCase):
    def test_exact_length_and_original_binary_with_unicode_fields(self):
        video = b"\x00\xfforiginal-video\r\n" * 12000
        source = TrackedStream(video)
        body = MultipartUploadBody({"caption": "車輛 14:30／車牌 ABC123", "duration": 123},
                                   source, "sample.mp4", boundary="test-boundary")
        self.assertEqual(source.read_sizes, [])
        blocks = list(body)
        self.assertTrue(all(0 < len(block) <= CHUNK_SIZE for block in blocks))
        payload = b"".join(blocks)
        self.assertEqual(len(payload), len(body))
        message = BytesParser(policy=default).parsebytes(
            ("Content-Type: %s\r\nMIME-Version: 1.0\r\n\r\n" % body.content_type).encode() + payload)
        parts = list(message.iter_parts())
        self.assertEqual(parts[0].get_param("name", header="Content-Disposition"), "caption")
        self.assertEqual(parts[0].get_payload(decode=True).decode("utf-8"), "車輛 14:30／車牌 ABC123")
        self.assertEqual(parts[1].get_payload(decode=True), b"123")
        self.assertEqual(parts[2].get_filename(), "sample.mp4")
        self.assertEqual(parts[2].get_content_type(), "video/mp4")
        self.assertEqual(parts[2].get_payload(decode=True), video)
        self.assertFalse(source.closed)

    def test_prepared_request_keeps_length_without_eager_read_or_chunked_encoding(self):
        import requests
        source = TrackedStream(b"x" * (CHUNK_SIZE * 3 + 7))
        body = MultipartUploadBody({"caption": "測試"}, source, "sample.mp4")
        prepared = requests.Request("POST", "https://example.invalid/upload", data=body,
            headers={"Content-Type": body.content_type}).prepare()
        self.assertIs(prepared.body, body)
        self.assertEqual(int(prepared.headers["Content-Length"]), len(body))
        self.assertNotIn("Transfer-Encoding", prepared.headers)
        self.assertEqual(source.read_sizes, [])
        self.assertEqual(source.tell(), 0)
        list(prepared.body)
        self.assertEqual(source.read_sizes, [CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE, 7])

    def test_filename_drops_local_path_and_escapes_header_injection(self):
        source = TrackedStream(b"data")
        body = MultipartUploadBody({'cap"\r\nInjected: true': "正常"}, source,
            'C:\\private\\movie"\r\nInjected: true.mp4', boundary="safe-boundary")
        payload = b"".join(body)
        self.assertNotIn(b"private", payload)
        self.assertNotIn(b"\r\nInjected:", payload)
        self.assertIn(b'filename="movie%22%0D%0AInjected: true.mp4"', payload)
        self.assertIn(b'name="cap%22%0D%0AInjected: true"', payload)
        self.assertEqual(len(body), len(payload))

    def test_current_offset_and_empty_files_have_exact_length(self):
        for data, offset in [(b"prefix-payload", 7), (b"", 0)]:
            with self.subTest(offset=offset, size=len(data)):
                source = TrackedStream(data)
                source.seek(offset)
                body = MultipartUploadBody({}, source, "video.mp4", boundary="boundary")
                self.assertEqual(source.tell(), offset)
                payload = b"".join(body)
                self.assertEqual(len(payload), len(body))
                self.assertEqual(payload.split(b"\r\n\r\n", 1)[1], data[offset:] + b"\r\n--boundary--\r\n")

    def test_large_fields_are_also_emitted_in_bounded_blocks(self):
        body = MultipartUploadBody({"caption": "字" * CHUNK_SIZE}, TrackedStream(b"x"), "video.mp4")
        blocks = list(body)
        self.assertTrue(all(len(block) <= CHUNK_SIZE for block in blocks))
        self.assertEqual(sum(map(len, blocks)), len(body))

    def test_cancellation_stops_before_next_file_read(self):
        stopped = threading.Event()
        source = TrackedStream(b"x" * (CHUNK_SIZE * 3))
        body = MultipartUploadBody({}, source, "sample.mp4", stop_event=stopped)
        iterator = iter(body)
        next(iterator)  # Multipart headers.
        next(iterator)  # First video block.
        stopped.set()
        with self.assertRaises(InterruptedError):
            next(iterator)
        self.assertEqual(source.read_sizes, [CHUNK_SIZE])
        self.assertFalse(source.closed)

    def test_pre_cancelled_body_does_not_read_file(self):
        stopped = threading.Event()
        stopped.set()
        source = TrackedStream(b"video")
        body = MultipartUploadBody({}, source, "sample.mp4", stop_event=stopped)
        with self.assertRaises(InterruptedError):
            list(body)
        self.assertEqual(source.read_sizes, [])

    def test_body_deadline_stops_without_retrying_or_reading_more(self):
        source = TrackedStream(b"x" * (CHUNK_SIZE * 2))
        body = MultipartUploadBody({}, source, "sample.mp4", timeout_seconds=1)
        with patch("telegram_upload.time.monotonic", return_value=100) as clock:
            iterator = iter(body)
            next(iterator)
            clock.return_value = 101
            with self.assertRaises(TimeoutError):
                next(iterator)
        self.assertEqual(source.read_sizes, [])

    def test_truncated_file_cannot_silently_emit_a_short_body(self):
        source = TrackedStream(b"video")
        body = MultipartUploadBody({}, source, "sample.mp4")
        source.truncate(0)
        with self.assertRaises(OSError):
            list(body)

    def test_moved_file_and_reused_body_are_rejected(self):
        source = TrackedStream(b"video")
        body = MultipartUploadBody({}, source, "sample.mp4")
        source.seek(1)
        with self.assertRaises(ValueError):
            list(body)
        body = MultipartUploadBody({}, TrackedStream(b"video"), "sample.mp4")
        list(body)
        with self.assertRaises(ValueError):
            list(body)

    def test_invalid_deadlines_and_header_values_are_rejected(self):
        for timeout in [0, -1, float("inf"), float("nan")]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                MultipartUploadBody({}, io.BytesIO(b""), "sample.mp4", timeout_seconds=timeout)
        for options in [{"boundary": "bad\r\nheader"}, {"boundary": ""},
                        {"media_type": "video/mp4\r\nInjected: true"}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                MultipartUploadBody({}, io.BytesIO(b""), "sample.mp4", **options)


if __name__ == "__main__":
    unittest.main()
