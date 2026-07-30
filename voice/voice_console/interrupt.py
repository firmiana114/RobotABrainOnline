from __future__ import annotations

from collections import deque
import logging
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Callable


logger = logging.getLogger(__name__)


class AudioPlaybackError(RuntimeError):
    pass


def play_audio_file(
    wav_path: Path,
    device: str | None,
    interruptible: bool,
    stop_event: threading.Event,
    running: Callable[[], bool],
    process_changed: Callable[[object | None], None] | None = None,
    popen: Callable[..., object] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    wav_path = Path(wav_path)
    selected_device = device or "default"
    command = ["aplay", "-q"]
    if device:
        command.extend(["-D", device])
    command.append(str(wav_path))

    try:
        process = popen(command)
    except OSError as exc:
        logger.exception(
            "启动音频播放失败：device=%s file=%s error_type=%s",
            selected_device,
            wav_path.name,
            type(exc).__name__,
        )
        raise AudioPlaybackError(
            f"无法启动 aplay：device={selected_device} file={wav_path.name}"
        ) from exc

    if process_changed:
        process_changed(process)
    interrupted = False
    return_code = None
    try:
        while running():
            return_code = process.poll()
            if return_code is not None:
                break
            if interruptible and stop_event.is_set():
                interrupted = True
                try:
                    process.terminate()
                    return_code = process.wait(timeout=0.5)
                except Exception as exc:
                    logger.warning(
                        "音频主动打断时优雅终止失败：device=%s file=%s error_type=%s",
                        selected_device,
                        wav_path.name,
                        type(exc).__name__,
                    )
                    try:
                        process.kill()
                        return_code = process.wait(timeout=0.5)
                    except Exception as kill_exc:
                        logger.exception(
                            "音频主动打断时强制终止失败：device=%s file=%s error_type=%s",
                            selected_device,
                            wav_path.name,
                            type(kill_exc).__name__,
                        )
                        raise AudioPlaybackError(
                            f"无法终止 aplay：device={selected_device} file={wav_path.name}"
                        ) from kill_exc
                break
            sleep(0.03)
    finally:
        if process_changed:
            process_changed(None)

    if interrupted:
        logger.info(
            "音频播放被主动打断：device=%s file=%s returncode=%s",
            selected_device,
            wav_path.name,
            return_code,
        )
        return True

    if return_code is None:
        return_code = process.poll()
    if return_code != 0:
        logger.error(
            "音频播放异常退出：device=%s file=%s returncode=%s",
            selected_device,
            wav_path.name,
            return_code,
        )
        raise AudioPlaybackError(
            f"aplay 异常退出：device={selected_device} file={wav_path.name} returncode={return_code}"
        )

    logger.info("音频播放完成：device=%s file=%s", selected_device, wav_path.name)
    return False


class RouteInterruptCoordinator:
    def __init__(
        self,
        boundary_grace: float = 0.9,
        idle_queue_size: int = 16,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.boundary_grace = max(0.0, float(boundary_grace))
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._next_attempt = 0
        self._next_session = 0
        self._attempt: dict | None = None
        self._session: dict | None = None
        self._idle_questions: queue.Queue[object] = queue.Queue(maxsize=idle_queue_size)
        self._recent_interrupt_questions: deque[tuple[float, str]] = deque(maxlen=16)

    @staticmethod
    def _normalized(text: str) -> str:
        return "".join((text or "").lower().split()).strip("，。！？!?、,. ")

    def begin_attempt(self, segment_index: int) -> int:
        with self._condition:
            if self._attempt is not None:
                raise RuntimeError("已有未完成的导览播放 attempt")
            self._next_attempt += 1
            attempt_id = self._next_attempt
            self._attempt = {
                "id": attempt_id,
                "segment_index": int(segment_index),
                "phase": "playing",
            }
            logger.info(
                "导览播放 attempt 开始：attempt=%s segment=%s",
                attempt_id,
                segment_index,
            )
            return attempt_id

    def claim_interrupt(self) -> int | None:
        with self._condition:
            if self._attempt is None:
                return None
            if self._session is None:
                self._next_session += 1
                now = self._monotonic()
                self._session = {
                    "id": self._next_session,
                    "attempt_id": self._attempt["id"],
                    "parts": [],
                    "normalized_parts": set(),
                    "last_part_at": None,
                    "stt_started_at": now,
                    "stt_ended_at": None,
                }
                logger.info(
                    "导览打断已归属：attempt=%s session=%s segment=%s phase=%s",
                    self._attempt["id"],
                    self._session["id"],
                    self._attempt["segment_index"],
                    self._attempt["phase"],
                )
            self._attempt["phase"] = "interrupted"
            self._condition.notify_all()
            return int(self._session["id"])

    def has_active_interrupt(self) -> bool:
        with self._condition:
            return self._session is not None

    def submit_interrupt_part(self, text: str, stt_ended_at: float | None = None) -> bool:
        text = (text or "").strip("，。！？!?、,. ")
        normalized = self._normalized(text)
        if len(normalized) < 2:
            return False
        with self._condition:
            if self._session is None:
                return False
            now = self._monotonic()
            if normalized not in self._session["normalized_parts"]:
                self._session["parts"].append(text)
                self._session["normalized_parts"].add(normalized)
            self._session["last_part_at"] = now
            if self._session.get("stt_started_at") is None:
                self._session["stt_started_at"] = now
            self._session["stt_ended_at"] = stt_ended_at if stt_ended_at is not None else now
            logger.info(
                "导览打断问题片段已接收：session=%s parts=%s text_length=%s",
                self._session["id"],
                len(self._session["parts"]),
                len(text),
            )
            self._condition.notify_all()
            return True

    def finish_attempt(self, attempt_id: int, player_interrupted: bool) -> int | None:
        with self._condition:
            if self._attempt is None or self._attempt["id"] != attempt_id:
                raise RuntimeError(f"未知或已完成的导览播放 attempt：{attempt_id}")
            if player_interrupted and self._session is None:
                self.claim_interrupt()
            if self._session is None:
                self._attempt["phase"] = "boundary"
                deadline = self._monotonic() + self.boundary_grace
                while self._session is None:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
            if self._session is not None:
                logger.info(
                    "导览播放保持暂停：attempt=%s session=%s player_interrupted=%s",
                    attempt_id,
                    self._session["id"],
                    player_interrupted,
                )
                return int(self._session["id"])
            logger.info("导览播放 attempt 正常完成：attempt=%s", attempt_id)
            self._attempt = None
            return None

    def collect_interrupt_question(self, session_id: int, timeout: float, settle_time: float) -> str:
        timeout = max(0.0, float(timeout))
        settle_time = max(0.0, float(settle_time))
        with self._condition:
            if self._session is None or self._session["id"] != session_id:
                return ""
            deadline = self._monotonic() + timeout
            while True:
                now = self._monotonic()
                last_part_at = self._session["last_part_at"]
                if self._session["parts"] and last_part_at is not None:
                    settle_remaining = settle_time - (now - last_part_at)
                    if settle_remaining <= 0:
                        break
                else:
                    settle_remaining = None
                timeout_remaining = deadline - now
                if timeout_remaining <= 0:
                    break
                wait_seconds = timeout_remaining
                if settle_remaining is not None:
                    wait_seconds = min(wait_seconds, settle_remaining)
                self._condition.wait(wait_seconds)
            return "，".join(self._session["parts"]).strip("，。！？!?、,. ")

    def get_interrupt_timing(self, session_id: int) -> dict | None:
        with self._condition:
            if self._session is None or self._session["id"] != session_id:
                return None
            return {
                "stt_started_at": self._session.get("stt_started_at"),
                "stt_ended_at": self._session.get("stt_ended_at"),
                "source": "interrupt",
            }

    def complete_interrupt(self, session_id: int) -> None:
        with self._condition:
            if self._session is None or self._session["id"] != session_id:
                return
            expires_at = self._monotonic() + 2.0
            for normalized in self._session["normalized_parts"]:
                self._recent_interrupt_questions.append((expires_at, normalized))
            logger.info(
                "导览打断处理完成：session=%s parts=%s",
                session_id,
                len(self._session["parts"]),
            )
            self._session = None
            self._attempt = None
            self._condition.notify_all()

    def abort_attempt(self, attempt_id: int) -> None:
        with self._condition:
            if self._attempt is not None and self._attempt["id"] == attempt_id:
                logger.warning("导览播放 attempt 异常终止：attempt=%s", attempt_id)
                self._attempt = None
                self._session = None
                self._condition.notify_all()

    def submit_idle_question(
        self,
        text: str,
        stt_started_at: float | None = None,
        stt_ended_at: float | None = None,
    ) -> bool:
        text = (text or "").strip("，。！？!?、,. ")
        normalized = self._normalized(text)
        if len(normalized) < 2:
            return False
        with self._condition:
            if self._session is not None:
                return self.submit_interrupt_part(text, stt_ended_at=stt_ended_at)
            now = self._monotonic()
            while self._recent_interrupt_questions and self._recent_interrupt_questions[0][0] <= now:
                self._recent_interrupt_questions.popleft()
            if any(old == normalized for _, old in self._recent_interrupt_questions):
                logger.info("忽略打断会话的迟到重复结果：text_length=%s", len(text))
                return False
        ended_at = stt_ended_at if stt_ended_at is not None else self._monotonic()
        payload = {
            "text": text,
            "stt_started_at": stt_started_at if stt_started_at is not None else ended_at,
            "stt_ended_at": ended_at,
            "source": "idle",
        }
        try:
            self._idle_questions.put_nowait(payload)
        except queue.Full:
            logger.warning("空闲问答队列已满，丢弃新问题：text_length=%s", len(text))
            return False
        logger.info("空闲问题已入队：text_length=%s queue_size=%s", len(text), self._idle_questions.qsize())
        return True

    def get_idle_question_payload(self, timeout: float = 0.1) -> dict | None:
        try:
            item = self._idle_questions.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, dict):
            return item
        return {"text": item, "stt_started_at": None, "stt_ended_at": None, "source": "idle"}

    def get_idle_question(self, timeout: float = 0.1) -> str | None:
        payload = self.get_idle_question_payload(timeout=timeout)
        if payload is None:
            return None
        return payload.get("text")

    def idle_task_done(self) -> None:
        self._idle_questions.task_done()


def run_interruptible_segment(
    app,
    coordinator: RouteInterruptCoordinator,
    text: str,
    index: int,
    answer: Callable[[str], None],
    is_resume: Callable[[str], bool],
    question_timeout: float = 2.6,
    question_settle_time: float = 0.8,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = lambda: False,
) -> None:
    while app.running and not cancelled():
        attempt_id = coordinator.begin_attempt(index)
        try:
            player_interrupted = app.play_guide_sentence(index, text)
            session_id = coordinator.finish_attempt(attempt_id, player_interrupted)
        except Exception:
            coordinator.abort_attempt(attempt_id)
            raise
        if cancelled():
            if session_id is not None:
                coordinator.complete_interrupt(session_id)
            app.stop_guide_event.clear()
            app.set_mode("idle")
            return
        if session_id is None:
            app.set_mode("idle")
            return

        try:
            question = coordinator.collect_interrupt_question(
                session_id,
                timeout=question_timeout,
                settle_time=question_settle_time,
            )
            if cancelled():
                return
            if is_resume(question):
                logger.info("收到继续导览指令：session=%s", session_id)
            elif question:
                answer(question, coordinator.get_interrupt_timing(session_id))
            else:
                logger.info("导览打断未收到完整问题：session=%s", session_id)
        finally:
            coordinator.complete_interrupt(session_id)
            app.stop_guide_event.clear()
            app.set_mode("idle")
        if cancelled():
            return
        sleep(app.args.resume_delay)


def answer_with_fallback(
    app,
    question: str,
    answer: Callable[[], None],
    fallback: Callable[[], None],
    after_success: Callable[[], None] | None = None,
) -> bool:
    app.set_mode("answer")
    succeeded = False
    try:
        answer()
        succeeded = True
        if after_success is not None:
            try:
                after_success()
            except Exception as after_success_exc:
                logger.exception(
                    "回答完成后的恢复提示播放失败：error_type=%s",
                    type(after_success_exc).__name__,
                )
    except Exception as exc:
        logger.exception(
            "流式回答失败，播放降级提示：question_length=%s error_type=%s",
            len(question or ""),
            type(exc).__name__,
        )
        try:
            fallback()
        except Exception as fallback_exc:
            logger.exception(
                "回答降级提示播放失败：error_type=%s",
                type(fallback_exc).__name__,
            )
    finally:
        app.stop_guide_event.clear()
        app.set_mode("idle")
    return succeeded
