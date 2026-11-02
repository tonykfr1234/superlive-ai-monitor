import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from plate_display import ClipPlateDisplay, clarity_score

class PlateDisplayTests(unittest.TestCase):
    def setUp(self):
        self.photo = np.full((40, 160, 3), 230, np.uint8)
        cv2.putText(self.photo, 'AB1234', (3, 30), cv2.FONT_HERSHEY_SIMPLEX, .8, (10,10,10), 2)
        self.tracker = SimpleNamespace(latest={'group_id':'group', 'plate':'AB1234', 'plate_image_sha256':'hash', 'plate_vehicle_index':0}, plate_image=self.photo,
            _observations=[(100,[(.1,.1,.5,.5)],1000,600),(101,[(.13,.1,.53,.5)],1000,600)])
        self.event = dict(request_id='clip', source='live_recording', confirmed=True, complete_plate=True, text='AB1234', plate_image={'sha256':'hash','timestamp':101,'sample_id':'b'}, evidence=[{'sample_id':'a','timestamp':100,'confidence':.95},{'sample_id':'b','timestamp':101,'confidence':.95}])
        self.state = dict(status='RECORDING',request_id='clip',clip_started_at=99)
        self.gate = ClipPlateDisplay()

    def test_clear_moving_confirmed_plate_is_displayed(self):
        self.gate.update(self.tracker,[self.event],self.state)
        self.assertIsNotNone(self.gate.image)
        self.assertEqual(self.gate.text,'AB1234')

    def test_static_plate_is_not_displayed(self):
        self.tracker._observations[-1] = (101,[(.1,.1,.5,.5)],1000,600)
        self.gate.update(self.tracker,[self.event],self.state)
        self.assertIsNone(self.gate.image)

    def test_blurry_or_tiny_crop_is_rejected_before_enhancement(self):
        self.assertIsNone(clarity_score(cv2.GaussianBlur(self.photo,(21,21),8)))
        self.assertIsNone(clarity_score(cv2.resize(self.photo,(40,10))))

    def test_new_clip_does_not_reuse_prior_plate(self):
        self.gate.update(self.tracker,[self.event],self.state)
        self.state['request_id']='next'
        self.gate.update(self.tracker,[self.event],self.state)
        self.assertIsNone(self.gate.image)
        self.assertEqual(self.gate.display_record({'plate':'OLD'})['plate'],'')

    def test_unconfirmed_or_preview_plate_is_not_displayed(self):
        for change in ({'confirmed':False},{'source':'live'},{'request_id':'old'}):
            self.gate.update(self.tracker,[dict(self.event,**change)],self.state)
            self.assertIsNone(self.gate.image)

    def test_motion_after_capture_does_not_qualify_old_static_crop(self):
        self.event['plate_image']['timestamp']=100
        self.gate.update(self.tracker,[self.event],self.state)
        self.assertIsNone(self.gate.image)

    def test_previous_clip_motion_does_not_qualify_current_clip(self):
        self.state['clip_started_at']=100.5
        self.gate.update(self.tracker,[self.event],self.state)
        self.assertIsNone(self.gate.image)
