import sys
from pathlib import Path
import unittest
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from preview_ui import PreviewController, PreviewLayout, enhance_plate_display


class SettingsAccessTests(unittest.TestCase):
    def test_click_settings_and_review_in_respective_cards(self):
        c = PreviewController()
        c.layout = PreviewLayout.create(960, 540, 1280, 820, dashboard=True)
        for name, expected in [('settings', 'settings'), ('review_plate', 'review_plate')]:
            button = c.layout.buttons[name]
            c.mouse('down', button.x+5, button.y+5)
            self.assertEqual(c.mouse('up', button.x+5, button.y+5).kind, expected)

    def test_enlargement_preserves_source_and_aspect(self):
        source = Image.fromarray(np.arange(20*80*3, dtype=np.uint8).reshape(20, 80, 3))
        before = source.tobytes()
        enlarged = enhance_plate_display(source, (320, 210))
        self.assertEqual(enlarged.size, (320, 80))
        self.assertEqual(source.tobytes(), before)
