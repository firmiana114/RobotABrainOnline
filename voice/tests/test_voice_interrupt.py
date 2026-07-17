from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from voice_console.interrupt import (
    AudioPlaybackError,
    RouteInterruptCoordinator,
    answer_with_fallback,
    play_audio_file,
    run_interruptible_segment,
)


class FakeApp:
    def __init__(self, play_steps):
        self.running = True
        self.args = SimpleNamespace(resume_delay=0)
        self.stop_guide_event = threading.Event()
        self.play_steps = list(play_steps)
        self.play_calls = []
        self.modes = []

    def set_mode(self, mode):
        self.modes.append(mode)

    def play_guide_sentence(self, index, text):
        self.play_calls.append((index, text))
        step = self.play_steps.pop(0)
        return step() if callable(step) else step


def run_segment(app, coordinator, answer, is_resume=lambda _question: False, **kwargs):
    return run_interruptible_segment(
        app,
        coordinator,
        "当前导览台词",
        7,
        answer=answer,
        is_resume=is_resume,
        sleep=lambda _seconds: None,
        **kwargs,
    )


def test_interrupted_guide_does_not_advance_before_answer_finishes():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    answer_started = threading.Event()
    release_answer = threading.Event()
    events = []

    def interrupt_playback():
        coordinator.claim_interrupt()
        coordinator.submit_interrupt_part("请介绍这个展项")
        return True

    app = FakeApp([interrupt_playback, False])

    def answer(_question):
        answer_started.set()
        release_answer.wait(1)
        events.append("answer-finished")

    def target():
        run_segment(app, coordinator, answer)
        events.append("next-segment")

    thread = threading.Thread(target=target)
    thread.start()
    assert answer_started.wait(1)
    assert len(app.play_calls) == 1
    assert events == []
    release_answer.set()
    thread.join(1)

    assert events == ["answer-finished", "next-segment"]
    assert len(app.play_calls) == 2


def test_wake_at_natural_wav_boundary_still_belongs_to_current_segment():
    coordinator = RouteInterruptCoordinator(boundary_grace=0.15)
    answered = []

    def natural_finish_then_wake():
        def delayed_wake():
            time.sleep(0.02)
            coordinator.claim_interrupt()
            coordinator.submit_interrupt_part("边界问题")

        threading.Thread(target=delayed_wake).start()
        return False

    app = FakeApp([natural_finish_then_wake, False])
    run_segment(app, coordinator, answered.append)

    assert answered == ["边界问题"]
    assert len(app.play_calls) == 2


def test_partial_wake_claims_session_before_delayed_final_result():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    answered = []

    def partial_wake():
        coordinator.claim_interrupt()

        def delayed_final():
            time.sleep(0.03)
            coordinator.submit_interrupt_part("最终完整问题")

        threading.Thread(target=delayed_final).start()
        return True

    app = FakeApp([partial_wake, False])
    run_segment(
        app,
        coordinator,
        answered.append,
        question_timeout=0.2,
        question_settle_time=0.01,
    )

    assert answered == ["最终完整问题"]


def test_idle_consumer_cannot_take_active_interrupt_question():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    coordinator.begin_attempt(1)
    session_id = coordinator.claim_interrupt()
    assert session_id is not None
    assert coordinator.submit_interrupt_part("当前打断问题") is True

    assert coordinator.get_idle_question(timeout=0.01) is None
    assert coordinator.collect_interrupt_question(session_id, 0, 0) == "当前打断问题"


def test_slow_inference_keeps_guide_paused():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    inference_started = threading.Event()
    release_inference = threading.Event()

    def interrupt_playback():
        coordinator.claim_interrupt()
        coordinator.submit_interrupt_part("需要慢推理的问题")
        return True

    app = FakeApp([interrupt_playback, False])

    def slow_answer(_question):
        inference_started.set()
        release_inference.wait(1)

    thread = threading.Thread(target=lambda: run_segment(app, coordinator, slow_answer))
    thread.start()
    assert inference_started.wait(1)
    time.sleep(0.03)
    assert len(app.play_calls) == 1
    release_inference.set()
    thread.join(1)
    assert len(app.play_calls) == 2


def test_resume_command_skips_qwen_and_replays_current_segment():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    answers = []

    def interrupt_playback():
        coordinator.claim_interrupt()
        coordinator.submit_interrupt_part("继续导览")
        return True

    app = FakeApp([interrupt_playback, False])
    run_segment(app, coordinator, answers.append, is_resume=lambda text: text == "继续导览")

    assert answers == []
    assert len(app.play_calls) == 2


def test_missing_question_times_out_then_replays_current_segment():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    answers = []

    def interrupt_playback():
        coordinator.claim_interrupt()
        return True

    app = FakeApp([interrupt_playback, False])
    run_segment(
        app,
        coordinator,
        answers.append,
        question_timeout=0.02,
        question_settle_time=0.01,
    )

    assert answers == []
    assert len(app.play_calls) == 2


def test_task_stop_during_playback_does_not_collect_question_or_replay():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    cancel_event = threading.Event()
    answers = []

    def stop_task():
        coordinator.claim_interrupt()
        cancel_event.set()
        return True

    app = FakeApp([stop_task])
    run_segment(
        app,
        coordinator,
        answers.append,
        cancelled=cancel_event.is_set,
    )

    assert answers == []
    assert len(app.play_calls) == 1
    assert app.modes[-1] == "idle"


class FailedProcess:
    def poll(self):
        return 2


def test_nonzero_aplay_exit_is_not_success(tmp_path: Path):
    wav_path = tmp_path / "guide.wav"
    wav_path.write_bytes(b"RIFF")

    with pytest.raises(AudioPlaybackError, match="returncode=2"):
        play_audio_file(
            wav_path,
            device="plughw:1,0",
            interruptible=True,
            stop_event=threading.Event(),
            running=lambda: True,
            popen=lambda _command: FailedProcess(),
        )


def test_answer_failure_plays_fallback_and_restores_idle():
    app = FakeApp([])
    fallback_calls = []

    def fail_answer():
        raise RuntimeError("mock inference failure")

    succeeded = answer_with_fallback(
        app,
        "不应进入日志的问题",
        answer=fail_answer,
        fallback=lambda: fallback_calls.append("played"),
    )

    assert succeeded is False
    assert fallback_calls == ["played"]
    assert app.modes == ["answer", "idle"]
    assert not app.stop_guide_event.is_set()


def test_answer_success_runs_resume_notice_before_restoring_idle():
    app = FakeApp([])
    events = []

    succeeded = answer_with_fallback(
        app,
        "不应进入日志的问题",
        answer=lambda: events.append("answer"),
        fallback=lambda: events.append("fallback"),
        after_success=lambda: events.append("notice"),
    )

    assert succeeded is True
    assert events == ["answer", "notice"]
    assert app.modes == ["answer", "idle"]


def test_answer_failure_does_not_run_resume_notice():
    app = FakeApp([])
    events = []

    def fail_answer():
        raise RuntimeError("mock failure")

    succeeded = answer_with_fallback(
        app,
        "不应进入日志的问题",
        answer=fail_answer,
        fallback=lambda: events.append("fallback"),
        after_success=lambda: events.append("notice"),
    )

    assert succeeded is False
    assert events == ["fallback"]


def test_route_interrupt_answer_enables_resume_notice(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    calls = []

    def fake_answer_then_resume(_app, question, announce_resume=False):
        calls.append((question, announce_resume))

    def fake_run(_app, _coordinator, _text, _index, answer, **_kwargs):
        answer("打断问题")

    monkeypatch.setattr(navigation, "answer_then_resume", fake_answer_then_resume)
    monkeypatch.setattr(navigation, "run_interruptible_segment", fake_run)
    app = SimpleNamespace(route_interrupts=object())

    navigation.speak_with_interrupt_resume(app, "当前导览台词", 2)

    assert calls == [("打断问题", True)]


def test_interrupt_resume_notice_uses_required_phrase(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    spoken = []

    def fake_answer_with_fallback(
        _app,
        _question,
        answer,
        fallback,
        after_success=None,
    ):
        assert after_success is not None
        after_success()
        return True

    monkeypatch.setattr(navigation, "answer_with_fallback", fake_answer_with_fallback)
    monkeypatch.setattr(
        navigation,
        "speak_one_answer_sentence",
        lambda _app, text, index: spoken.append((text, index)),
    )
    app = SimpleNamespace(args=SimpleNamespace(max_answer_sentences=3))

    succeeded = navigation.answer_then_resume(
        app,
        "打断问题",
        announce_resume=True,
    )

    assert succeeded is True
    assert spoken == [("如果没有别的问题，我将继续导览。", 4)]


def test_idle_answer_does_not_enable_resume_notice(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    calls = []
    app = SimpleNamespace(route_voice_lock=threading.Lock())

    monkeypatch.setattr(
        navigation,
        "answer_then_resume",
        lambda _app, question, announce_resume=False: calls.append(
            (question, announce_resume)
        ),
    )

    navigation.answer_background_question(app, "空闲问题")

    assert calls == [("空闲问题", False)]


def test_duplicate_final_result_is_consumed_once_and_not_reanswered_as_idle():
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    coordinator.begin_attempt(3)
    session_id = coordinator.claim_interrupt()
    assert session_id is not None
    assert coordinator.submit_interrupt_part("同一个问题") is True
    assert coordinator.submit_interrupt_part("同一个问题") is True

    assert coordinator.collect_interrupt_question(session_id, 0, 0) == "同一个问题"
    coordinator.complete_interrupt(session_id)
    assert coordinator.submit_idle_question("同一个问题") is False
    assert coordinator.get_idle_question(timeout=0.01) is None


def test_route_stt_partial_wake_and_final_share_interrupt_session(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")
    coordinator = RouteInterruptCoordinator(boundary_grace=0)
    coordinator.begin_attempt(9)

    class CallbackApp:
        def __init__(self):
            self.mode = "guide"
            self.route_interrupts = coordinator
            self.stop_requests = 0

        def get_mode(self):
            return self.mode, "", 9

        @staticmethod
        def contains_wake_word(text):
            return "小智" in text

        @staticmethod
        def remove_wake_word(text):
            return text.split("小智", 1)[-1].strip("，。 ")

        @staticmethod
        def strip_guide_echo(text):
            return text

        def request_stop_guide(self):
            self.stop_requests += 1
            self.mode = "interrupted"

    class Result:
        def __init__(self, text, is_end):
            self.text = text
            self.is_end = is_end

        def get_sentence(self):
            return {"text": self.text, "is_end": self.is_end}

    monkeypatch.setattr(
        navigation.RecognitionResult,
        "is_sentence_end",
        staticmethod(lambda sentence: sentence["is_end"]),
    )
    app = CallbackApp()
    callback = navigation.RouteSTTCallback(app)

    callback.on_event(Result("小智", False))
    callback.on_event(Result("小智，请介绍这里", True))

    assert app.stop_requests == 2
    assert coordinator.collect_interrupt_question(1, 0, 0) == "请介绍这里"


def test_route_stt_ignores_wake_word_while_preheated_but_not_running(monkeypatch):
    navigation = pytest.importorskip("nav_route_then_speak")

    class StandbyApp:
        route_task_active = False
        stop_requests = 0

        @staticmethod
        def get_mode():
            return "idle", "", -1

        def request_stop_guide(self):
            self.stop_requests += 1

    class Result:
        @staticmethod
        def get_sentence():
            return {"text": "小智，请介绍这里", "is_end": True}

    app = StandbyApp()
    navigation.RouteSTTCallback(app).on_event(Result())

    assert app.stop_requests == 0


def test_preheated_route_reloads_configuration_when_task_starts(monkeypatch, tmp_path):
    navigation = pytest.importorskip("nav_route_then_speak")
    route_file = tmp_path / "route.json"
    route_file.write_text(
        '[{"name":"opening","skip_navigation":true,'
        '"segments":[{"text":"欢迎参观。"}]}]',
        encoding="utf-8",
    )
    args = SimpleNamespace(
        route_file=str(route_file),
        action_alias="",
        qa_context_file="",
        qa_context_max_chars=100,
        publisher_wait=1,
        goal_timeout=1,
        qa_context_text="",
    )
    app = SimpleNamespace(args=SimpleNamespace(qa_context_text=""))
    cancel_event = threading.Event()
    spoken = []

    class Node:
        @staticmethod
        def wait_for_nav_subscriber(_timeout, cancel_event=None):
            return not cancel_event.is_set()

    monkeypatch.setattr(
        navigation,
        "speak_segment",
        lambda _app, _node, segment, index, stt_enabled, aliases, cancel_event=None: spoken.append(
            (segment["text"], index, stt_enabled, aliases, cancel_event.is_set())
        ),
    )

    navigation.execute_route_once(args, app, Node(), True, cancel_event)

    assert spoken == [("欢迎参观。", 0, True, navigation.parse_action_aliases(""), False)]


def test_preheated_route_stop_prevents_next_point(monkeypatch, tmp_path):
    navigation = pytest.importorskip("nav_route_then_speak")
    route_file = tmp_path / "route.json"
    route_file.write_text(
        '[{"name":"opening","skip_navigation":true,"segments":[]}]',
        encoding="utf-8",
    )
    args = SimpleNamespace(
        route_file=str(route_file),
        action_alias="",
        qa_context_file="",
        qa_context_max_chars=100,
        publisher_wait=1,
        goal_timeout=1,
        qa_context_text="",
    )
    app = SimpleNamespace(args=SimpleNamespace(qa_context_text=""))
    cancel_event = threading.Event()
    cancel_event.set()

    class Node:
        @staticmethod
        def wait_for_nav_subscriber(_timeout, cancel_event=None):
            raise navigation.TourCancelled("cancelled")

    with pytest.raises(navigation.TourCancelled):
        navigation.execute_route_once(args, app, Node(), True, cancel_event)


def test_preheat_generates_all_route_audio_without_playback(monkeypatch, tmp_path):
    navigation = pytest.importorskip("nav_route_then_speak")
    route_file = tmp_path / "route.json"
    route_file.write_text(
        '[{"name":"opening","skip_navigation":true,'
        '"segments":[{"text":"第一段。"},{"text":"第二段。"}]}]',
        encoding="utf-8",
    )
    args = SimpleNamespace(route_file=str(route_file))
    generated = []

    class App:
        @staticmethod
        def guide_wav_path(index, _text):
            return tmp_path / f"guide_{index}.wav"

        @staticmethod
        def ensure_guide_wav(index, text):
            generated.append((index, text))

    total, generated_count = navigation.precache_route_audio(args, App())

    assert total == 2
    assert generated_count == 2
    assert generated == [(0, "第一段。"), (1, "第二段。")]


def test_guide_cache_key_changes_with_kokoro_voice(tmp_path: Path):
    guide = pytest.importorskip("tour_guide_xiaozhi_interrupt_resume")
    assistant = object.__new__(guide.TourGuideAssistant)
    assistant.guide_cache_dir = tmp_path
    assistant.kokoro_repo_id = "hexgrad/Kokoro-82M"
    assistant.args = SimpleNamespace(kokoro_speed=1.0)

    assistant.kokoro_voice = "zf_001"
    female_path = assistant.guide_wav_path(1, "欢迎参观。")
    assistant.kokoro_voice = "zm_yunxi"
    male_path = assistant.guide_wav_path(1, "欢迎参观。")

    assert female_path != male_path
