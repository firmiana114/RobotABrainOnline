import json
import time

from voice_console.pose import PoseTracker


def test_pose_freshness_and_validation():
    tracker = PoseTracker(stale_seconds=5)
    payload = {"x": 1, "y": 2, "z": 0, "ox": 0, "oy": 0, "oz": 0.5, "ow": 0.5}
    assert tracker.update_from_text(json.dumps(payload)) is True
    assert tracker.snapshot()["fresh"] is True
    with tracker._lock:
        tracker._updated_monotonic = time.monotonic() - 6
    assert tracker.snapshot()["fresh"] is False
    assert tracker.update_from_text("not-json") is False
