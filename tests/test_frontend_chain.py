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


class TestEvalCacheInvalidation(unittest.TestCase):
    """Static guards for the stale-readout fix.

    The /eval cache is keyed by project + data revision + exact instant:
    a same-instant request after a project switch or an item save/delete
    must re-hit ``GET /api/projects/{id}/eval?t=``, an in-flight response
    from a superseded context must be dropped (never overwrite newer
    state), and a failed evaluation of the current project must clear the
    old readouts and surface the error.
    """

    @classmethod
    def setUpClass(cls):
        cls.src = APP_JS.read_text(encoding="utf-8")
        cls.flat = re.sub(r"\s+", " ", cls.src)

    def test_cache_context_combines_project_and_revision(self):
        body = function_body(self.src, "function evalCtx()")
        self.assertIn("state.projectId", body)
        self.assertIn("state.dataVersion", body)

    def test_dedup_requires_same_context_and_time(self):
        # time-only dedup was the bug: the context must match too
        body = function_body(self.src, "async function requestEval(immediate)")
        self.assertIn("state.lastEvalCtx === ctx", body)
        self.assertIn("state.lastEvalTime === tq", body)

    def test_open_project_bumps_revision_and_clears_readouts(self):
        body = function_body(self.src, "async function openProject(id)")
        self.assertIn("state.dataVersion += 1", body)
        # old channel readouts are cleared before the fresh evaluation,
        # so switching to an empty project shows no stale channels
        self.assertLess(body.index("state.readouts = null"),
                        body.index("requestEval(true)"))
        self.assertIn("renderReadouts()", body)

    def test_apply_payload_bumps_revision_and_reevaluates(self):
        body = function_body(self.src,
                             "async function applyProjectPayload(payload)")
        self.assertIn("state.dataVersion += 1", body)
        self.assertIn("requestEval(true)", body)

    def test_stale_inflight_response_is_discarded(self):
        body = function_body(self.src, "async function requestEval(immediate)")
        # the staleness guard must run before the response is stored
        self.assertLess(body.index("ctx !== evalCtx()"),
                        body.index("state.readouts = data"))

    def test_eval_failure_clears_readouts_and_toasts(self):
        body = function_body(self.src, "async function requestEval(immediate)")
        catch = body.split("} catch (e) {", 1)[1]
        self.assertIn("state.readouts = null", catch)
        self.assertIn("renderReadouts()", catch)
        self.assertIn("toast(e.message)", catch)

    def test_save_failure_keeps_readouts(self):
        # a rejected save must leave the previous readouts untouched
        body = function_body(self.src,
                             "async function saveForm(form, isEdit, errBox)")
        self.assertNotIn("state.readouts", body)

    def test_eval_url_keeps_project_path_and_verbatim_time(self):
        self.assertIn(
            "/api/projects/${state.projectId}/eval"
            "?t=${encodeURIComponent(String(tq))}", self.src)


if __name__ == "__main__":
    unittest.main()
