import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import model3d


@unittest.skipUnless(model3d.can_sit(), "sitting frames not rendered")
class SittingTest(unittest.TestCase):
    def test_sequence_plays_forward_then_backward(self):
        self.assertEqual(model3d.phase_frame("pull", 0), 0)
        self.assertEqual(model3d.phase_frame("pull", 99), 27)
        self.assertEqual(model3d.phase_frame("push", 0), 27)
        self.assertEqual(model3d.phase_frame("push", 99), 0)
        self.assertEqual(model3d.phase_length("seated"), float("inf"))

    def test_reversing_midway_keeps_the_same_frame(self):
        for frame in range(28):
            t = model3d.phase_time_for_frame("push", frame)
            self.assertEqual(model3d.phase_frame("push", t + 1e-6), frame)

    def test_chair_offset_for_every_pull_frame(self):
        m = model3d._manifest()
        self.assertEqual(len(m["chair"]["pull_offsets"]), m["anims"]["pull"]["2"]["frames"])
        self.assertEqual(m["chair"]["pull_offsets"][-1], [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
