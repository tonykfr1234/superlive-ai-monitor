import os, sys, ctypes
from pathlib import Path
import cv2, mss, numpy as np, psutil, win32gui, win32process
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception: pass
wins=[]
def cb(hwnd,_):
    if not win32gui.IsWindowVisible(hwnd): return
    try:
        _,pid=win32process.GetWindowThreadProcessId(hwnd)
        if psutil.Process(pid).name().lower()!='mumunxdevice.exe': return
        l,t,r,b=win32gui.GetWindowRect(hwnd)
        if r-l>=200 and b-t>=300:wins.append((hwnd,(r-l)*(b-t)))
    except Exception: pass
win32gui.EnumWindows(cb,None)
if not wins:
    print('MUMU_CAPTURE=NOT_TESTED_NO_WINDOW'); raise SystemExit(3)
hwnd=max(wins,key=lambda x:x[1])[0]
cx,cy=win32gui.ClientToScreen(hwnd,(0,0));cr=win32gui.GetClientRect(hwnd);cw,ch=cr[2],cr[3]
try:sct=mss.MSS()
except AttributeError:sct=mss.mss()
try:shot=sct.grab({'left':int(cx),'top':int(cy),'width':int(cw),'height':int(ch)})
finally:
    try:sct.close()
    except:pass
frame=cv2.cvtColor(np.array(shot),cv2.COLOR_BGRA2BGR)
mean=float(np.mean(frame));std=float(np.std(frame))
out=Path.home()/"Downloads"/"SuperLive_AI"/"logs"/"VM_Verify"/"VM_MuMu_Capture_Probe.jpg"
out.parent.mkdir(parents=True,exist_ok=True);cv2.imwrite(str(out),frame)
print('MUMU_CAPTURE_SIZE=%dx%d'%(cw,ch));print('IMAGE_MEAN=%.2f'%mean);print('IMAGE_STD=%.2f'%std);print('CAPTURE_JPG=%s'%out)
if mean<2 or std<1:print('MUMU_CAPTURE=BLACK_OR_BLANK');raise SystemExit(4)
print('MUMU_CAPTURE=PASS')
