import unittest
import numpy as np
from test_superlive_recovery import CameraClient, SuperLiveRecovery
from stream_health import FrameStallDetector


class RestartOnlyTests(unittest.TestCase):
    def test_restart_does_not_select_channel_or_change_rotation(self):
        client = CameraClient()
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
