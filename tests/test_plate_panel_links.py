"""Synthetic history interaction only; no Explorer, camera, or live processes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from preview_ui import (PreviewController, PreviewLayout, plate_event_groups,
                        render_plate_panel, ui_font)


def record(number=0, **changes):
    value = dict(id="synthetic-%d" % number, timestamp=1000+number, confirmed=True,
                 text="AB1235", matched="AB1234", distance=1,
                 source="recording_replay", source_video="sample-%d.mp4" % number)
    value.update(changes)
    return value


class PlatePanelInteractionTests(unittest.TestCase):
    def controller(self, events, targets=("AB1234",), size=(1100, 900)):
        controller = PreviewController()
        controller.plate_enabled = True
        controller.plate_panel_open = True
        controller.layout = PreviewLayout.create(1600, 900, *size, show_plates=True, plates_open=True)
        controller.plate_filter_targets = list(targets)
        controller.plate_events = events
        return controller

    def render(self, controller):
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (controller.layout.width, controller.layout.height))
        base = ImageDraw.Draw(image)
        output, strokes = [], []
        class Draw:
            def __getattr__(self, name):
                return getattr(base, name)
            def text(self, position, text, **options):
                output.append((position, text, options))
                return base.text(position, text, **options)
            def line(self, position, **options):
                strokes.append((position, options))
                return base.line(position, **options)
        render_plate_panel(Draw(), controller, ui_font(controller.layout.font_size)[0], True)
        return "".join(text for _, text, _ in output), output, strokes

    def click(self, controller, rect):
        x, y = rect.x+1, rect.y+1
        controller.mouse("down", x, y)
        return controller.mouse("up", x, y)

    def test_only_confirmed_existing_target_matches_with_up_to_two_edits_expand(self):
        values = [record(0, matched="ab-1234", distance=0),
                  record(1, text="XY1234", raw_text="XY-1234", distance=2),
                  record(2, text="AB1234", matched="ZZ9999", distance=1),
                  record(3, matched=None, distance=None),
                  record(4, confirmed=False, outcome="insufficient"),
                  record(5, confirmed=False, outcome="no_plate"),
                  record(6, confirmed=False, outcome="error")]
        values += [record(10+i, distance=distance) for i, distance in
                   enumerate((3, -1, float("nan"), True, "2", 1.5))]
        featured, other = plate_event_groups(values, [" ab 1234 "])
        self.assertEqual([item["id"] for item in featured], ["synthetic-1", "synthetic-0"])
        self.assertEqual(len(other), len(values)-2)
        self.assertEqual(featured[0]["text"], "XY1234")
        all_confirmed, negatives = plate_event_groups(values, [])
        self.assertEqual(len(all_confirmed), len(values)-3)
        self.assertEqual(len(negatives), 3)

    def test_other_results_start_collapsed_and_toggle_without_polygon_changes(self):
        samples = [record(0), record(1, text="ZZ9999", matched="ZZ9999"),
                   record(2, confirmed=False, outcome="no_plate", source_video="no-plate.mp4"),
                   record(3, confirmed=False, outcome="error", source_video="failed.mp4")]
        controller = self.controller(samples)
        controller.command("select")
        controller.draft = [[.1, .1], [.2, .2]]
        text, _, _ = self.render(controller)
        self.assertFalse(controller.plate_other_expanded)
        self.assertIn("其他辨識紀錄（3 筆）", text)
        self.assertNotIn("ZZ9999", text)
        self.assertNotIn("no-plate.mp4", text)
        self.assertEqual(len(controller.plate_links), 1)
        self.assertIsNone(self.click(controller, controller.plate_group_hits[0]))
        self.assertTrue(controller.plate_other_expanded)
        text, _, _ = self.render(controller)
        while controller.plate_scroll < controller.plate_scroll_max:
            controller.key("page_down")
            text += self.render(controller)[0]
        self.assertIn("ZZ9999", text)
        self.assertIn("no-plate.mp4", text)
        controller.key("home")
        self.render(controller)
        group = controller.plate_group_hits[0]
        controller.mouse("right_down", group.x+1, group.y+1)
        self.assertEqual(controller.draft, [[.1, .1], [.2, .2]])
        self.click(controller, group)
        self.assertFalse(controller.plate_other_expanded)
        text, _, _ = self.render(controller)
        self.assertNotIn("no-plate.mp4", text)
        self.assertEqual(controller.draft, [[.1, .1], [.2, .2]])

    def test_every_wrapped_filename_line_returns_event_copy_and_is_blue_underlined(self):
        filename = "synthetic-long-filename-"*14+".mp4"
        source = record(source_video="private/folder/"+filename, evidence=[{"sample": 1}])
        controller = self.controller([source])
        controller.command("select")
        text, output, strokes = self.render(controller)
        self.assertNotIn("private/folder", text)
        self.assertIn(filename, text)
        links = controller.plate_links
        self.assertGreater(len(links), 2)
        link_color = (105, 184, 255)
        self.assertEqual(sum(options["fill"] == link_color for _, _, options in output), len(links))
        self.assertEqual(sum(options.get("fill") == link_color for _, options in strokes), len(links))
        for rect, stored in links:
            self.assertIs(stored, source)
            action = self.click(controller, rect)
            self.assertEqual(action.kind, "reveal_video")
            self.assertEqual(action.event, source)
            self.assertIsNot(action.event, source)
            self.assertIsNone(action.roi)
            action.event["evidence"][0]["sample"] = 99
            self.assertEqual(source["evidence"][0]["sample"], 1)
        self.assertEqual(controller.draft, [])

    def test_scroll_resize_and_history_updates_do_not_reuse_stale_click_targets(self):
        controller = self.controller([record(i) for i in range(40)], size=(800, 480))
        self.render(controller)
        old_rect, first = controller.plate_links[0]
        controller.key("end")
        self.assertIsNone(self.click(controller, old_rect))
        self.render(controller)
        self.assertNotIn(first["id"], {event["id"] for _, event in controller.plate_links})
        for rect, value in controller.plate_links:
            self.assertEqual(self.click(controller, rect).event["id"], value["id"])
        controller.layout = PreviewLayout.create(1600, 900, 640, 600, show_plates=True, plates_open=True)
        controller.key("home")
        self.render(controller)
        for rect, value in controller.plate_links:
            self.assertTrue(controller.layout.plates.contains(rect.x, rect.y))
            self.assertLessEqual(rect.y+rect.height, controller.layout.footer.y)
            self.assertEqual(self.click(controller, rect).event["id"], value["id"])
        old_rect = controller.plate_links[0][0]
        controller.plate_events = [record(99)]
        self.assertIsNone(self.click(controller, old_rect))
        self.render(controller)
        self.assertEqual(self.click(controller, controller.plate_links[0][0]).event["id"], "synthetic-99")

    def test_release_elsewhere_or_change_filter_cancels_link_activation(self):
        controller = self.controller([record()])
        self.render(controller)
        rect = controller.plate_links[0][0]
        controller.mouse("down", rect.x+1, rect.y+1)
        self.assertIsNone(controller.mouse("up", 1, 1))
        controller.mouse("down", rect.x+1, rect.y+1)
        controller.plate_filter_targets = ["ZZ9999"]
        self.assertIsNone(controller.mouse("up", rect.x+1, rect.y+1))
        text, _, _ = self.render(controller)
        self.assertIn("其他辨識紀錄（1 筆）", text)
        self.assertEqual(controller.plate_links, [])


if __name__ == "__main__":
    unittest.main()
