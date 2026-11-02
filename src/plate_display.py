"""Only show independently confirmed, sharp plates from a moving vehicle in this clip."""
import numpy as np
import cv2


def clarity_score(photo):
    if not isinstance(photo, np.ndarray) or photo.ndim != 3:
        return None
    h, w = photo.shape[:2]
    if w < 80 or h < 20 or not 1.5 <= w / h <= 6.5:
        return None
    gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if contrast < 18 or sharpness < 80:
        return None
    return sharpness


def moving_at(observations, index, stamp, clip_start):
    rows = [(t, boxes[index]) for t, boxes, _, _ in observations
            if clip_start <= t <= stamp + .1 and index < len(boxes)]
    if len(rows) < 2 or rows[-1][0] - rows[0][0] < .2:
        return False
    first, last = rows[0][1], rows[-1][1]
    return (abs((last[0]+last[2]-first[0]-first[2])/2) >= .01
            or abs((last[1]+last[3]-first[1]-first[3])/2) >= .01
            or abs((last[2]-last[0])-(first[2]-first[0])) >= .02
            or abs((last[3]-last[1])-(first[3]-first[1])) >= .02)


class ClipPlateDisplay:
    def __init__(self):
        self.key = None
        self.image = None
        self.text = ''
        self.score = -1

    def update(self, tracker, events, recording):
        recording = recording or {}
        clip = recording.get('request_id')
        key = (clip, tracker.latest.get('group_id'))
        if key != self.key:
            self.key, self.image, self.text, self.score = key, None, '', -1
        if not clip or recording.get('status') != 'RECORDING':
            return
        for event in events:
            if (event.get('request_id') != clip or event.get('source') != 'live_recording'
                    or event.get('confirmed') is not True or not event.get('complete_plate')
                    or event.get('text') != tracker.latest.get('plate')):
                continue
            meta = event.get('plate_image') or {}
            if not meta.get('sha256') or meta.get('sha256') != tracker.latest.get('plate_image_sha256'):
                continue
            evidence = {row.get('sample_id'): row for row in event.get('evidence', [])
                        if row.get('confidence', 0) >= .85}
            if (len(evidence) < 2 or meta.get('sample_id') not in evidence
                    or len({row.get('timestamp') for row in evidence.values()}) < 2):
                continue
            index = tracker.latest.get('plate_vehicle_index')
            if not isinstance(index, int) or not moving_at(tracker._observations, index,
                    meta.get('timestamp', 0), recording.get('clip_started_at', 0)):
                continue
            score = clarity_score(tracker.plate_image)
            if score is not None and score > self.score:
                self.image = tracker.plate_image.copy()
                self.text = event.get('display_text') or event['text']
                self.score = score

    def display_record(self, record):
        result = dict(record)
        for name in ('plate', 'plate_display', 'candidate_plate'):
            result[name] = self.text if self.image is not None else ''
        return result
