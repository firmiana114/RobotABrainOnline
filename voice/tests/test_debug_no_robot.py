import json
import threading
from types import SimpleNamespace

import pytest

from voice_console.debug_no_robot import SimulatedRobot, select_points


def test_point_selection():
    assert select_points([1, 2, 3], 2, 0) == [2, 3]
    assert select_points([1, 2, 3], 2, 1) == [2]
    with pytest.raises(ValueError):
        select_points([1], 2, 1)


def test_real_route_executor_uses_simulation_only(monkeypatch, tmp_path):
    import nav_route_then_speak as route

    def forbidden(*args, **kwargs):
        pytest.fail("robot/network access attempted")

    monkeypatch.setattr(route, "RouteNode", forbidden)
    monkeypatch.setattr(route.urllib.request, "urlopen", forbidden)
    robot = SimulatedRobot(0)
    events = []
    original_action = robot.send_arm_action

    def action(name):
        original_action(name)
        events.append(name)

    robot.send_arm_action = action
    monkeypatch.setattr(route, "speak_with_interrupt_resume", lambda *a, **k: events.append("speech"))
    path = tmp_path / "route.json"
    path.write_text(json.dumps([{"name": "test", "goal": {"x": 0, "y": 0, "z": 0, "oz": 0, "ow": 1},
                                 "segments": [{"text": "测试", "action": "face_wave"}]}]))
    args = SimpleNamespace(route_file=str(path), action_alias="", qa_context_file="",
                           qa_context_max_chars=100, publisher_wait=0, goal_timeout=1)
    app = SimpleNamespace(args=SimpleNamespace(), route_voice_lock=threading.Lock())
    route.execute_route_once(args, app, robot, True, threading.Event())
    assert events == ["face_wave", "speech", "release_arm"]


def test_simulation_cancelled():
    import nav_route_then_speak as route
    stop = threading.Event()
    stop.set()
    with pytest.raises(route.TourCancelled):
        SimulatedRobot(10).wait_arrived(20, stop)
