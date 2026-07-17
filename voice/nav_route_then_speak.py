#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import audioop
import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import dashscope
import numpy as np
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from voice_console.interrupt import (
    RouteInterruptCoordinator,
    answer_with_fallback,
    run_interruptible_segment,
)


ARRIVED = {"arrived", "mid_arrived"}
FAILED = {"failed", "nav_failed", "error"}
clean_api_key = None
clean_tts_text = None
logger = logging.getLogger("voice_route")


def load_route(path):
    with Path(path).open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("nodes") or data.get("route")
    if not isinstance(data, list) or not data:
        raise ValueError("route file must be a non-empty list, or {'nodes': [...]}")
    return data


def load_qa_context(path, max_chars):
    if not path:
        return ""
    context_path = Path(path).expanduser()
    if not context_path.exists():
        raise FileNotFoundError(f"qa context file not found: {context_path}")
    text = context_path.read_text(encoding="utf-8-sig").strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip()
        print(f"[voice] qa context truncated to {max_chars} chars", flush=True)
    print(f"[voice] loaded qa context: {context_path} ({len(text)} chars)", flush=True)
    return text


def normalize_goal(item):
    goal = dict(item.get("goal") or {})
    for key in ("x", "y", "z", "ox", "oy", "oz", "ow", "mode", "speed"):
        if key in item and key not in goal:
            goal[key] = item[key]

    for key in ("x", "y", "z"):
        if key not in goal:
            raise ValueError(f"route node missing {key}")

    result = {
        "x": float(goal["x"]),
        "y": float(goal["y"]),
        "z": float(goal["z"]),
        "ox": float(goal.get("ox", 0.0)),
        "oy": float(goal.get("oy", 0.0)),
        "oz": float(goal.get("oz", 0.0)),
        "ow": float(goal.get("ow", 1.0)),
        "mode": int(goal.get("mode", 1)),
    }
    if "speed" in goal and goal["speed"] is not None:
        result["speed"] = float(goal["speed"])
    return result


def load_speech_segments(item, base_dir):
    segments = item.get("segments")
    if isinstance(segments, list):
        result = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            speech_file = seg.get("speech_file")
            if speech_file:
                speech_path = Path(speech_file)
                if not speech_path.is_absolute():
                    speech_path = base_dir / speech_path
                text = speech_path.read_text(encoding="utf-8-sig").strip()
            if not text:
                continue
            loaded_segment = {
                "text": text,
                "action": seg.get("action") or seg.get("action_name"),
                "speak_with_action": bool(seg.get("speak_with_action", True)),
            }
            if "post_wait_seconds" in seg:
                loaded_segment["post_wait_seconds"] = seg["post_wait_seconds"]
            result.append(loaded_segment)
        return result

    if "speech_file" in item and item["speech_file"]:
        speech_path = Path(item["speech_file"])
        if not speech_path.is_absolute():
            speech_path = base_dir / speech_path
        text = speech_path.read_text(encoding="utf-8-sig").strip()
    else:
        text = str(item.get("speech") or "").strip()

    if not text:
        return []
    return [{
        "text": text,
        "action": item.get("action") or item.get("action_name"),
        "speak_with_action": bool(item.get("speak_with_action", True)),
    }]


def parse_action_aliases(raw):
    aliases = {
        "handshake_greeting": "shake_hand",
        "handshake_welcome": "shake_hand",
        "group_welcome": "face_wave",
    }
    if not raw:
        return aliases
    for item in raw.split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise ValueError(f"invalid action alias {item!r}, expected from=to")
        src, dst = item.split("=", 1)
        aliases[src.strip()] = dst.strip()
    return aliases


def resolve_action_name(action, aliases):
    if not action:
        return ""
    action = str(action).strip()
    return aliases.get(action, action)


def load_assistant(voice_project):
    global clean_api_key, clean_tts_text
    if voice_project not in sys.path:
        sys.path.insert(0, voice_project)
    from tour_guide_xiaozhi_interrupt_resume import (
        TourGuideAssistant,
        clean_api_key as _clean_api_key,
        clean_tts_text as _clean_tts_text,
    )
    clean_api_key = _clean_api_key
    clean_tts_text = _clean_tts_text
    return TourGuideAssistant


def resolve_aplay_device(args):
    if args.aplay_device:
        return args.aplay_device
    if not args.speaker_name:
        return None

    try:
        result = subprocess.run(
            ["aplay", "-l"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        print(f"[voice] failed to query playback devices: {exc!r}", flush=True)
        return None

    pattern = re.compile(r"card\s+(\d+):\s+.*\[(.*?)\],\s+device\s+(\d+):")
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        card, name, device = match.groups()
        if args.speaker_name.lower() in name.lower():
            aplay_device = f"plughw:{card},{device}"
            print(f"[voice] matched speaker {name!r}: {aplay_device}", flush=True)
            return aplay_device

    print(f"[voice] speaker {args.speaker_name!r} not found, use default playback", flush=True)
    return None


def make_voice_args(args):
    return SimpleNamespace(
        device_index=args.device_index,
        alsa_input_device=args.alsa_input_device,
        alsa_input_channels=args.alsa_input_channels,
        input_sample_rate=args.input_sample_rate,
        stt_sample_rate=args.stt_sample_rate,
        channels=1,
        chunk_ms=100,
        max_answer_sentences=args.max_answer_sentences,
        stt_model=args.stt_model,
        qwen_model=args.qwen_model,
        temperature=args.temperature,
        max_qwen_tokens=args.max_qwen_tokens,
        max_speak_chars=args.max_speak_chars,
        enable_search=args.enable_search,
        kokoro_repo_id=args.kokoro_repo_id,
        kokoro_voice=args.kokoro_voice,
        kokoro_speed=args.kokoro_speed,
        max_sentence_silence=args.max_sentence_silence,
        question_settle_time=args.question_settle_time,
        question_timeout=args.question_timeout,
        sentence_gap=0.4,
        resume_delay=args.resume_delay,
        loop_guide=False,
        aplay_device=resolve_aplay_device(args),
        base_dir=args.voice_project,
    )



def split_complete_sentences(buffer):
    sentences = []
    start = 0
    for match in re.finditer(r"[。！？!?;；]", buffer):
        end = match.end()
        sentence = buffer[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
    return sentences, buffer[start:].strip()


def split_fast_first_clause(buffer):
    text = buffer.strip()
    if len(text) < 8:
        return None, buffer
    # Start TTS as soon as a short conclusion clause is available.
    for match in re.finditer(r"[，,、：:]", text):
        end = match.end()
        if end >= 6:
            first = text[:end].rstrip("，,、：:").strip()
            rest = text[end:].strip()
            if first:
                return first + "。", rest
    if len(text) >= 18:
        return text[:18].strip() + "。", text[18:].strip()
    return None, buffer


def best_mono_channel(data, channels):
    if channels == 1:
        return data
    arr = np.frombuffer(data, dtype=np.int16)
    usable = (arr.size // channels) * channels
    if usable <= 0:
        return b""
    frames = arr[:usable].reshape(-1, channels)
    energy = np.mean(np.abs(frames.astype(np.int32)), axis=0)
    best = int(np.argmax(energy))
    return frames[:, best].astype(np.int16).tobytes()


class RouteSTTCallback(RecognitionCallback):
    def __init__(self, app):
        self.app = app

    def on_open(self):
        print("[stt] connected", flush=True)

    def on_close(self):
        print("\n[stt] closed", flush=True)

    def on_complete(self):
        print("\n[stt] complete", flush=True)

    def on_error(self, message):
        print("\n[stt] error:", getattr(message, "message", message), flush=True)

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()
        if not sentence:
            return
        text = (sentence.get("text") or "").strip()
        if not text:
            return

        mode, _, _ = self.app.get_mode()
        coordinator = self.app.route_interrupts
        if mode == "answer":
            return

        is_end = RecognitionResult.is_sentence_end(sentence)
        if not is_end:
            if self.app.contains_wake_word(text):
                if mode in ("guide", "interrupted"):
                    session_id = coordinator.claim_interrupt()
                    if session_id is not None:
                        logger.info(
                            "检测到播报中唤醒词：phase=partial session=%s text_length=%s",
                            session_id,
                            len(text),
                        )
                        self.app.request_stop_guide()
                else:
                    logger.info("检测到空闲唤醒词中间结果：text_length=%s", len(text))
            return

        mode, _, _ = self.app.get_mode()
        has_wake_word = self.app.contains_wake_word(text)
        if mode in ("guide", "interrupted") or coordinator.has_active_interrupt():
            if has_wake_word:
                session_id = coordinator.claim_interrupt()
                if session_id is not None:
                    self.app.request_stop_guide()
                question = self.app.remove_wake_word(text)
            else:
                question = text
            question = self.app.strip_guide_echo(question)
            accepted = coordinator.submit_interrupt_part(question)
            logger.info(
                "收到播报打断最终结果：accepted=%s text_length=%s",
                accepted,
                len(text),
            )
            return

        if mode == "idle" and has_wake_word:
            question = self.app.remove_wake_word(text)
            question = self.app.strip_guide_echo(question)
            if question:
                coordinator.submit_idle_question(question)


class ArecordSTT:
    def __init__(self, app, args):
        self.app = app
        self.args = args
        self.proc = None
        self.recognition = None
        self.rate_state = None
        self.running = False
        self.thread = None

    def start_arecord(self):
        cmd = [
            "arecord",
            "-q",
            "-D",
            self.args.alsa_input_device,
            "-f",
            "S16_LE",
            "-c",
            str(self.args.alsa_input_channels),
            "-r",
            str(self.args.input_sample_rate),
            "-t",
            "raw",
        ]
        print("[mic] arecord:", " ".join(cmd), flush=True)
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)

    def start(self):
        api_key = clean_api_key(os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY"))
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not set")
        dashscope.api_key = api_key

        self.proc = self.start_arecord()
        print("[mic] current input device:", self.args.alsa_input_device, flush=True)
        print("[mic] input sample rate:", self.args.input_sample_rate, flush=True)
        print("[mic] ALSA channels:", self.args.alsa_input_channels, flush=True)
        print("[stt] send sample rate:", self.args.stt_sample_rate, flush=True)
        print("[stt] model:", self.args.stt_model, flush=True)

        self.recognition = Recognition(
            model=self.args.stt_model,
            format="pcm",
            sample_rate=self.args.stt_sample_rate,
            language_hints=["zh", "en"],
            semantic_punctuation_enabled=False,
            max_sentence_silence=self.args.max_sentence_silence,
            punctuation_prediction_enabled=True,
            inverse_text_normalization_enabled=True,
            callback=RouteSTTCallback(self.app),
        )
        self.recognition.start()
        self.running = True
        self.thread = threading.Thread(target=self.audio_loop, daemon=True)
        self.thread.start()
        print("[stt] session started", flush=True)

    def audio_loop(self):
        bytes_per_frame = self.args.alsa_input_channels * 2
        chunk_frames = max(1, int(self.args.input_sample_rate * self.args.chunk_ms / 1000))
        chunk_bytes = chunk_frames * bytes_per_frame
        last_rms_time = 0.0

        while self.running:
            raw = self.proc.stdout.read(chunk_bytes)
            if not raw:
                err = self.proc.stderr.read().decode("utf-8", errors="ignore") if self.proc and self.proc.stderr else ""
                print("\n[mic] arecord stopped:", err.strip(), flush=True)
                break

            mono = best_mono_channel(raw, self.args.alsa_input_channels)
            if not mono:
                continue

            mode, _, _ = self.app.get_mode()
            now = time.time()
            if now - last_rms_time >= 1.0:
                # Debug audio level logging is intentionally disabled during guide runs.
                last_rms_time = now

            if mode == "answer":
                samples = int(self.args.stt_sample_rate * self.args.chunk_ms / 1000)
                data = b"\x00\x00" * samples
            else:
                data = mono
                if self.args.input_sample_rate != self.args.stt_sample_rate:
                    data, self.rate_state = audioop.ratecv(
                        data,
                        2,
                        1,
                        self.args.input_sample_rate,
                        self.args.stt_sample_rate,
                        self.rate_state,
                    )
            try:
                self.recognition.send_audio_frame(data)
            except Exception as exc:
                print("\n[stt] send_audio_frame failed:", repr(exc), flush=True)
                break

    def stop(self):
        self.running = False
        if self.recognition is not None:
            try:
                self.recognition.stop()
            except Exception:
                pass
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    pass


def synth_answer_sentence(app, sentence, index):
    sentence = clean_tts_text(sentence)
    if not sentence:
        return None
    with tempfile.NamedTemporaryFile(suffix=f"_route_answer_{index}.wav", delete=False, dir=str(app.runtime_dir)) as f:
        wav_path = Path(f.name)
    logger.info("准备合成回答音频：sentence_index=%s text_length=%s", index, len(sentence))
    t0 = time.perf_counter()
    app.synthesize_to_wav(sentence, wav_path)
    t1 = time.perf_counter()
    print(f"[answer:{index}] Kokoro synth: {(t1 - t0) * 1000:.2f} ms", flush=True)
    return wav_path


def play_answer_wav(app, wav_path):
    try:
        app.play_wav(wav_path, interruptible=False)
    finally:
        try:
            wav_path.unlink()
        except Exception:
            pass


def speak_one_answer_sentence(app, sentence, index):
    wav_path = synth_answer_sentence(app, sentence, index)
    if wav_path is not None:
        play_answer_wav(app, wav_path)


def start_answer_pipeline(app):
    synth_queue = queue.Queue()
    audio_queue = queue.Queue()
    errors = []
    errors_lock = threading.Lock()

    def synth_worker():
        while True:
            item = synth_queue.get()
            try:
                if item is None:
                    audio_queue.put(None)
                    return
                index, sentence = item
                wav_path = synth_answer_sentence(app, sentence, index)
                if wav_path is not None:
                    audio_queue.put((index, wav_path))
            except Exception as exc:
                logger.exception("回答音频合成失败：sentence_index=%s", index if item else None)
                with errors_lock:
                    errors.append(exc)
            finally:
                synth_queue.task_done()

    def play_worker():
        while True:
            item = audio_queue.get()
            try:
                if item is None:
                    return
                _, wav_path = item
                play_answer_wav(app, wav_path)
            except Exception as exc:
                logger.exception("回答音频播放失败")
                with errors_lock:
                    errors.append(exc)
            finally:
                audio_queue.task_done()

    synth_thread = threading.Thread(target=synth_worker, daemon=True)
    play_thread = threading.Thread(target=play_worker, daemon=True)
    synth_thread.start()
    play_thread.start()
    return synth_queue, audio_queue, synth_thread, play_thread, errors


def stream_qwen_and_speak_by_sentence(app, question, max_sentences):
    question = question.strip()
    if not question:
        return

    max_sentences = max(1, min(max_sentences, 3))
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    now = time.localtime()
    today = time.strftime("%Y年%m月%d日", now) + " " + weekdays[now.tm_wday]
    qa_context = getattr(app.args, "qa_context_text", "") or ""
    context_prompt = ""
    if qa_context:
        context_prompt = (
            "你现在是导览机器人小智。下面的【导览知识库】是本次导览的最高优先级资料，"
            "必须在之后每一次回答中主动记住并优先使用。只要用户问题与园区、展项、路线、机构、人物、技术、导览词有关，"
            "就必须先从【导览知识库】中查找答案并按其中表述回答；不要编造与资料冲突的内容。"
            "如果资料中没有明确答案，只说‘资料中没有提到’，再用一句通用知识简短补充。"
            "回答要口语化、简短，适合语音播报，不要Markdown，不要列项目符号。\n"
            "【导览知识库开始】\n"
            f"{qa_context}\n"
            "【导览知识库结束】\n"
        )
    prompt = (
        f"当前日期是{today}。请直接回答用户问题。简单问题只用一句话；"
        f"确需解释时最多{max_sentences}句话。每句话尽量短。"
        f"{context_prompt}"
        f"用户问题：{question}"
    )

    app.chat_messages.append({"role": "user", "content": prompt})
    system_msg = app.chat_messages[:1]
    history = app.chat_messages[1:]
    if len(history) > 12:
        history = history[-12:]
    messages = system_msg + history
    app.chat_messages = messages

    extra_body = None
    if app.args.enable_search:
        extra_body = {"enable_search": True, "search_options": {"forced_search": True, "search_strategy": "turbo"}}

    print(f"\nQwen(stream): started question_length={len(question)}", flush=True)
    answer_parts = []
    pending = ""
    spoken = 0
    synth_queue, audio_queue, synth_thread, play_thread, errors = start_answer_pipeline(app)

    try:
        stream = app.qwen_client.chat.completions.create(
            model=app.args.qwen_model,
            messages=messages,
            temperature=app.args.temperature,
            max_tokens=app.args.max_qwen_tokens,
            stream=True,
            extra_body=extra_body,
        )

        for chunk in stream:
            if spoken >= max_sentences:
                break
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None) or ""
            if not piece:
                continue
            answer_parts.append(piece)
            pending += piece
            sentences, pending = split_complete_sentences(pending)
            for sentence in sentences:
                if spoken >= max_sentences:
                    break
                spoken += 1
                synth_queue.put((spoken, sentence))

        if spoken < max_sentences:
            tail = pending.strip()
            if tail:
                spoken += 1
                if tail[-1] not in "。！？!?":
                    tail += "。"
                synth_queue.put((spoken, tail))
    finally:
        synth_queue.put(None)
        synth_queue.join()
        audio_queue.join()
        synth_thread.join(timeout=1.0)
        play_thread.join(timeout=1.0)

    if errors:
        raise RuntimeError("answer audio pipeline failed") from errors[0]

    final_answer = "".join(answer_parts).strip()
    if final_answer:
        app.chat_messages.append({"role": "assistant", "content": final_answer})
    logger.info(
        "流式回答播放完成：sentences=%s answer_length=%s",
        spoken,
        len(final_answer),
    )


def answer_then_resume(app, question):
    return answer_with_fallback(
        app,
        question,
        answer=lambda: stream_qwen_and_speak_by_sentence(
            app, question, app.args.max_answer_sentences
        ),
        fallback=lambda: speak_one_answer_sentence(
            app, "抱歉，刚才回答时出现了问题。", 1
        ),
    )
def init_voice(args):
    assistant_cls = load_assistant(args.voice_project)
    app = assistant_cls(make_voice_args(args))
    app.kokoro_device = args.kokoro_device
    app.preload_kokoro()
    app.route_voice_lock = threading.Lock()
    app.route_interrupts = RouteInterruptCoordinator(args.interrupt_boundary_grace)
    stt = ArecordSTT(app, app.args)
    app.set_mode("idle")
    return app, stt

def speak_with_interrupt_resume(app, text, index):
    run_interruptible_segment(
        app,
        app.route_interrupts,
        text,
        index,
        answer=lambda question: answer_then_resume(app, question),
        is_resume=is_resume_command,
    )


def speak_playback_only(app, text, index):
    wav_path = app.ensure_guide_wav(index, text)
    print("[voice] playback-only mode, playing without wake-word interrupt.", flush=True)
    app.play_wav(wav_path, interruptible=False)


def is_resume_command(text):
    normalized = re.sub(r"[\s，。！？!?、,.]", "", text or "")
    if not normalized:
        return False
    commands = {
        "继续",
        "继续导览",
        "继续捣览",
        "继续导游",
        "继续播报",
        "继续讲解",
        "继续介绍",
        "恢复导览",
        "恢复播报",
    }
    if normalized in commands:
        return True
    return normalized.startswith("继续") and any(
        word in normalized for word in ("导览", "捣览", "播报", "讲解", "介绍")
    )


def answer_background_question(app, question):
    question = question.strip()
    if not question:
        return
    if is_resume_command(question):
        print("[voice] ignore resume command while not in guide speech.", flush=True)
        return

    logger.info("开始处理空闲问答：question_length=%s", len(question))
    with app.route_voice_lock:
        answer_then_resume(app, question)


def background_question_loop(app, stop_event):
    print("[voice] background XiaoZhi question listener enabled.", flush=True)
    while app.running and not stop_event.is_set():
        question = app.route_interrupts.get_idle_question(timeout=0.1)
        if question is None:
            continue
        try:
            answer_background_question(app, question)
        finally:
            app.route_interrupts.idle_task_done()


def speak_segment(app, node, segment, index, stt_enabled, action_aliases):
    text = segment["text"]
    action = resolve_action_name(segment.get("action"), action_aliases)

    with app.route_voice_lock:
        if action and segment.get("speak_with_action", True):
            print(f"[route] segment action={action} text={text[:30]}", flush=True)
            node.send_arm_action(action)
            time.sleep(0.15)

        if stt_enabled:
            speak_with_interrupt_resume(app, text, index)
        else:
            speak_playback_only(app, text, index)

class RouteNode:
    def __init__(self, bridge_url):
        self.bridge_url = bridge_url.rstrip("/")
        self.arm_bridge_url = os.environ.get(
            "ARM_BRIDGE_URL", f"{self.bridge_url}/navi_arm"
        ).strip()
        self.arm_client = None
        self.last_status = ""
        self.seen_navigating = False
        self.status_seq = 0
        self.goal_status_seq = 0
        import logging
        self.logger = logging.getLogger("nav_route_http")
        self.logger.info("[nav] using Humble HTTP bridge: %s", self.bridge_url)

    def get_logger(self):
        return self.logger

    def _request(self, path, method="GET", payload=None, timeout=2.0):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.bridge_url}{path}", data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_for_nav_subscriber(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if int(self._request("/nav_state").get("goal_subscribers", 0)) > 0:
                    return True
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                pass
            time.sleep(0.2)
        return False

    def publish_goal(self, goal):
        state = self._request("/nav_state")
        self.status_seq = int(state.get("status_seq", 0))
        self.goal_status_seq = self.status_seq
        self.last_status = ""
        self.seen_navigating = False
        result = self._request("/goal", method="POST", payload=goal)
        self.logger.info(
            "[goal] bridge accepted payload_length=%s subscribers=%s",
            len(json.dumps(goal, ensure_ascii=False)),
            result.get("goal_subscribers", 0),
        )

    def send_arm_action(self, action_name):
        if not action_name:
            return False
        try:
            request = urllib.request.Request(
                self.arm_bridge_url,
                data=json.dumps({"action_name": action_name}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                accepted = 200 <= response.status < 300
            self.logger.info("[arm] bridge result action=%s accepted=%s", action_name, accepted)
            return accepted
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            self.logger.error("[arm] bridge send failed action=%s error_type=%s", action_name, type(exc).__name__)
            return False

    def wait_arrived(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._request("/nav_state")
            sequence = int(state.get("status_seq", 0))
            status = str(state.get("nav_status", "")).strip()
            navigating_seq = int(state.get("navigating_seq", 0))
            arrived_seq = int(state.get("arrived_seq", 0))
            failed_seq = int(state.get("failed_seq", 0))
            if failed_seq > self.goal_status_seq:
                raise RuntimeError(f"navigation failed; last_status={status!r}")
            if navigating_seq > self.goal_status_seq:
                self.seen_navigating = True
            if self.seen_navigating and arrived_seq > navigating_seq > self.goal_status_seq:
                self.last_status = status
                return status
            if sequence > self.status_seq:
                self.status_seq = sequence
                self.last_status = status
                if status:
                    self.logger.info("[nav_status] %s", status)
            time.sleep(0.1)
        raise TimeoutError(
            "timeout waiting for arrived/mid_arrived after navigating; "
            f"last_status={self.last_status!r}, seen_navigating={self.seen_navigating}"
        )

    def destroy_node(self):
        return None

def build_parser():
    parser = argparse.ArgumentParser(description="按路线逐点导航，到点后逐段播报")
    parser.add_argument("--route-file", required=True)
    parser.add_argument("--goal-timeout", type=float, default=180.0)
    parser.add_argument("--publisher-wait", type=float, default=20.0)
    parser.add_argument("--voice-project", default="/mnt/ssd/pc/projects/voice")
    parser.add_argument("--speaker-name", default="REDMI Speaker 2-6002")
    parser.add_argument("--aplay-device", default=None)
    parser.add_argument("--device-index", type=int, default=25)
    parser.add_argument("--alsa-input-device", default="plughw:CARD=Rx,DEV=0")
    parser.add_argument("--alsa-input-channels", type=int, default=2)
    parser.add_argument("--input-sample-rate", type=int, default=48000)
    parser.add_argument("--stt-sample-rate", type=int, default=16000)
    parser.add_argument("--stt-model", default="paraformer-realtime-v2")
    parser.add_argument("--qwen-model", default="qwen-plus")
    parser.add_argument("--enable-search", action="store_true")
    parser.add_argument("--max-sentence-silence", type=int, default=700)
    parser.add_argument("--question-settle-time", type=float, default=0.55)
    parser.add_argument("--question-timeout", type=float, default=4.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-qwen-tokens", type=int, default=160)
    parser.add_argument("--max-speak-chars", type=int, default=130)
    parser.add_argument("--max-answer-sentences", type=int, default=3)
    parser.add_argument("--kokoro-repo-id", default="hexgrad/Kokoro-82M-v1.1-zh")
    parser.add_argument("--kokoro-voice", default="zf_001")
    parser.add_argument("--kokoro-speed", type=float, default=1.0)
    parser.add_argument("--kokoro-device", default="cuda")
    parser.add_argument("--resume-delay", type=float, default=0.5)
    parser.add_argument("--interrupt-boundary-grace", type=float, default=0.9)
    parser.add_argument("--action-alias", default="")
    parser.add_argument("--qa-context-file", default="")
    parser.add_argument("--qa-context-max-chars", type=int, default=12000)
    return parser


def main():
    args = build_parser().parse_args()
    base_dir = Path(args.route_file).resolve().parent
    route = load_route(args.route_file)
    action_aliases = parse_action_aliases(args.action_alias)
    args.qa_context_text = load_qa_context(args.qa_context_file, args.qa_context_max_chars)

    voice_app, stt = init_voice(args)
    stt_enabled = False
    qa_stop = threading.Event()
    try:
        stt.start()
        stt_enabled = True
    except Exception as exc:
        print("[voice] failed to start arecord STT:", repr(exc), flush=True)
        print("[voice] continue in playback-only mode.", flush=True)
    if stt_enabled:
        threading.Thread(target=background_question_loop, args=(voice_app, qa_stop), daemon=True).start()

    def stop(_sig, _frame):
        print("\n收到退出信号，正在停止...")
        qa_stop.set()
        if stt_enabled:
            stt.stop()
        voice_app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    nav_bridge_url = os.environ.get("NAV_BRIDGE_URL", "").strip()
    if not nav_bridge_url:
        raise RuntimeError("NAV_BRIDGE_URL is required; all ROS access must use the Humble bridge")
    node = RouteNode(nav_bridge_url)

    try:
        node.get_logger().info("[arm] action requests will use the Humble HTTP bridge")

        if not node.wait_for_nav_subscriber(args.publisher_wait):
            raise RuntimeError("no subscriber on /go_goal_pose; start goGoalNavigation66 first")

        for index, item in enumerate(route, start=1):
            name = item.get("name") or f"point_{index}"
            segments = load_speech_segments(item, base_dir)

            print(f"\n[route] {index}/{len(route)} -> {name}", flush=True)
            if item.get("skip_navigation"):
                print(f"[route] {name} skips navigation", flush=True)
            else:
                goal = normalize_goal(item)
                node.publish_goal(goal)
                status = node.wait_arrived(args.goal_timeout)
                print(f"[route] arrived at {name}: {status}", flush=True)

            if segments:
                for seg_index, segment in enumerate(segments):
                    speak_index = (index - 1) * 100 + seg_index
                    speak_segment(voice_app, node, segment, speak_index, stt_enabled, action_aliases)
            else:
                print(f"[route] {name} has no speech, skip speaking", flush=True)

        print("[route] finished", flush=True)
    finally:
        qa_stop.set()
        if stt_enabled:
            stt.stop()
        voice_app.stop()
        node.destroy_node()


if __name__ == "__main__":
    main()
