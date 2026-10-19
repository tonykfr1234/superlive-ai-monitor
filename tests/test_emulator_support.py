import sys,unittest
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
import numpy as np
import cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hq_video import fit_frame
from capture_region import find_emulator_window,memu_render_rect
class EmulatorTests(unittest.TestCase):
 def test_both_emulators_and_ignore_manager(self):
  gui,processes,ps=Mock(),Mock(),Mock()
  gui.EnumWindows.side_effect=lambda cb,_:[cb(h,None) for h in (1,2,3)]
  gui.IsWindowVisible.return_value=True
  gui.IsIconic.return_value=False
  gui.GetWindowRect.return_value=(0,0,800,600)
  processes.GetWindowThreadProcessId.side_effect=lambda h:(0,h)
  ps.Process.side_effect=lambda pid:SimpleNamespace(name=lambda:{1:'MuMuNxDevice.exe',2:'MEmu.exe',3:'MEmuHeadless.exe'}[pid])
  self.assertEqual(find_emulator_window(gui,processes,ps),2)
 def test_memu_render_excludes_toolbar(self):
  gui=Mock();gui.EnumChildWindows.side_effect=lambda h,cb,_:cb(2,None)
  gui.IsWindowVisible.return_value=True;gui.GetWindowText.return_value='RenderWindowWindow'
  gui.ClientToScreen.return_value=(100,132);gui.GetClientRect.return_value=(0,0,800,450)
  self.assertEqual(memu_render_rect(1,gui),(100,132,900,582))
 def test_recording_resizes_with_black_bars(self):
  for source in ((400,200),(200,400),(300,300)):
   frame=np.full((source[1],source[0],3),255,dtype=np.uint8)
   fitted=fit_frame(frame,(640,480),cv2)
   ys,xs=np.where(fitted[:,:,0]>0)
   self.assertEqual(fitted.shape,(480,640,3))
   self.assertAlmostEqual((xs.max()-xs.min()+1)/(ys.max()-ys.min()+1),source[0]/source[1],delta=.01)
if __name__=='__main__':unittest.main()
