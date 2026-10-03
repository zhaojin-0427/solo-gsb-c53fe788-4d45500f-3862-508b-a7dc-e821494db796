"""Static guards for the high-precision playhead chain in static/app.js.

The browser code has no JS test runner in this repo, so these tests pin
the exact regression that was fixed: a playhead time produced by play /
drag / jump must travel from internal state and the jump input to
``GET /api/projects/{id}/eval?t=`` **verbatim** — the old fixed
6-decimal rounding (``round6``) must not reappear in the input echo, the
dedup key, the throttle trailing re-send, or the request URL itself.
"""

import re
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"


def function_body(src: str, signature: str) -> str:
    """Return the body of a top-level JS function (closing brace at col 0)."""
    m = re.search(re.escape(signature) + r"\s*\{.*?\n\}", src, re.S)
    if not m:
        raise AssertionError(f"{signature} not found in app.js")
    return m.group(0)


class TestPlayheadPrecisionChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = APP_JS.read_text(encoding="utf-8")
        cls.flat = re.sub(r"\s+", " ", cls.src)

    def test_no_fixed_decimal_rounding_helper(self):
        # the microsecond-rounding helper is gone from the whole chain
        self.assertNotIn("round6", self.src)

    def test_jump_input_echo_is_verbatim(self):
        # the jump box echoes the exact playhead, so Go re-seeks to it
        self.assertIn('$("#jumpInput").value = String(state.time);', self.flat)

    def test_eval_query_uses_verbatim_state_time(self):
        # the request time is the unrounded playhead value
        self.assertIn("const tq = state.time;", self.flat)
        self.assertIn("/eval?t=${encodeURIComponent(String(tq))}", self.src)

    def test_dedup_compares_verbatim_time(self):
        # dedup must compare the same unrounded value that would be sent
        self.assertIn("state.lastEvalTime === tq", self.flat)

    def test_drag_end_resends_current_instant(self):
        body = function_body(self.src, "function startDrag(evt)")
        up = body.split("const up = () => {", 1)
        self.assertEqual(len(up), 2, "mouseup handler not found")
        self.assertIn("requestEval(true)", up[1])

    def test_pause_and_seek_resend_immediately(self):
        # every seek evaluates at once
        seek = function_body(self.src, "function seekTo(t)")
        self.assertIn("requestEval(true)", seek)
        # pause settles on the exact current instant
        play = function_body(self.src, "function setPlaying(on)")
        else_branch = play.split("} else {", 1)[1]
        self.assertIn("requestEval(true)", else_branch)

    def test_playback_throttle_still_present(self):
        # playback frames go through the rate limiter (immediate=false) and
        # a skipped frame schedules one trailing re-send of the live time
        tick = function_body(self.src, "function setPlaying(on)")
        self.assertIn("requestEval(false)", tick)
        self.assertIn("_evalTrailTimer", self.src)
        trail = re.search(
            r"_evalTrailTimer = setTimeout\(\(\) => \{(.*?)\}, 80\)",
            self.src, re.S)
        self.assertIsNotNone(trail, "throttle trailing timer not found")
        self.assertIn("requestEval(true)", trail.group(1))


if __name__ == "__main__":
    unittest.main()
