import pytest
import sys

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
