"""Private human-reviewed plate spellings; raw OCR evidence is never overwritten."""
from pathlib import Path
import hashlib
import math
import re
from record_protocol import read_json


def normalized(text):
    return re.sub('[^A-Z0-9]', '', str(text or '').upper())


def display_plate(text):
    value = normalized(text)
    for pattern in (r'([A-Z]{2,3})([0-9]{4})', r'([0-9]{4})([A-Z]{2,3})'):
        match = re.fullmatch(pattern, value)
        if match:
            return '-'.join(match.groups())
    return value


def load_corrections(root):
    data = read_json(Path(root)/'config/plate_corrections.json', {})
    return data if isinstance(data, dict) else {}


def prefer_verified(variants, selected, config):
    """Rank a verified spelling only when OCR actually returned it confidently."""
    if not selected:
        return selected, False
    verified = {normalized(p) for p in config.get('verified', []) if isinstance(p, str)}
    candidates = [v for v in variants if normalized(v.get('raw_text', '')) in verified
                  and .85 <= v.get('confidence', 0) <= 1
                  and .65 <= v.get('min_character_confidence', v.get('confidence', 0)) <= 1
                  and v.get('confidence', 0) >= selected.get('confidence', 0)-.04]
    keys = {normalized(v['raw_text']) for v in candidates}
    if len(keys) != 1:
        return selected, False
    winner = max(candidates, key=lambda v: v['confidence'])
    return winner, normalized(winner['raw_text']) != normalized(selected['raw_text'])


def correct_event(event, config):
    raw = normalized(event.get('text', ''))
    aliases = config.get('aliases', {})
    corrected = aliases.get(raw) if isinstance(aliases, dict) else None
    if corrected and re.fullmatch('[A-Z0-9]{4,10}', normalized(corrected)):
        event.update(observed_text=raw, text=normalized(corrected), user_corrected=True,
                     correction_source='local_manual_alias')
    event['display_text'] = display_plate(event.get('text', ''))
    return event


def save_plate_crop(root, event, frame, sample_id):
    """Save pixels from the exact evidence frame, never a later vehicle image."""
    if frame is None:
        return
    raw = event.get('observed_text', event.get('text'))
    sample = next((s for s in event.get('evidence', [])
                   if s.get('sample_id') == str(sample_id) and s.get('text') == raw), None)
    if sample is None:
        return
    import cv2
    try:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = map(float, sample['bbox'])
        if not all(math.isfinite(x) for x in (x1, y1, x2, y2)) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return
        pad = max(2, int((y2-y1)*.12))
        crop = frame[max(0, int(y1)-pad):min(height, math.ceil(y2)+pad),
                     max(0, int(x1)-pad):min(width, math.ceil(x2)+pad)]
        if crop.shape[1] > 720:
            crop = cv2.resize(crop, (720, max(1, round(720*crop.shape[0]/crop.shape[1]))))
        ok, data = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 96])
        if not ok:
            return
        name = hashlib.sha256(str(event['id']).encode()).hexdigest()+'.jpg'
        path = Path(root)/'runtime/plate_crops'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(data.tobytes())
        temporary.replace(path)
        event['plate_image'] = dict(file=name, sha256=hashlib.sha256(data).hexdigest(),
                                   sample_id=str(sample_id), timestamp=sample['timestamp'])
    except (OSError, KeyError, ValueError, TypeError, cv2.error):
        pass  # Image failure must not discard a valid text detection.
