import unittest
from unittest.mock import patch
import numpy as np
from test_superlive_recovery import CameraClient, SuperLiveRecovery
from stream_health import FrameStallDetector


class RestartOnlyTests(unittest.TestCase):
    def test_restart_does_not_select_channel_or_change_rotation(self):
        client = CameraClient()
        with patch.object(SuperLiveRecovery, "_wait_for_camera", return_value=True):
            self.assertTrue(SuperLiveRecovery(client, None, None).restore())
        self.assertEqual(client.taps, [])
        self.assertTrue(any(c[:3] == ('shell','am','force-stop') for c in client.calls))
        self.assertFalse(any(c[:2] == ('shell','settings') for c in client.calls))

    def test_rotating_loader_is_not_live_camera_motion(self):
        detector = FrameStallDetector()
        frames=[]
        for second in range(61):
            frame=np.full((360,640,3),51,np.uint8)
            frame[170:180,300+second%30:310+second%30]=(0,140,220)
            frames.append(frame)
            suspect=detector.observe(frame,second)
        self.assertTrue(suspect)
        self.assertFalse(detector.frames_changed(frames[0],frames[30]))

    def test_loading_screen_verification_stops_at_sixty_seconds(self):
        import cv2
        clock = [0.0]
        frame = np.full((360, 640, 3), 51, np.uint8)
        frame[170:180, 300:310] = (0, 140, 220)
        png = cv2.imencode('.png', frame)[1].tobytes()
        client = CameraClient()
        original = client.run
        def run(*args, **kwargs):
            if args[:2] == ('exec-out', 'screencap'):
                return png
            return original(*args, **kwargs)
        client.run = run
        recovery = SuperLiveRecovery(client, None, None)
        def wait(seconds):
            clock[0] += seconds
            return False
        with patch('superlive_recovery.time.monotonic', side_effect=lambda: clock[0]), patch.object(recovery.stop_event, 'wait', side_effect=wait):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                recovery.restore()
        self.assertLessEqual(clock[0], 60.01)

    def test_real_changing_camera_returns_before_deadline(self):
        import cv2
        rng = np.random.default_rng(7)
        frames = [rng.integers(0, 255, (200, 400, 3), dtype=np.uint8) for _ in range(2)]
        client = CameraClient()
        original = client.run
        def run(*args, **kwargs):
            if args[:2] == ('exec-out', 'screencap'):
                return cv2.imencode('.png', frames.pop(0))[1].tobytes()
            return original(*args, **kwargs)
        client.run = run
        self.assertTrue(SuperLiveRecovery(client, None, None).restore())

    def test_splash_without_camera_node_is_never_accepted(self):
        clock = [0.0]
        client = CameraClient()
        client.startup_unknown = True
        recovery = SuperLiveRecovery(client, None, None)
        def wait(seconds):
            clock[0] += seconds
            return False
        with patch('superlive_recovery.time.monotonic', side_effect=lambda: clock[0]), patch.object(recovery.stop_event, 'wait', side_effect=wait):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                recovery.restore()
        self.assertLessEqual(clock[0], 60)
        self.assertIsNone(client.operation_deadline)

    def test_toolbar_changes_do_not_count_as_camera_motion(self):
        import cv2
        clock = [0.0]
        client = CameraClient()
        original = client.run
        def run(*args, **kwargs):
            if args[:2] == ('exec-out', 'screencap'):
                frame = np.full((400, 200, 3), 51, np.uint8)
                frame[160:] = int(clock[0] * 31) % 255
                return cv2.imencode('.png', frame)[1].tobytes()
            return original(*args, **kwargs)
        client.run = run
        recovery = SuperLiveRecovery(client, None, None)
        def wait(seconds):
            clock[0] += seconds
            return False
        with patch('superlive_recovery.time.monotonic', side_effect=lambda: clock[0]), patch.object(recovery.stop_event, 'wait', side_effect=wait):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                recovery.restore()
