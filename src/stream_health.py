"""Camera-content freshness, separate from person/vehicle movement detection.

A changing camera timestamp is sufficient evidence of a live, quiet scene.
The caller must provide the camera area (or its normalized ROI), excluding
Android clocks/toolbars, and independently confirm a suspected stall before
restarting an app. Pixel comparison alone cannot prove a stream is frozen.
"""
import math
import time

import numpy as np


class FrameStallDetector:
    """Track meaningful camera pixel changes using monotonic elapsed time.

    Keep one instance across normal screenrecord rollovers. Call ``reset`` only
    when the camera/ROI changes or app recovery deliberately starts over.
    ``observe`` returns True for a *suspected* freeze, never a recovery command.
    """

    def __init__(self, timeout_seconds=60.0, roi=None, sample_interval=1.0):
        self.timeout_seconds = float(timeout_seconds)
        self.sample_interval = float(sample_interval)
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds < 15:
            raise ValueError('Freeze timeout must be at least 15 seconds')
        if (not math.isfinite(self.sample_interval) or self.sample_interval <= 0
                or self.sample_interval > self.timeout_seconds / 2):
            raise ValueError('Invalid freeze sampling interval')
        if roi is None:
            self.roi = (0.0, 0.0, 1.0, 1.0)
        else:
            if len(roi) != 4:
                raise ValueError('Camera ROI must have four normalized coordinates')
            self.roi = tuple(float(value) for value in roi)
            left, top, right, bottom = self.roi
            if (not all(math.isfinite(value) for value in self.roi)
                    or not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1):
                raise ValueError('Camera ROI is outside the image')
        self.reset()

    def reset(self):
        self._reference = None
        self._source_shape = None
        self._last_sample = None
        self._last_change = None
        self._suspected = False

    @property
    def suspected(self):
        return self._suspected

    @staticmethod
    def _clock(now):
        now = time.monotonic() if now is None else float(now)
        if not math.isfinite(now):
            raise ValueError('Invalid monotonic timestamp')
        return now

    def stale_for(self, now=None):
        """Seconds since the last meaningful change, or zero before a sample."""
        now = self._clock(now)
        return 0.0 if self._last_change is None else max(0.0, now - self._last_change)

    @staticmethod
    def _validate_frame(frame):
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim not in (2, 3) or min(frame.shape[:2]) < 1
                or (frame.ndim == 3 and frame.shape[2] not in (3, 4))):
            raise ValueError('Expected a nonempty uint8 camera image')

    def _signature(self, frame):
        import cv2

        height, width = frame.shape[:2]
        left, top, right, bottom = self.roi
        x1, y1 = int(left * width), int(top * height)
        x2, y2 = int(math.ceil(right * width)), int(math.ceil(bottom * height))
        camera = frame[y1:y2, x1:x2]
        # Area averaging avoids skipping narrow timestamp glyphs. At most
        # 960x960 pixels are compared, once a second during normal streaming.
        scale = min(1.0, 960.0 / max(camera.shape[:2]))
        if scale < 1:
            camera = cv2.resize(camera, (max(1, round(camera.shape[1] * scale)),
                                        max(1, round(camera.shape[0] * scale))),
                                interpolation=cv2.INTER_AREA)
        if camera.ndim == 3:
            camera = cv2.cvtColor(camera, cv2.COLOR_BGRA2GRAY if camera.shape[2] == 4
                                 else cv2.COLOR_BGR2GRAY)
        return camera.copy()

    @staticmethod
    def _changed(reference, current):
        # A global percentage threshold misses the tiny seconds digit on an
        # otherwise static camera. Require a small local cluster instead;
        # +/- a few H.264 quantization levels or isolated hot pixels do not count.
        changed = np.abs(current.astype(np.int16) - reference.astype(np.int16)) >= 12
        minimum = min(8, changed.size)
        if np.count_nonzero(changed) < minimum:
            return False
        height, width = changed.shape
        padded = np.pad(changed, ((0, (-height) % 8), (0, (-width) % 8)))
        tiles = padded.reshape(padded.shape[0] // 8, 8, padded.shape[1] // 8, 8)
        return bool(np.any(tiles.sum(axis=(1, 3)) >= min(4, minimum)))

    def frames_changed(self, first, second):
        """Compare two independent captures without changing freshness state.

        Use the same capture method for both images so codec differences do
        not count as camera activity. A resolution/orientation change counts
        as new content and should be resolved before declaring a freeze.
        """
        self._validate_frame(first)
        self._validate_frame(second)
        if first.shape != second.shape:
            return True
        return self._changed(self._signature(first), self._signature(second))

    def observe(self, frame, now=None):
        """Return whether camera content has stayed unchanged for the timeout.

        Invalid frames raise without advancing freshness. Resolution changes
        and a regressing test clock reset observation conservatively. The frame
        that first reaches the timeout is always compared before declaring it
        suspect, even if the ordinary sampling interval has not elapsed.
        """
        now = self._clock(now)
        self._validate_frame(frame)
        shape_changed = frame.shape != self._source_shape
        clock_regressed = self._last_sample is not None and now < self._last_sample
        due = (self._last_sample is None or now - self._last_sample >= self.sample_interval
               or (not self._suspected and self.stale_for(now) >= self.timeout_seconds))
        if not (due or shape_changed or clock_regressed):
            return self._suspected
        signature = self._signature(frame)
        if (self._reference is None or shape_changed or clock_regressed
                or self._changed(self._reference, signature)):
            self._reference = signature
            self._last_change = now
        self._source_shape = frame.shape
        self._last_sample = now
        self._suspected = self.stale_for(now) >= self.timeout_seconds
        return self._suspected
