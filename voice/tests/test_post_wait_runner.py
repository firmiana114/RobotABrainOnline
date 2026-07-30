import json
import pytest
import sys
import threading
from types import SimpleNamespace

from voice_console.dialogue import DialogueError
from voice_console.post_wait_runner import main, wait_after_segment


def test_wait_after_segment_uses_configured_seconds():
    calls = []
    assert wait_after_segment({"text": "观看视频", "post_wait_seconds": 2.5}, calls.append) == 2.5
    assert calls == [2.5]


def test_wait_after_segment_skips_missing_or_zero_value():
    calls = []
    assert wait_after_segment({"text": "继续"}, calls.append) == 0
    assert wait_after_segment({"text": "继续", "post_wait_seconds": 0}, calls.append) == 0
    assert calls == []


def test_wait_after_segment_can_be_cancelled_without_waiting_full_duration(capsys):
    cancelled = False

    def cancel(_seconds):
        nonlocal cancelled
        cancelled = True

    result = wait_after_segment(
        {"text": "观看视频", "post_wait_seconds": 277},
        sleep=cancel,
        cancelled=lambda: cancelled,
    )

    assert result == 0
    assert "post-wait cancelled" in capsys.readouterr().out


@pytest.mark.parametrize("value", [-1, 3601, True, "10", [10]])
def test_wait_after_segment_rejects_invalid_value(value):
    with pytest.raises(DialogueError):
        wait_after_segment({"text": "等待", "post_wait_seconds": value}, lambda _seconds: None)


def test_runner_wraps_external_speak_segment(monkeypatch, tmp_path, capsys):
    script = tmp_path / "fake_route.py"
    script.write_text(
        "def speak_segment(*args):\n"
        "    print('spoken', flush=True)\n"
        "def main():\n"
        "    speak_segment(None, None, {'text': '视频', 'post_wait_seconds': 0.001}, 0, False, {})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["post_wait_runner", "--navigation-script", str(script)])

    main()

    output = capsys.readouterr().out
    assert "spoken" in output
    assert "post-wait start seconds=0.001" in output
    assert "post-wait complete seconds=0.001" in output


def test_runner_forwards_task_cancel_event(monkeypatch, tmp_path, capsys):
    script = tmp_path / "fake_route.py"
    script.write_text(
        "import threading\n"
        "class TourCancelled(RuntimeError):\n"
        "    pass\n"
        "def speak_segment(*args, **kwargs):\n"
        "    assert kwargs['cancel_event'] is args[0].route_cancel_event\n"
        "    print('spoken-with-cancel', flush=True)\n"
        "def main():\n"
        "    app = type('App', (), {})()\n"
        "    app.route_cancel_event = threading.Event()\n"
        "    speak_segment(app, None, {'text': '继续'}, 0, False, {}, cancel_event=app.route_cancel_event)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["post_wait_runner", "--navigation-script", str(script)])

    main()

    assert "spoken-with-cancel" in capsys.readouterr().out


def test_navigation_loader_preserves_post_wait_seconds(tmp_path):
    navigation = pytest.importorskip("nav_route_then_speak")
    segments = navigation.load_speech_segments(
        {
            "segments": [
                {
                    "text": "请观看宣传片。",
                    "post_wait_seconds": 277,
                    "action": "face_wave",
                }
            ]
        },
        tmp_path,
    )

    assert segments == [
        {
            "text": "请观看宣传片。",
            "post_wait_seconds": 277,
            "action": "face_wave",
            "speak_with_action": True,
        }
    ]
    calls = []
    assert wait_after_segment(segments[0], calls.append) == 277
    assert calls == [277.0]


def test_navigation_loader_preserves_extra_function_directives(tmp_path):
    navigation = pytest.importorskip("nav_route_then_speak")

    segments = navigation.load_speech_segments(
        {
            "segments": [
                {
                    "text": "边走边介绍。",
                    "speak_during_navigation": True,
                },
                {
                    "text": "我来呼叫咖啡小车。",
                    "background_command": "coffee_delivery_run",
                    "background_command_name": "呼叫AIR咖啡车",
                    "background_command_once_key": "coffee_delivery_run",
                },
            ]
        },
        tmp_path,
    )

    assert segments[0]["speak_during_navigation"] is True
    assert segments[1]["background_command"] == "coffee_delivery_run"
    assert segments[1]["background_command_name"] == "呼叫AIR咖啡车"
    assert segments[1]["background_command_once_key"] == "coffee_delivery_run"


def test_speak_segment_releases_gesture_after_speech(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    events = []

    class FakeNode:
        def send_arm_action(self, action_name):
            events.append(f"action:{action_name}")
            return True

    app = SimpleNamespace(route_voice_lock=threading.Lock())
    monkeypatch.setattr(
        navigation,
        "speak_playback_only",
        lambda _app, text, _index: events.append(f"speak:{text}"),
    )
    monkeypatch.setattr(
        navigation.time,
        "sleep",
        lambda seconds: events.append(f"sleep:{seconds}"),
    )

    navigation.speak_segment(
        app,
        FakeNode(),
        {
            "text": "我来给各位安排。",
            "action": "right_hand_up",
            "speak_with_action": True,
        },
        0,
        False,
        {},
    )

    assert events == [
        "action:right_hand_up",
        "sleep:0.15",
        "speak:我来给各位安排。",
        "action:release_arm",
    ]


def test_speak_segment_release_delay_starts_after_speech(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    events = []

    class FakeNode:
        def send_arm_action(self, action_name):
            events.append(f"action:{action_name}")
            return True

    app = SimpleNamespace(route_voice_lock=threading.Lock())
    monkeypatch.setattr(
        navigation,
        "speak_playback_only",
        lambda _app, text, _index: events.append(f"speak:{text}"),
    )
    monkeypatch.setattr(
        navigation.time,
        "sleep",
        lambda seconds: events.append(f"sleep:{seconds}"),
    )

    navigation.speak_segment(
        app,
        FakeNode(),
        {
            "text": "各位再会！",
            "action": "face_wave",
            "release_after_seconds": 0.5,
        },
        0,
        False,
        {},
    )

    assert events == [
        "action:face_wave",
        "sleep:0.15",
        "speak:各位再会！",
        "sleep:0.5",
        "action:release_arm",
    ]


def test_speak_segment_releases_gesture_when_speech_fails(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    actions = []

    class FakeNode:
        def send_arm_action(self, action_name):
            actions.append(action_name)
            return True

    app = SimpleNamespace(route_voice_lock=threading.Lock())
    monkeypatch.setattr(navigation.time, "sleep", lambda _seconds: None)

    def fail_speech(_app, _text, _index):
        raise RuntimeError("playback failed")

    monkeypatch.setattr(navigation, "speak_playback_only", fail_speech)

    with pytest.raises(RuntimeError, match="playback failed"):
        navigation.speak_segment(
            app,
            FakeNode(),
            {"text": "测试台词。", "action": "right_hand_up"},
            0,
            False,
            {},
        )

    assert actions == ["right_hand_up", "release_arm"]


def test_speak_segment_does_not_release_release_arm_again(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    actions = []

    class FakeNode:
        def send_arm_action(self, action_name):
            actions.append(action_name)
            return True

    app = SimpleNamespace(route_voice_lock=threading.Lock())
    monkeypatch.setattr(navigation.time, "sleep", lambda _seconds: None)

    navigation.speak_segment(
        app,
        FakeNode(),
        {"text": "", "action": "release_arm"},
        0,
        False,
        {},
    )

    assert actions == ["release_arm"]


def test_route_speaks_during_navigation_and_runs_background_command_once(
    monkeypatch,
    tmp_path,
):
    navigation = pytest.importorskip("nav_route_then_speak")
    route_file = tmp_path / "route.json"
    route_file.write_text(
        json.dumps(
            [
                {
                    "name": "point_3",
                    "goal": {"x": 1, "y": 2, "z": 0},
                    "segments": [
                        {
                            "text": "边走边介绍。",
                            "speak_during_navigation": True,
                            "background_command": "coffee_delivery_run",
                        },
                        {
                            "text": "到达后继续。",
                            "background_command": "coffee_delivery_run",
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    events = []

    class FakeNode:
        def wait_for_nav_subscriber(self, _timeout, cancel_event=None):
            return True

        def publish_goal(self, _goal):
            events.append("publish")

        def wait_arrived(self, _timeout, cancel_event=None):
            events.append("arrived")
            return "arrived"

    def fake_speak(
        _app,
        _node,
        segment,
        _index,
        _stt,
        _aliases,
        cancel_event=None,
    ):
        events.append(f"speak:{segment['text']}")

    monkeypatch.setattr(navigation, "speak_segment", fake_speak)
    monkeypatch.setattr(
        navigation,
        "start_background_command",
        lambda command, _project, cancel_event=None: events.append(f"command:{command}"),
    )
    args = SimpleNamespace(
        route_file=str(route_file),
        action_alias="",
        qa_context_file="",
        qa_context_max_chars=12000,
        publisher_wait=1,
        goal_timeout=1,
        voice_project=str(tmp_path),
    )
    app = SimpleNamespace(args=SimpleNamespace())

    navigation.execute_route_once(
        args,
        app,
        FakeNode(),
        stt_enabled=False,
        cancel_event=threading.Event(),
    )

    assert events == [
        "publish",
        "command:coffee_delivery_run",
        "speak:边走边介绍。",
        "arrived",
        "speak:到达后继续。",
    ]


def test_navigation_loader_rejects_unknown_background_command(tmp_path):
    navigation = pytest.importorskip("nav_route_then_speak")

    with pytest.raises(ValueError, match="unsupported background command"):
        navigation.load_speech_segments(
            {
                "segments": [
                    {
                        "text": "测试。",
                        "background_command": "arbitrary_script",
                    }
                ]
            },
            tmp_path,
        )


def test_background_command_calls_delivery_script_with_fixed_arguments(
    monkeypatch,
    tmp_path,
):
    navigation = pytest.importorskip("nav_route_then_speak")
    script = tmp_path / "send_delivery_task.py"
    script.write_text("# test placeholder\n", encoding="utf-8")
    calls = []

    def fake_run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(navigation.subprocess, "run", fake_run)
    thread = navigation.start_background_command(
        "coffee_delivery_run",
        tmp_path,
    )
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert calls[0][0] == [sys.executable, str(script), "run"]
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert calls[0][1]["timeout"] == navigation.BACKGROUND_COMMAND_TIMEOUT_SECONDS
