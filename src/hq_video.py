"""Full-capture-resolution H.264 recording with explicit quality control."""
import os
import subprocess


class HighQualityWriter:
    def __init__(self, path, fps, size):
        import imageio_ffmpeg
        self.closed = False
        self.size = size
        self.process = subprocess.Popen([
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % size,
            "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-threads", "2", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def isOpened(self):
        return not self.closed and self.process.poll() is None

    def write(self, frame):
        if frame.shape[:2] != (self.size[1], self.size[0]):
            raise ValueError("Recording frame dimensions changed")
        self.process.stdin.write(frame.tobytes())

    def release(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.close()
            if self.process.wait(timeout=15):
                raise RuntimeError("High quality encoder failed")
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=3)
