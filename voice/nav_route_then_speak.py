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
from voice_console.config import DEFAULT_KOKORO_REPO_ID, DEFAULT_KOKORO_VOICE
from voice_console.tour_control import TourControlServer, TourTaskController


ARRIVED = {"arrived", "mid_arrived"}
FAILED = {"failed", "nav_failed", "error"}
clean_api_key = None
clean_tts_text = None
logger = logging.getLogger("voice_route")
INTERRUPT_RESUME_NOTICE = "如果没有别的问题，我将继续导览。"
BACKGROUND_COMMANDS = {
    "coffee_delivery_run": ("send_delivery_task.py", "run"),
}
BACKGROUND_COMMAND_TIMEOUT_SECONDS = 30.0


class TourCancelled(RuntimeError):
    pass


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


GUIDE_CONTEXT_QUERY_KEYWORDS = (
    "园区", "复星", "g1", "点位", "展项", "展厅", "展示", "展墙", "参观", "导览", "讲解",
    "路线", "开园", "企业", "厂房", "产业", "入驻", "招商", "配套", "停车", "餐厅",
    "便利店", "苏宁", "面积", "投资", "容积率", "机器人", "这里", "这个地方", "刚才",
    "在哪里", "在哪", "位置", "什么位置", "这里是哪里", "所在", "地址",
)
LOCATION_QUERY_KEYWORDS = ("你在哪里", "你在哪", "你在什么位置", "这里是哪里", "当前位置", "你的位置", "所在位置")
REALTIME_QUERY_KEYWORDS = ("今天", "现在", "实时", "新闻", "天气", "股价", "几点", "日期", "星期", "路况")
GUIDE_CONTEXT_STOPWORDS = {
    "什么", "怎么", "可以", "一下", "一个", "我们", "你们", "这个", "那个", "这里", "那里",
    "请问", "请你", "帮我", "知道", "介绍", "讲讲", "多少", "哪些", "有关", "相关",
}
GUIDE_CONTEXT_NOISE_TERMS = ("介绍", "请介", "请介绍", "一下", "讲讲", "帮我", "请问")


def split_guide_context_sections(text):
    sections = []
    title = "导览知识库"
    body = []

    def flush():
        nonlocal body
        content = "\n".join(line for line in body if line.strip()).strip()
        if content:
            sections.append((title, content))
        body = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("【") and line.endswith("】"):
            flush()
            title = line.strip("【】") or title
            continue
        body.append(line)
    flush()
    return sections


def guide_context_terms(text):
    lowered = (text or "").lower()
    terms = set(re.findall(r"[a-z0-9]+", lowered))
    chinese = re.sub(r"[^\u4e00-\u9fff]", "", lowered)
    for size in (2, 3, 4):
        for index in range(0, max(0, len(chinese) - size + 1)):
            term = chinese[index:index + size]
            if term in GUIDE_CONTEXT_STOPWORDS:
                continue
            if any(noise in term for noise in GUIDE_CONTEXT_NOISE_TERMS):
                continue
            terms.add(term)
    return terms


def select_relevant_guide_context(question, qa_context, max_sections=3, max_chars=1800):
    question = (question or "").strip()
    qa_context = (qa_context or "").strip()
    if not question or not qa_context:
        return ""

    lowered_question = question.lower()
    is_location_query = any(keyword in lowered_question for keyword in LOCATION_QUERY_KEYWORDS)
    if is_location_query:
        return "【当前位置】\n小智当前所在位置是滨湖复星人形机器人产业园。"
    has_guide_keyword = any(keyword in lowered_question for keyword in GUIDE_CONTEXT_QUERY_KEYWORDS)
    question_terms = guide_context_terms(question)
    if not question_terms and not has_guide_keyword:
        return ""

    scored = []
    for title, body in split_guide_context_sections(qa_context):
        section_text = f"{title}\n{body}"
        section_terms = guide_context_terms(section_text)
        overlap = question_terms & section_terms
        score = len(overlap)
        if title and title in question:
            score += 8
        if any(keyword in section_text.lower() and keyword in lowered_question for keyword in GUIDE_CONTEXT_QUERY_KEYWORDS):
            score += 2
        if score > 0:
            scored.append((score, title, body))

    if not scored:
        return "" if not has_guide_keyword else qa_context[:max_chars].rstrip()
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    if not has_guide_keyword and ranked[0][0] < 2:
        return ""

    selected = []
    total = 0
    min_score = max(2, ranked[0][0] - 1) if ranked and ranked[0][0] >= 4 else (2 if ranked and ranked[0][0] >= 2 else 1)
    for _score, title, body in [item for item in ranked if item[0] >= min_score][:max_sections]:
        section = f"【{title}】\n{body}".strip()
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(section) > remaining:
            section = section[:remaining].rstrip()
        selected.append(section)
        total += len(section) + 2
    return "\n\n".join(selected).strip()



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
            action = seg.get("action") or seg.get("action_name")
            if not text and not action:
                continue
            loaded_segment = {
                "text": text,
                "action": action,
                "speak_with_action": bool(seg.get("speak_with_action", True)),
            }
            if "post_wait_seconds" in seg:
                loaded_segment["post_wait_seconds"] = seg["post_wait_seconds"]
            if "release_after_seconds" in seg:
                loaded_segment["release_after_seconds"] = seg["release_after_seconds"]
            if "speak_during_navigation" in seg:
                if not isinstance(seg["speak_during_navigation"], bool):
                    raise ValueError("speak_during_navigation must be a boolean")
                loaded_segment["speak_during_navigation"] = seg["speak_during_navigation"]
            if "background_command" in seg:
                command = str(seg["background_command"])
                if command not in BACKGROUND_COMMANDS:
                    raise ValueError(f"unsupported background command: {command}")
                loaded_segment["background_command"] = command
                loaded_segment["background_command_name"] = str(
                    seg.get("background_command_name") or command
                )
                loaded_segment["background_command_once_key"] = str(
                    seg.get("background_command_once_key") or command
                )
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
    # Use the desktop audio server unless a caller explicitly requests ALSA.
    # Speaker names and USB card indices must not select a hardware bypass.
    return args.aplay_device or "pulse"


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


def remember_idle_stt_start(app):
    if getattr(app, "route_idle_stt_started_at", None) is None:
        app.route_idle_stt_started_at = time.monotonic()


def consume_idle_stt_timing(app):
    ended_at = time.monotonic()
    started_at = getattr(app, "route_idle_stt_started_at", None) or ended_at
    app.route_idle_stt_started_at = None
    return started_at, ended_at


def ms_between(start, end):
    if start is None or end is None:
        return None
    return max(0.0, (end - start) * 1000.0)


def fmt_ms(value):
    if value is None:
        return "unknown"
    return f"{value:.1f}"


def emit_interaction_event(event_type, **payload):
    event = {"type": event_type, **payload}
    print("[interaction] " + json.dumps(event, ensure_ascii=False), flush=True)


def unpack_question_payload(payload):
    if isinstance(payload, dict):
        return (payload.get("text") or "", payload)
    return (payload or "", None)


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
        if not getattr(self.app, "route_task_active", True):
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
                        question = self.app.remove_wake_word(text)
                        question = self.app.strip_guide_echo(question)
                        logger.info(
                            "检测到播报中唤醒词：phase=partial session=%s text_length=%s partial_question_length=%s",
                            session_id,
                            len(text),
                            len(question),
                        )
                        self.app.request_stop_guide()
                else:
                    remember_idle_stt_start(self.app)
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
            accepted = coordinator.submit_interrupt_part(question, stt_ended_at=time.monotonic())
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
                started_at, ended_at = consume_idle_stt_timing(self.app)
                coordinator.submit_idle_question(
                    question,
                    stt_started_at=started_at,
                    stt_ended_at=ended_at,
                )


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
    metrics = {
        "tts_started_at": None,
        "tts_finished_at": None,
        "synth_ms_total": 0.0,
        "play_ms_total": 0.0,
    }
    metrics_lock = threading.Lock()

    def synth_worker():
        while True:
            item = synth_queue.get()
            try:
                if item is None:
                    audio_queue.put(None)
                    return
                index, sentence = item
                synth_started_at = time.monotonic()
                with metrics_lock:
                    if metrics["tts_started_at"] is None:
                        metrics["tts_started_at"] = synth_started_at
                wav_path = synth_answer_sentence(app, sentence, index)
                synth_finished_at = time.monotonic()
                with metrics_lock:
                    metrics["synth_ms_total"] += (synth_finished_at - synth_started_at) * 1000.0
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
                play_started_at = time.monotonic()
                play_answer_wav(app, wav_path)
                play_finished_at = time.monotonic()
                with metrics_lock:
                    metrics["play_ms_total"] += (play_finished_at - play_started_at) * 1000.0
                    metrics["tts_finished_at"] = play_finished_at
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
    return synth_queue, audio_queue, synth_thread, play_thread, errors, metrics


def stream_qwen_and_speak_by_sentence(app, question, max_sentences, latency_timing=None):
    question = question.strip()
    if not question:
        return

    max_sentences = max(1, min(max_sentences, 3))
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    now = time.localtime()
    today = time.strftime("%Y年%m月%d日", now) + " " + weekdays[now.tm_wday]
    qa_context = getattr(app.args, "qa_context_text", "") or ""
    relevant_context = select_relevant_guide_context(question, qa_context)
    context_prompt = ""
    if relevant_context:
        context_prompt = (
            "下面的【导览知识库】只用于回答与园区、点位、展项、路线、讲解词有关的问题。"
            "如果用户问题与导览资料无关，请忽略这份资料并按普通对话直接回答。"
            "如果问题与导览相关，请优先使用资料；资料没有明确答案时，可以说明资料未提到，"
            "再给出一句简短通用回答。回答要口语化，适合语音播报，不要Markdown，不要列项目符号。\n"
            "【导览知识库开始】\n"
            f"{relevant_context}\n"
            "【导览知识库结束】\n"
        )
    prompt = (
        f"当前日期是{today}。请直接回答用户问题。简单问题只用一句话；"
        f"确需解释时最多{max_sentences}句话。每句话尽量短。"
        f"{context_prompt}"
        f"用户问题：{question}"
    )

    system_msg = app.chat_messages[:1]
    history = app.chat_messages[1:]
    if len(history) > 12:
        history = history[-12:]
    messages = system_msg + history + [{"role": "user", "content": prompt}]
    app.chat_messages = system_msg + history + [{"role": "user", "content": question}]

    is_realtime_query = any(keyword in question for keyword in REALTIME_QUERY_KEYWORDS)
    extra_body = None
    if app.args.enable_search and is_realtime_query:
        extra_body = {"enable_search": True, "search_options": {"forced_search": True, "search_strategy": "turbo"}}

    latency_timing = latency_timing or {}
    request_started_at = time.monotonic()
    stt_started_at = latency_timing.get("stt_started_at") or latency_timing.get("stt_ended_at") or request_started_at
    stt_ended_at = latency_timing.get("stt_ended_at") or request_started_at

    emit_interaction_event(
        "question",
        text=question,
        source=latency_timing.get("source") or "unknown",
        guide_context_chars=len(relevant_context),
    )
    print(
        f"\nQwen(stream): started question_length={len(question)} guide_context_chars={len(relevant_context)}",
        flush=True,
    )
    answer_parts = []
    pending = ""
    spoken = 0
    first_token_at = None
    reasoning_started_at = time.monotonic()
    reasoning_finished_at = None
    synth_queue, audio_queue, synth_thread, play_thread, errors, tts_metrics = start_answer_pipeline(app)

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
            if first_token_at is None:
                first_token_at = time.monotonic()
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
        reasoning_finished_at = time.monotonic()
    finally:
        if reasoning_finished_at is None:
            reasoning_finished_at = time.monotonic()
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
    emit_interaction_event("answer", text=final_answer, sentences=spoken)
    answer_finished_at = time.monotonic()
    stt_ms = ms_between(stt_started_at, stt_ended_at)
    reasoning_ms = ms_between(reasoning_started_at, reasoning_finished_at)
    first_token_ms = ms_between(reasoning_started_at, first_token_at)
    tts_started_at = tts_metrics.get("tts_started_at") or reasoning_finished_at
    tts_finished_at = tts_metrics.get("tts_finished_at") or answer_finished_at
    tts_ms = ms_between(tts_started_at, tts_finished_at)
    total_ms = ms_between(stt_started_at, answer_finished_at)
    logger.info(
        "问答耗时统计：stt_ms=%s reasoning_ms=%s tts_ms=%s total_ms=%s first_token_ms=%s "
        "synth_sum_ms=%s play_sum_ms=%s source=%s sentences=%s question_length=%s answer_length=%s guide_context_chars=%s",
        fmt_ms(stt_ms),
        fmt_ms(reasoning_ms),
        fmt_ms(tts_ms),
        fmt_ms(total_ms),
        fmt_ms(first_token_ms),
        fmt_ms(tts_metrics.get("synth_ms_total")),
        fmt_ms(tts_metrics.get("play_ms_total")),
        latency_timing.get("source") or "unknown",
        spoken,
        len(question),
        len(final_answer),
        len(relevant_context),
    )
    emit_interaction_event(
        "latency",
        stt_ms=fmt_ms(stt_ms),
        reasoning_ms=fmt_ms(reasoning_ms),
        tts_ms=fmt_ms(tts_ms),
        total_ms=fmt_ms(total_ms),
        first_token_ms=fmt_ms(first_token_ms),
        synth_sum_ms=fmt_ms(tts_metrics.get("synth_ms_total")),
        play_sum_ms=fmt_ms(tts_metrics.get("play_ms_total")),
    )
    print(
        "[latency] "
        f"stt_ms={fmt_ms(stt_ms)} reasoning_ms={fmt_ms(reasoning_ms)} "
        f"tts_ms={fmt_ms(tts_ms)} total_ms={fmt_ms(total_ms)}",
        flush=True,
    )


def answer_then_resume(app, question, announce_resume=False, latency_timing=None):
    after_success = None
    if announce_resume:
        def announce():
            logger.info("开始播放打断回答后的恢复导览提示")
            speak_one_answer_sentence(
                app,
                INTERRUPT_RESUME_NOTICE,
                app.args.max_answer_sentences + 1,
            )
            logger.info("打断回答后的恢复导览提示播放完成")

        after_success = announce

    return answer_with_fallback(
        app,
        question,
        answer=lambda: stream_qwen_and_speak_by_sentence(
            app, question, app.args.max_answer_sentences, latency_timing=latency_timing
        ),
        fallback=lambda: speak_one_answer_sentence(
            app, "抱歉，刚才回答时出现了问题。", 1
        ),
        after_success=after_success,
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


def speak_with_interrupt_resume(app, text, index, cancel_event=None):
    cancelled = cancel_event.is_set if cancel_event is not None else lambda: False
    run_interruptible_segment(
        app,
        app.route_interrupts,
        text,
        index,
        answer=lambda question, latency_timing=None: answer_then_resume(
            app, question, announce_resume=True, latency_timing=latency_timing
        ),
        is_resume=is_resume_command,
        cancelled=cancelled,
    )


def speak_playback_only(app, text, index):
    wav_path = app.ensure_guide_wav(index, text)
    print("[voice] playback-only mode, playing without wake-word interrupt.", flush=True)
    app.play_wav(wav_path, interruptible=True)


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


def answer_background_question(app, payload):
    question, latency_timing = unpack_question_payload(payload)
    question = question.strip()
    if not question:
        return
    if is_resume_command(question):
        print("[voice] ignore resume command while not in guide speech.", flush=True)
        return

    logger.info("开始处理空闲问答：question_length=%s", len(question))
    with app.route_voice_lock:
        answer_then_resume(app, question, latency_timing=latency_timing)


def background_question_loop(app, stop_event):
    print("[voice] background XiaoZhi question listener enabled.", flush=True)
    while app.running and not stop_event.is_set():
        payload = app.route_interrupts.get_idle_question_payload(timeout=0.1)
        if payload is None:
            continue
        try:
            answer_background_question(app, payload)
        finally:
            app.route_interrupts.idle_task_done()


def speak_segment(app, node, segment, index, stt_enabled, action_aliases, cancel_event=None):
    text = str(segment.get("text") or "").strip()
    action = resolve_action_name(segment.get("action"), action_aliases)
    cancel_event = cancel_event or getattr(app, "route_cancel_event", None)
    action_started = False
    release_delay = 0.0

    if cancel_event is not None and cancel_event.is_set():
        raise TourCancelled("tour task stopped before segment")

    with app.route_voice_lock:
        try:
            if action and segment.get("speak_with_action", True):
                print(f"[route] segment action={action} text={text[:30]}", flush=True)
                action_started = action != "release_arm"
                release_after = segment.get("release_after_seconds", 0.0)
                try:
                    release_delay = max(0.0, float(release_after))
                except (TypeError, ValueError):
                    logger.warning(
                        "无效的手势释放延迟，按 0 秒处理：source_action=%s value_type=%s",
                        action,
                        type(release_after).__name__,
                    )
                    release_delay = 0.0
                node.send_arm_action(action)
                if cancel_event is not None and cancel_event.wait(0.15):
                    raise TourCancelled("tour task stopped after arm action")
                if cancel_event is None:
                    time.sleep(0.15)

            if not text:
                logger.info("动作段执行完成：action=%s", action or "")
                return

            if stt_enabled:
                speak_with_interrupt_resume(app, text, index, cancel_event=cancel_event)
            else:
                speak_playback_only(app, text, index)
        finally:
            if action_started:
                cancelled_before_release = bool(
                    cancel_event is not None and cancel_event.is_set()
                )
                if release_delay > 0 and not cancelled_before_release:
                    if cancel_event is not None:
                        cancelled_before_release = cancel_event.wait(release_delay)
                    else:
                        time.sleep(release_delay)
                logger.info(
                    "台词手势自动释放：source_action=%s delay_seconds=%.3f "
                    "cancelled_before_release=%s text_length=%s",
                    action,
                    release_delay,
                    cancelled_before_release,
                    len(text),
                )
                released = node.send_arm_action("release_arm")
                if not released:
                    logger.warning(
                        "台词手势释放请求未被桥接服务接受：source_action=%s",
                        action,
                    )

    if cancel_event is not None and cancel_event.is_set():
        raise TourCancelled("tour task stopped during segment")

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

    def wait_for_nav_subscriber(self, timeout, cancel_event=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise TourCancelled("tour task stopped while waiting for navigation subscriber")
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

    def wait_arrived(self, timeout, cancel_event=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise TourCancelled("tour task stopped while waiting for navigation")
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
    parser.add_argument("--kokoro-repo-id", default=DEFAULT_KOKORO_REPO_ID)
    parser.add_argument("--kokoro-voice", default=DEFAULT_KOKORO_VOICE)
    parser.add_argument("--kokoro-speed", type=float, default=1.0)
    parser.add_argument("--kokoro-device", default="cuda")
    parser.add_argument("--resume-delay", type=float, default=0.5)
    parser.add_argument("--interrupt-boundary-grace", type=float, default=0.9)
    parser.add_argument("--action-alias", default="")
    parser.add_argument("--qa-context-file", default="")
    parser.add_argument("--qa-context-max-chars", type=int, default=12000)
    parser.add_argument("--standby-control", action="store_true")
    parser.add_argument("--control-host", default="127.0.0.1")
    parser.add_argument("--control-port", type=int, default=8766)
    return parser


def start_background_command(command_name, voice_project, cancel_event=None):
    command_spec = BACKGROUND_COMMANDS.get(command_name)
    if command_spec is None:
        raise ValueError(f"unsupported background command: {command_name}")
    script_name, *arguments = command_spec
    script_path = (Path(voice_project).resolve() / script_name).resolve()
    project_path = Path(voice_project).resolve()
    if script_path.parent != project_path or not script_path.is_file():
        raise FileNotFoundError(f"background command script not found: {script_path}")

    def worker():
        if cancel_event is not None and cancel_event.is_set():
            logger.info("后台功能命令已取消：command=%s reason=tour_cancelled", command_name)
            return
        started_at = time.perf_counter()
        logger.info(
            "后台功能命令开始：command=%s script=%s",
            command_name,
            script_path.name,
        )
        try:
            result = subprocess.run(
                [sys.executable, str(script_path), *arguments],
                cwd=str(project_path),
                check=False,
                capture_output=True,
                text=True,
                timeout=BACKGROUND_COMMAND_TIMEOUT_SECONDS,
            )
            elapsed_seconds = time.perf_counter() - started_at
            log = logger.info if result.returncode == 0 else logger.error
            log(
                "后台功能命令结束：command=%s return_code=%s elapsed_seconds=%.3f "
                "stdout_length=%s stderr_length=%s",
                command_name,
                result.returncode,
                elapsed_seconds,
                len(result.stdout or ""),
                len(result.stderr or ""),
            )
        except subprocess.TimeoutExpired:
            logger.error(
                "后台功能命令超时：command=%s timeout_seconds=%.1f",
                command_name,
                BACKGROUND_COMMAND_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("后台功能命令异常：command=%s", command_name)

    thread = threading.Thread(
        target=worker,
        name=f"route-command-{command_name}",
        daemon=True,
    )
    thread.start()
    return thread


def trigger_segment_background_command(
    segment,
    voice_project,
    executed_once_keys,
    cancel_event=None,
):
    command_name = str(segment.get("background_command") or "").strip()
    if not command_name:
        return None
    once_key = str(segment.get("background_command_once_key") or command_name)
    if once_key in executed_once_keys:
        logger.info(
            "后台功能命令跳过重复执行：command=%s once_key=%s",
            command_name,
            once_key,
        )
        return None
    executed_once_keys.add(once_key)
    logger.info(
        "触发台词后台功能：command=%s display_name=%s once_key=%s",
        command_name,
        str(segment.get("background_command_name") or command_name),
        once_key,
    )
    return start_background_command(
        command_name,
        voice_project,
        cancel_event=cancel_event,
    )


def execute_route_once(args, voice_app, node, stt_enabled, cancel_event):
    base_dir = Path(args.route_file).resolve().parent
    voice_project = Path(getattr(args, "voice_project", base_dir.parent))
    route = load_route(args.route_file)
    action_aliases = parse_action_aliases(args.action_alias)
    args.qa_context_text = load_qa_context(args.qa_context_file, args.qa_context_max_chars)
    voice_app.args.qa_context_text = args.qa_context_text

    stage_started = time.perf_counter()
    if not node.wait_for_nav_subscriber(args.publisher_wait, cancel_event=cancel_event):
        raise RuntimeError("no subscriber on /go_goal_pose; start goGoalNavigation66 first")
    print(
        "[startup] nav_subscriber_ready "
        f"elapsed_seconds={time.perf_counter() - stage_started:.3f}",
        flush=True,
    )
    executed_background_commands = set()

    for index, item in enumerate(route, start=1):
        if cancel_event.is_set():
            raise TourCancelled("tour task stopped before route point")
        name = item.get("name") or f"point_{index}"
        segments = load_speech_segments(item, base_dir)

        print(f"\n[route] {index}/{len(route)} -> {name}", flush=True)
        if item.get("skip_navigation"):
            print(f"[route] {name} skips navigation", flush=True)
            navigation_segments = []
            arrival_segments = list(enumerate(segments))
        else:
            goal = normalize_goal(item)
            node.publish_goal(goal)
            navigation_segments = [
                (seg_index, segment)
                for seg_index, segment in enumerate(segments)
                if segment.get("speak_during_navigation")
            ]
            arrival_segments = [
                (seg_index, segment)
                for seg_index, segment in enumerate(segments)
                if not segment.get("speak_during_navigation")
            ]
            if navigation_segments:
                logger.info(
                    "开始边走边说：route=%s segment_count=%s",
                    name,
                    len(navigation_segments),
                )
            for seg_index, segment in navigation_segments:
                if cancel_event.is_set():
                    raise TourCancelled("tour task stopped before navigation speech segment")
                trigger_segment_background_command(
                    segment,
                    voice_project,
                    executed_background_commands,
                    cancel_event=cancel_event,
                )
                speak_segment(
                    voice_app,
                    node,
                    segment,
                    (index - 1) * 100 + seg_index,
                    stt_enabled,
                    action_aliases,
                    cancel_event=cancel_event,
                )
            status = node.wait_arrived(args.goal_timeout, cancel_event=cancel_event)
            print(f"[route] arrived at {name}: {status}", flush=True)

        if arrival_segments:
            for seg_index, segment in arrival_segments:
                if cancel_event.is_set():
                    raise TourCancelled("tour task stopped before speech segment")
                trigger_segment_background_command(
                    segment,
                    voice_project,
                    executed_background_commands,
                    cancel_event=cancel_event,
                )
                speak_segment(
                    voice_app,
                    node,
                    segment,
                    (index - 1) * 100 + seg_index,
                    stt_enabled,
                    action_aliases,
                    cancel_event=cancel_event,
                )
        elif not navigation_segments:
            print(f"[route] {name} has no speech, skip speaking", flush=True)

    print("[route] finished", flush=True)


def precache_route_audio(args, voice_app):
    route = load_route(args.route_file)
    base_dir = Path(args.route_file).resolve().parent
    total = 0
    generated = 0
    for index, item in enumerate(route, start=1):
        for seg_index, segment in enumerate(load_speech_segments(item, base_dir)):
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            speak_index = (index - 1) * 100 + seg_index
            wav_path = voice_app.guide_wav_path(speak_index, text)
            cached = wav_path.exists() and wav_path.stat().st_size > 1000
            voice_app.ensure_guide_wav(speak_index, text)
            total += 1
            if not cached:
                generated += 1
    return total, generated


def main():
    startup_started = time.perf_counter()
    args = build_parser().parse_args()
    load_route(args.route_file)
    parse_action_aliases(args.action_alias)
    args.qa_context_text = load_qa_context(args.qa_context_file, args.qa_context_max_chars)

    stage_started = time.perf_counter()
    voice_app, stt = init_voice(args)
    print(
        "[startup] voice_ready "
        f"elapsed_seconds={time.perf_counter() - stage_started:.3f} "
        f"total_seconds={time.perf_counter() - startup_started:.3f} "
        f"repo={args.kokoro_repo_id} voice={args.kokoro_voice}",
        flush=True,
    )
    stage_started = time.perf_counter()
    cached_segments, generated_segments = precache_route_audio(args, voice_app)
    print(
        "[startup] guide_cache_ready "
        f"elapsed_seconds={time.perf_counter() - stage_started:.3f} "
        f"total_seconds={time.perf_counter() - startup_started:.3f} "
        f"segments={cached_segments} generated={generated_segments}",
        flush=True,
    )
    stt_enabled = False
    qa_stop = threading.Event()
    stage_started = time.perf_counter()
    try:
        stt.start()
        stt_enabled = True
    except Exception as exc:
        print("[voice] failed to start arecord STT:", repr(exc), flush=True)
        print("[voice] continue in playback-only mode.", flush=True)
    print(
        "[startup] stt_ready "
        f"elapsed_seconds={time.perf_counter() - stage_started:.3f} "
        f"total_seconds={time.perf_counter() - startup_started:.3f} "
        f"enabled={str(stt_enabled).lower()}",
        flush=True,
    )
    if stt_enabled:
        threading.Thread(target=background_question_loop, args=(voice_app, qa_stop), daemon=True).start()

    nav_bridge_url = os.environ.get("NAV_BRIDGE_URL", "").strip()
    if not nav_bridge_url:
        raise RuntimeError("NAV_BRIDGE_URL is required; all ROS access must use the Humble bridge")
    node = RouteNode(nav_bridge_url)
    controller = TourTaskController(stop_playback=voice_app.stop_guide_event.set)
    voice_app.route_cancel_event = controller.cancel_event
    voice_app.route_task_active = False
    control_server = None
    control_thread = None

    def stop(_sig, _frame):
        print("\n收到退出信号，正在关闭常驻导览服务...", flush=True)
        controller.request_shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        node.get_logger().info("[arm] action requests will use the Humble HTTP bridge")

        stage_started = time.perf_counter()
        if not node.wait_for_nav_subscriber(
            args.publisher_wait,
            cancel_event=controller.cancel_event,
        ):
            raise RuntimeError("no subscriber on /go_goal_pose; start goGoalNavigation66 first")
        print(
            "[startup] nav_subscriber_ready "
            f"elapsed_seconds={time.perf_counter() - stage_started:.3f} "
            f"total_seconds={time.perf_counter() - startup_started:.3f}",
            flush=True,
        )
        if not args.standby_control:
            voice_app.route_task_active = True
            execute_route_once(args, voice_app, node, stt_enabled, controller.cancel_event)
            return

        control_server = TourControlServer(
            (args.control_host, args.control_port),
            controller,
        )
        control_thread = threading.Thread(target=control_server.serve_forever, daemon=True)
        control_thread.start()
        controller.mark_ready()
        print(
            f"[control] ready host={args.control_host} port={args.control_port}",
            flush=True,
        )

        while not controller.shutdown_event.is_set():
            if not controller.wait_for_start(timeout=0.5):
                continue
            if controller.shutdown_event.is_set():
                break
            if controller.cancel_event.is_set():
                controller.mark_finished("stopped")
                print("[control] tour_stopped", flush=True)
                continue

            controller.mark_running()
            voice_app.route_task_active = True
            voice_app.stop_guide_event.clear()
            print("[control] tour_started", flush=True)
            outcome = "completed"
            try:
                execute_route_once(
                    args,
                    voice_app,
                    node,
                    stt_enabled,
                    controller.cancel_event,
                )
            except TourCancelled:
                outcome = "stopped"
            except Exception as exc:
                outcome = "failed"
                logger.exception(
                    "导览任务执行失败：error_type=%s",
                    type(exc).__name__,
                )
            finally:
                voice_app.route_task_active = False
                voice_app.stop_guide_event.clear()
                voice_app.set_mode("idle")
                controller.mark_finished(outcome)
                print(f"[control] tour_{outcome}", flush=True)
    finally:
        controller.request_shutdown()
        qa_stop.set()
        if control_server is not None:
            control_server.shutdown()
            control_server.server_close()
        if control_thread is not None:
            control_thread.join(timeout=2.0)
        if stt_enabled:
            stt.stop()
        voice_app.stop()
        node.destroy_node()


if __name__ == "__main__":
    main()
