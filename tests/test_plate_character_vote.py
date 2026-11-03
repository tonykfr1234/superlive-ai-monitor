"""Character vote: noisy small-plate frames confirm only with consistent evidence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from plate_recognition import CharacterVote


def read(text, confidence, scores=None):
    return dict(raw_text=text, confidence=confidence,
                character_confidences=scores or [confidence] * len(text))


class CharacterVoteTests(unittest.TestCase):
    def feed(self, vote, frames, step=1.0, move=40):
        event = None
        for index, variants in enumerate(frames):
            box = [100 + index * move, 100, 170 + index * move, 125]
            event = vote.observe(variants, 100 + index * step, "s%d" % index, box, 0.85) or event
        return event

    def test_different_wrong_character_per_frame_confirms_voted_plate(self):
        # Shaped like a real 55-80 px plate: each frame misses a different character.
        frames = [[read("ABC1234", .82), read("ABC7234", .78)],
                  [read("ABG1234", .70), read("ABC1234", .66)],
                  [read("ABC1284", .80)],
                  [read("ABC1234", .90), read("ABC1734", .90)]]
        event = self.feed(CharacterVote(), frames)
        self.assertIsNotNone(event)
        self.assertEqual(event["text"], "ABC1234")
        self.assertEqual(event["consensus"], "character_vote")
        self.assertGreaterEqual(event["verbatim_frames"], 2)

    def test_tied_position_does_not_confirm(self):
        # Every frame is equally unsure between 1 and 6: no position winner.
        frames = [[read("ABC1234", .9), read("ABC6234", .9)] for _ in range(6)]
        self.assertIsNone(self.feed(CharacterVote(), frames))

    def test_never_read_verbatim_does_not_confirm(self):
        frames = [[read("ABC7234", .9)], [read("ABG1234", .9)], [read("ABC1284", .9)]]
        self.assertIsNone(self.feed(CharacterVote(), frames))

    def test_character_blurred_in_every_frame_does_not_confirm(self):
        weak = [.9, .9, .9, .3, .9, .9, .9]
        frames = [[read("ABC1234", .83, weak)] for _ in range(4)]
        self.assertIsNone(self.feed(CharacterVote(), frames))

    def test_near_certain_different_read_vetoes_repeated_misread(self):
        # 2026-11-03: one frame read the plate at .999, then three tilted frames
        # misread two characters identically and outvoted it.
        frames = [[read("ABC1234", .999)], [read("HBC1534", .95)], [read("HBC1534", .97)], [read("HBC1534", .96)]]
        self.assertIsNone(self.feed(CharacterVote(), frames))
        # Without that near-certain read the same misreads would confirm.
        self.assertEqual(self.feed(CharacterVote(), [[read("ABC1234", .9)]] + frames[1:])["text"], "HBC1534")

    def test_invalid_taiwan_layouts_are_ignored(self):
        frames = [[read("AB12C34", .95)] for _ in range(4)]
        self.assertIsNone(self.feed(CharacterVote(), frames))

    def test_far_jump_starts_a_separate_track(self):
        vote = CharacterVote()
        for index, x in enumerate((100, 900, 100, 900)):
            self.assertIsNone(vote.observe([read("ABC1234", .9)], 100 + index, "s%d" % index,
                                           [x, 100, x + 70, 125], 0.85))
        self.assertEqual(len(vote.tracks), 2)

    def test_duplicate_sample_and_reset(self):
        vote = CharacterVote()
        for _ in range(4):
            self.assertIsNone(vote.observe([read("ABC1234", .9)], 100, "same", [0, 0, 70, 25], .85))
        vote.reset()
        self.assertEqual(vote.tracks, [])


if __name__ == "__main__":
    unittest.main()
