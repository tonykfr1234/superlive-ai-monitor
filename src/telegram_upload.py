"""Bounded multipart request bodies for the original high-quality video.

``requests`` eagerly reads files passed through ``files=`` into one large
body. An iterable with a known length lets its transport write small blocks
while retaining Content-Length and ordinary verified HTTPS. This module
performs no network requests or retries and never closes the caller's file.
"""
import math
import os
import time
import uuid


CHUNK_SIZE = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 600.0


def _quoted_parameter(value):
    # Match browser multipart escaping; literal line breaks cannot add headers.
    return str(value).replace("\r", "%0D").replace("\n", "%0A").replace('"', "%22")


class MultipartUploadBody:
    """Single-use multipart body compatible with ``requests.post(data=...)``.

    Pass ``headers={"Content-Type": body.content_type}``; requests obtains the
    exact Content-Length from ``len(body)``. ``stream`` must be a seekable binary
    file and is uploaded from its current position without being read eagerly.
    Cancellation and the body deadline are checked between blocks. Transport
    socket timeouts still bound a blocked write or the response wait.
    """

    def __init__(self, fields, stream, filename, *, field_name="video",
                 media_type="video/mp4", stop_event=None,
                 timeout_seconds=DEFAULT_TIMEOUT_SECONDS, boundary=None):
        self.stream = stream
        self.stop_event = stop_event
        self.timeout_seconds = float(timeout_seconds)
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Upload timeout must be positive and finite")
        boundary = uuid.uuid4().hex if boundary is None else str(boundary)
        if (not 1 <= len(boundary) <= 70
                or not all(character.isascii() and (character.isalnum() or character in "-_")
                           for character in boundary)):
            raise ValueError("Invalid multipart boundary")
        if not media_type or any(character in str(media_type) for character in "\r\n"):
            raise ValueError("Invalid multipart media type")
        self.content_type = "multipart/form-data; boundary=" + boundary
        self._start = stream.tell()
        # Seek, rather than read, to measure the remaining file. Restore the
        # caller's position even when measuring fails.
        try:
            stream.seek(0, os.SEEK_END)
            self._file_length = stream.tell() - self._start
        finally:
            stream.seek(self._start)
        if self._file_length < 0:
            raise ValueError("Upload file position is past its end")
        filename = str(filename).replace("\\", "/").rsplit("/", 1)[-1] or "video.mp4"
        parts = []
        for name, value in fields.items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, _quoted_parameter(name), value)).encode("utf-8"))
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                      "Content-Type: %s\r\n\r\n"
                      % (boundary, _quoted_parameter(field_name), _quoted_parameter(filename), media_type))
                     .encode("utf-8"))
        self._prefix = b"".join(parts)
        self._suffix = ("\r\n--%s--\r\n" % boundary).encode("ascii")
        self._length = len(self._prefix) + self._file_length + len(self._suffix)
        self._used = False

    def __len__(self):
        return self._length

    def _check(self, started):
        if self.stop_event is not None and self.stop_event.is_set():
            raise InterruptedError("Video upload cancelled")
        if time.monotonic() - started >= self.timeout_seconds:
            raise TimeoutError("Video upload body deadline exceeded")

    def __iter__(self):
        if self._used:
            raise ValueError("Upload body cannot be sent twice")
        self._used = True
        started = time.monotonic()
        self._check(started)
        if self.stream.tell() != self._start:
            raise ValueError("Upload file position changed before sending")
        for offset in range(0, len(self._prefix), CHUNK_SIZE):
            self._check(started)
            yield self._prefix[offset:offset + CHUNK_SIZE]
        remaining = self._file_length
        while remaining:
            self._check(started)
            size = min(CHUNK_SIZE, remaining)
            block = self.stream.read(size)
            if not isinstance(block, bytes) or not 0 < len(block) <= size:
                raise OSError("Upload file changed or could not be read")
            remaining -= len(block)
            self._check(started)
            yield block
        self._check(started)
        yield self._suffix
