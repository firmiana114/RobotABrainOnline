#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import queue
import audioop
import signal
import hashlib
import argparse
import logging
import threading
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import dashscope
import pyaudio

from openai import OpenAI
from kokoro import KModel, KPipeline
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from voice_console.interrupt import play_audio_file
from voice_console.config import DEFAULT_KOKORO_REPO_ID, DEFAULT_KOKORO_VOICE


TOUR_SENTENCES = [
    "清华大学无锡应用技术研究院成立于2023年，是由清华大学与无锡市人民政府联合共建的科研机构。",
    "作为清华大学在长三角地区的重要成果转化派出机构，研究院坐落于无锡市滨湖区，旨在发挥清华大学的科技和人才优势，推动校地企紧密合作，促进科技成果产业化，带动地方高新技术产业的发展与转型升级。",
    "在研发方向上，研究院紧密围绕国家重大战略需求与地方特色产业发展，重点布局了集成电路、人工智能、高端装备、物联网、双碳技术以及生物医学等六大核心技术领域。",
    "目前，研究院已建成微纳电子与系统芯片、集成电路创新服务平台、能源物联网等30余个研究中心。",
    "同时，研究院引进了由张亚勤院士牵头的清华大学智能产业研究院，成立了智能产业创新中心，持续赋能前沿技术的研发与应用。",
]
logger = logging.getLogger("tour_guide")
KOKORO_CACHE_VERSION = "zh-prosody-v2"
KOKORO_MAX_CHUNK_PHONEMES = 220
KOKORO_HARD_PHONEME_LIMIT = 510


def clean_api_key(key):
    if not key:
        return key
    return re.sub(r"[\x00-\x20\x7f]+", "", key)


def normalize_text(text: str) -> str:
    text = text or ""
    text = text.lower()
    text = re.sub(r"[\s，。！？!?、,.；;：:“”\"'（）()\[\]【】《》<>]", "", text)
    return text


def clean_tts_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = text.replace("**", "")
    text = text.replace("*", "")
    text = text.replace("#", "")
    text = text.replace("`", "")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_complete_sentences(buffer: str):
    sentences = []
    start = 0
    for match in re.finditer(r"[。！？!?；;]", buffer):
        end = match.end()
        sentence = buffer[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
    return sentences, buffer[start:].strip()


def _kokoro_pause_after(text: str) -> float:
    ending = text.rstrip()[-1:] if text.strip() else ""
    if ending in "。！？!?":
        return 0.28
    if ending in "；;":
        return 0.20
    if ending in "，,、：:":
        return 0.12
    return 0.10


def _split_piece_by_phonemes(piece, phoneme_count, max_phonemes):
    remaining = piece.strip()
    chunks = []
    while remaining:
        if phoneme_count(remaining) <= max_phonemes:
            chunks.append(remaining)
            break

        low = 1
        high = len(remaining)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            candidate = remaining[:middle].rstrip()
            if candidate and phoneme_count(candidate + "，") <= max_phonemes:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best <= 0:
            raise RuntimeError("Kokoro 无法在安全音素长度内切分文本")

        preferred = max(
            remaining.rfind(mark, max(1, best // 2), best)
            for mark in (" ", "、", "，", ",", "：", ":")
        )
        cut = preferred + 1 if preferred >= 0 else best
        chunk = remaining[:cut].strip()
        if chunk[-1:] not in "，,、：:；;。！？!?":
            chunk += "，"
        chunks.append(chunk)
        remaining = remaining[cut:].strip()
    return chunks


def split_kokoro_text(text, phoneme_count, max_phonemes=KOKORO_MAX_CHUNK_PHONEMES):
    if max_phonemes <= 0 or max_phonemes >= KOKORO_HARD_PHONEME_LIMIT:
        raise ValueError("Kokoro 分块音素上限必须在 1 到 509 之间")

    pieces = re.findall(r"[^，,、：:；;。！？!?\n]+[，,、：:；;。！？!?]?|\n+", text)
    expanded = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        expanded.extend(_split_piece_by_phonemes(piece, phoneme_count, max_phonemes))

    chunks = []
    current = ""
    for piece in expanded:
        candidate = current + piece
        if current and phoneme_count(candidate) > max_phonemes:
            chunks.append((current, _kokoro_pause_after(current), phoneme_count(current)))
            current = piece
        else:
            current = candidate
    if current:
        chunks.append((current, _kokoro_pause_after(current), phoneme_count(current)))

    if not chunks:
        raise RuntimeError("Kokoro 没有可合成的文本分块")
    if any(count > max_phonemes for _, _, count in chunks):
        raise RuntimeError("Kokoro 文本分块超过安全音素上限")
    return chunks


class RawAlsaInputStream:
    def __init__(self, device: str, sample_rate: int, channels: int, frames_per_buffer: int):
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.frames_per_buffer = frames_per_buffer
        self.proc = subprocess.Popen(
            [
                "arecord",
                "-q",
                "-D",
                device,
                "-f",
                "S16_LE",
                "-c",
                str(channels),
                "-r",
                str(sample_rate),
                "-t",
                "raw",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
        time.sleep(0.1)
        if self.proc.poll() is not None:
            err = self.proc.stderr.read().decode("utf-8", errors="ignore").strip()
            raise RuntimeError("arecord failed to start" + (": " + err if err else ""))

    def read(self, frames, exception_on_overflow=False):
        raw = self.proc.stdout.read(frames * self.channels * 2)
        if not raw:
            try:
                if self.proc.poll() is None:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    self.proc.wait(timeout=0.5)
                err = self.proc.stderr.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                err = ""
            raise RuntimeError(
                "arecord input stopped"
                + (": " + err if err else "")
            )
        if self.channels == 1:
            return raw

        arr = np.frombuffer(raw, dtype=np.int16)
        usable = (arr.size // self.channels) * self.channels
        if usable <= 0:
            return b""
        frames_arr = arr[:usable].reshape(-1, self.channels)
        energy = np.mean(np.abs(frames_arr.astype(np.int32)), axis=0)
        best = int(np.argmax(energy))
        return frames_arr[:, best].astype(np.int16).tobytes()

    def stop_stream(self):
        if self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=1.0)
        except Exception:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except Exception:
                pass

    def close(self):
        self.stop_stream()


class TourGuideAssistant:
    def __init__(self, args):
        self.args = args

        api_key = clean_api_key(
            os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError("没有找到 DASHSCOPE_API_KEY，请先设置 DashScope API Key")

        dashscope.api_key = api_key

        self.qwen_client = OpenAI(
            api_key=api_key,
            base_url=(
                os.getenv("OPENAI_BASE_URL")
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
        )

        self.running = True

        self.mode_lock = threading.Lock()
        self.mode = "init"
        self.current_guide_sentence = ""
        self.current_guide_index = -1

        self.audio = None
        self.stream = None
        self.ratecv_state = None
        self.frames_per_buffer = int(args.input_sample_rate * args.chunk_ms / 1000)

        self.recognition = None
        self.recognition_lock = threading.Lock()
        self.stt_alive = False
        self.stt_restart_event = threading.Event()

        self.stop_guide_event = threading.Event()
        self.question_queue = queue.Queue()

        self.pending_lock = threading.Lock()
        self.pending_question_parts = []

        self.current_play_proc = None

        self.base_dir = Path(args.base_dir)
        self.guide_cache_dir = self.base_dir / "guide_cache"
        self.runtime_dir = self.base_dir / "runtime"
        self.guide_cache_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.kokoro_ready = False
        self.kokoro_model = None
        self.kokoro_pipeline = None
        self.kokoro_en_pipeline = None
        self.kokoro_repo_id = args.kokoro_repo_id
        self.kokoro_voice = args.kokoro_voice
        self.kokoro_sample_rate = 24000
        self.kokoro_device = "cuda" if torch.cuda.is_available() else "cpu"

        self.chat_messages = [
            {
                "role": "system",
                "content": (
                    "你是清华大学无锡应用技术研究院的中文导览问答助手。"
                    "用户会在导览过程中用“小智”打断你并提问。"
                    "回答要简洁、准确、口语化。"
                    "不要使用Markdown，不要列太长。"
                    "如果用户问实时信息，可以结合联网搜索结果回答。"
                ),
            }
        ]

    def set_mode(self, mode: str, sentence: str = "", index: int = -1):
        with self.mode_lock:
            self.mode = mode
            if sentence:
                self.current_guide_sentence = sentence
            if index >= -1:
                self.current_guide_index = index

    def get_mode(self):
        with self.mode_lock:
            return self.mode, self.current_guide_sentence, self.current_guide_index

    def contains_wake_word(self, text: str) -> bool:
        return self.find_wake_word_end(text) >= 0

    def find_wake_word_end(self, text: str) -> int:
        if not text:
            return -1

        patterns = [
            r"小\s*智",
            r"小\s*知",
            r"小\s*志",
            r"晓\s*智",
        ]

        best_end = -1
        for pat in patterns:
            for m in re.finditer(pat, text):
                if m.end() > best_end:
                    best_end = m.end()

        return best_end

    def remove_wake_word(self, text: str) -> str:
        end = self.find_wake_word_end(text)
        if end < 0:
            return text.strip()
        return text[end:].strip("，。！？!?、,. ")

    def strip_guide_echo(self, text: str) -> str:
        raw = (text or "").strip()
        raw = raw.strip("，。！？!?、,. ")

        if not raw:
            return ""

        mode, guide_sentence, _ = self.get_mode()

        candidates = []
        if guide_sentence:
            candidates.append(guide_sentence)
        candidates.extend(TOUR_SENTENCES)

        for cand in sorted(candidates, key=len, reverse=True):
            cand = cand.strip()
            if not cand:
                continue

            if raw.startswith(cand):
                raw = raw[len(cand):].strip("，。！？!?、,. ")
                break

            pos = raw.find(cand)
            if pos >= 0:
                raw = raw[pos + len(cand):].strip("，。！？!?、,. ")
                break

        if self.contains_wake_word(raw):
            raw = self.remove_wake_word(raw)

        q_marks = [
            "谁", "什么", "哪里", "哪儿", "为什么", "怎么", "如何",
            "吗", "几", "多少", "介绍", "说一下", "讲一下"
        ]

        if len(raw) > 30:
            parts = re.split(r"[。！？!?；;]", raw)
            parts = [x.strip("，。！？!?、,. ") for x in parts if x.strip()]

            question_parts = [x for x in parts if any(q in x for q in q_marks)]
            if question_parts:
                raw = question_parts[-1]
            elif parts:
                raw = parts[-1]

        if len(raw) > 30:
            best_pos = -1
            for mark in q_marks:
                pos = raw.rfind(mark)
                if pos > best_pos:
                    best_pos = pos

            if best_pos >= 0:
                start = max(0, best_pos - 14)
                raw = raw[start:]

        raw = raw.strip("，。！？!?、,. ")

        return raw

    def add_pending_question_part(self, text: str):
        text = self.strip_guide_echo(text)
        if not text:
            return

        if len(normalize_text(text)) < 2:
            return

        with self.pending_lock:
            self.pending_question_parts.append(text)

    def pop_pending_question_parts(self):
        with self.pending_lock:
            parts = list(self.pending_question_parts)
            self.pending_question_parts.clear()
        return parts

    def clear_question_queue(self):
        while True:
            try:
                self.question_queue.get_nowait()
            except queue.Empty:
                break

    def request_stop_guide(self, text: str = ""):
        mode, sent, idx = self.get_mode()

        if text and self.contains_wake_word(text):
            tail = self.remove_wake_word(text)
            self.add_pending_question_part(tail)

        if mode in ("guide", "interrupted"):
            self.set_mode("interrupted", sent, idx)
            self.stop_guide_event.set()

    def init_audio(self):
        alsa_input_device = getattr(self.args, "alsa_input_device", "") or ""
        if alsa_input_device:
            alsa_channels = int(getattr(self.args, "alsa_input_channels", 2) or 2)
            self.args.channels = 1
            print("current input device:", alsa_input_device)
            print("ALSA input channels:", alsa_channels)
            print("input sample rate:", self.args.input_sample_rate)
            print("STT sample rate:", self.args.stt_sample_rate)
            print("STT model:", self.args.stt_model)
            print("Qwen model:", self.args.qwen_model)
            print("enable search:", self.args.enable_search)
            print("Kokoro device:", self.kokoro_device)
            print("Kokoro voice:", self.kokoro_voice)
            print("playback device:", self.args.aplay_device or "system default")
            print("cache dir:", str(self.base_dir))
            print("HF_HOME:", os.getenv("HF_HOME"))
            print()
            self.stream = RawAlsaInputStream(
                alsa_input_device,
                self.args.input_sample_rate,
                alsa_channels,
                self.frames_per_buffer,
            )
            return

        self.audio = pyaudio.PyAudio()

        if self.args.device_index is not None:
            info = self.audio.get_device_info_by_index(self.args.device_index)
        else:
            info = self.audio.get_default_input_device_info()

        print("当前输入设备:", info.get("name"))
        print("设备默认采样率:", info.get("defaultSampleRate"))
        print("实际打开采样率:", self.args.input_sample_rate)
        print("发送给 STT 采样率:", self.args.stt_sample_rate)
        print("STT 模型:", self.args.stt_model)
        print("Qwen 模型:", self.args.qwen_model)
        print("联网搜索:", self.args.enable_search)
        print("Kokoro 设备:", self.kokoro_device)
        print("Kokoro voice:", self.kokoro_voice)
        print("播放设备:", self.args.aplay_device or "系统默认")
        print("缓存目录:", str(self.base_dir))
        print("HF_HOME:", os.getenv("HF_HOME"))
        print()

        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=self.args.channels,
            rate=self.args.input_sample_rate,
            input=True,
            input_device_index=self.args.device_index,
            frames_per_buffer=self.frames_per_buffer,
        )

    def reopen_alsa_input(self):
        alsa_input_device = getattr(self.args, "alsa_input_device", "") or ""
        if not alsa_input_device:
            return False

        if self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        alsa_channels = int(getattr(self.args, "alsa_input_channels", 2) or 2)
        try:
            self.stream = RawAlsaInputStream(
                alsa_input_device,
                self.args.input_sample_rate,
                alsa_channels,
                self.frames_per_buffer,
            )
            print("[mic] ALSA input reconnected:", alsa_input_device, flush=True)
            return True
        except Exception as exc:
            print("[mic] ALSA input reconnect failed:", repr(exc), flush=True)
            self.stream = None
            return False

    def close_audio(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if self.audio is not None:
            try:
                self.audio.terminate()
            except Exception:
                pass
            self.audio = None

    def preload_kokoro(self):
        if self.kokoro_ready:
            return

        print("正在预加载 Kokoro-82M，本步骤第一次会下载模型到 SSD 的 Hugging Face 缓存...")
        t0 = time.perf_counter()

        self.kokoro_model = KModel(
            repo_id=self.kokoro_repo_id
        ).to(self.kokoro_device).eval()

        self.kokoro_en_pipeline = KPipeline(
            lang_code="a",
            repo_id=self.kokoro_repo_id,
            model=False,
        )

        def en_callable(en_text):
            return next(self.kokoro_en_pipeline(en_text)).phonemes

        self.kokoro_pipeline = KPipeline(
            lang_code="z",
            repo_id=self.kokoro_repo_id,
            model=self.kokoro_model,
            en_callable=en_callable,
        )

        with torch.inference_mode():
            _ = next(
                self.kokoro_pipeline(
                    "你好。",
                    voice=self.kokoro_voice,
                    speed=self.args.kokoro_speed,
                )
            )

        t1 = time.perf_counter()
        self.kokoro_ready = True

        print(f"Kokoro 预加载完成，耗时: {(t1 - t0) * 1000:.2f} ms")
        print()

    def extract_kokoro_audio(self, result):
        if hasattr(result, "audio"):
            audio = result.audio
        else:
            audio = result[2]

        if isinstance(audio, torch.Tensor):
            audio = audio.detach().cpu().numpy()

        return np.asarray(audio, dtype=np.float32)

    def synthesize_to_wav(self, text: str, wav_path: Path):
        text = clean_tts_text(text)

        wav_path = Path(wav_path)
        wav_path.parent.mkdir(parents=True, exist_ok=True)

        phoneme_counts = {}

        def phoneme_count(value):
            if value not in phoneme_counts:
                phonemes, _ = self.kokoro_pipeline.g2p(value)
                phoneme_counts[value] = len(phonemes)
            return phoneme_counts[value]

        chunks = split_kokoro_text(text, phoneme_count)
        audios = []
        total_pause_seconds = 0.0

        with torch.inference_mode():
            for chunk_index, (chunk, pause_seconds, phonemes) in enumerate(chunks):
                if phonemes >= KOKORO_HARD_PHONEME_LIMIT:
                    raise RuntimeError("Kokoro 文本分块触及硬音素上限")
                generator = self.kokoro_pipeline(
                    chunk,
                    voice=self.kokoro_voice,
                    speed=self.args.kokoro_speed,
                    split_pattern=None,
                )
                generated = 0
                for result in generator:
                    audio = self.extract_kokoro_audio(result)
                    if audio.size > 0:
                        if generated:
                            audios.append(
                                np.zeros(int(self.kokoro_sample_rate * 0.10), dtype=np.float32)
                            )
                            total_pause_seconds += 0.10
                        audios.append(audio)
                        generated += 1
                if generated == 0:
                    raise RuntimeError("Kokoro 文本分块没有生成音频")
                if chunk_index < len(chunks) - 1:
                    audios.append(
                        np.zeros(
                            int(self.kokoro_sample_rate * pause_seconds),
                            dtype=np.float32,
                        )
                    )
                    total_pause_seconds += pause_seconds

        if not audios:
            raise RuntimeError("Kokoro 没有生成音频")

        audio = np.concatenate(audios, axis=0)
        sf.write(str(wav_path), audio, self.kokoro_sample_rate)
        logger.info(
            "Kokoro 合成分块完成：chars=%s chunks=%s max_phonemes=%s "
            "pause_seconds=%.3f samples=%s",
            len(text),
            len(chunks),
            max(count for _, _, count in chunks),
            total_pause_seconds,
            audio.size,
        )
        print(
            "[tts] synthesis_ready "
            f"chars={len(text)} chunks={len(chunks)} "
            f"max_phonemes={max(count for _, _, count in chunks)} "
            f"pause_seconds={total_pause_seconds:.3f} samples={audio.size}",
            flush=True,
        )

    def guide_wav_path(self, index: int, text: str) -> Path:
        cache_identity = "|".join(
            (
                KOKORO_CACHE_VERSION,
                self.kokoro_repo_id,
                self.kokoro_voice,
                str(self.args.kokoro_speed),
                text,
            )
        )
        h = hashlib.sha1(cache_identity.encode("utf-8")).hexdigest()[:16]
        return self.guide_cache_dir / f"guide_{index:02d}_{h}.wav"

    def ensure_guide_wav(self, index: int, text: str) -> Path:
        wav_path = self.guide_wav_path(index, text)
        if wav_path.exists() and wav_path.stat().st_size > 1000:
            return wav_path

        print(f"正在合成第 {index + 1} 句导览音频...")
        self.synthesize_to_wav(text, wav_path)
        return wav_path

    def play_wav(self, wav_path: Path, interruptible: bool = False) -> bool:
        def process_changed(process):
            self.current_play_proc = process

        return play_audio_file(
            wav_path=wav_path,
            device=self.args.aplay_device,
            interruptible=interruptible,
            stop_event=self.stop_guide_event,
            running=lambda: self.running,
            process_changed=process_changed,
        )

    def create_recognition(self):
        return Recognition(
            model=self.args.stt_model,
            format="pcm",
            sample_rate=self.args.stt_sample_rate,
            language_hints=["zh", "en"],
            semantic_punctuation_enabled=False,
            max_sentence_silence=self.args.max_sentence_silence,
            punctuation_prediction_enabled=True,
            inverse_text_normalization_enabled=True,
            callback=STTCallback(self),
        )

    def start_stt_session(self):
        with self.recognition_lock:
            try:
                old = self.recognition
                if old is not None:
                    try:
                        old.stop()
                    except Exception:
                        pass

                self.recognition = self.create_recognition()
                self.recognition.start()
                self.stt_alive = True
                print("STT 会话已启动。")
            except Exception as e:
                self.stt_alive = False
                print("STT 启动失败:", repr(e))
                if self.running:
                    self.stt_restart_event.set()

    def mark_stt_stopped(self, reason=""):
        self.stt_alive = False
        if reason:
            print(reason)
        if self.running:
            self.stt_restart_event.set()

    def stt_supervisor_loop(self):
        while self.running:
            self.stt_restart_event.wait(timeout=0.5)

            if not self.running:
                break

            if not self.stt_restart_event.is_set():
                continue

            self.stt_restart_event.clear()

            while self.running:
                mode, _, _ = self.get_mode()
                if mode != "answer":
                    break
                time.sleep(0.1)

            if not self.running:
                break

            print("正在自动重启 STT 会话...")
            self.start_stt_session()

    def resample_to_stt_rate(self, data: bytes) -> bytes:
        if self.args.input_sample_rate == self.args.stt_sample_rate:
            return data

        converted, self.ratecv_state = audioop.ratecv(
            data,
            2,
            self.args.channels,
            self.args.input_sample_rate,
            self.args.stt_sample_rate,
            self.ratecv_state,
        )
        return converted

    def make_silence_for_stt(self) -> bytes:
        samples = int(self.args.stt_sample_rate * self.args.chunk_ms / 1000)
        return b"\x00\x00" * samples * self.args.channels

    def send_audio_safe(self, data: bytes):
        with self.recognition_lock:
            rec = self.recognition

        if rec is None or not self.stt_alive:
            return

        try:
            rec.send_audio_frame(data)
        except Exception as e:
            print("\nSTT 发送音频失败，将自动重启:", repr(e))
            self.mark_stt_stopped()

    def audio_loop(self):
        while self.running:
            try:
                if self.stream is None:
                    time.sleep(0.05)
                    continue

                raw = self.stream.read(
                    self.frames_per_buffer,
                    exception_on_overflow=False,
                )

                mode, _, _ = self.get_mode()

                if mode == "answer":
                    self.send_audio_safe(self.make_silence_for_stt())
                    continue

                data = self.resample_to_stt_rate(raw)
                self.send_audio_safe(data)

            except Exception as e:
                if self.running:
                    print("\n读取麦克风失败:", repr(e))
                if self.running and getattr(self.args, "alsa_input_device", ""):
                    self.reopen_alsa_input()
                time.sleep(0.5)

    def wait_for_complete_question(self, timeout: float = None, settle_time: float = None) -> str:
        parts = []
        timeout = self.args.question_timeout if timeout is None else timeout
        settle_time = self.args.question_settle_time if settle_time is None else settle_time

        for x in self.pop_pending_question_parts():
            x = self.strip_guide_echo(x)
            if x and len(normalize_text(x)) >= 2:
                parts.append(x)

        start = time.time()
        last_part_time = time.time() if parts else None

        while self.running and time.time() - start < timeout:
            if parts and last_part_time is not None:
                if time.time() - last_part_time >= settle_time:
                    break

            try:
                item = self.question_queue.get(timeout=0.08)
            except queue.Empty:
                continue

            item = item.strip()
            if not item:
                continue

            if self.contains_wake_word(item):
                item = self.remove_wake_word(item)

            item = self.strip_guide_echo(item)

            if item and len(normalize_text(item)) >= 2:
                parts.append(item)
                last_part_time = time.time()

        if not parts:
            return ""

        cleaned = []

        for item in parts:
            n_item = normalize_text(item)
            if not n_item:
                continue

            duplicate = False
            for old in list(cleaned):
                n_old = normalize_text(old)

                if n_item == n_old:
                    duplicate = True
                    break

                if n_item in n_old:
                    duplicate = True
                    break

                if n_old in n_item:
                    cleaned.remove(old)
                    break

            if not duplicate:
                cleaned.append(item)

        if not cleaned:
            return ""

        question = "，".join(cleaned)
        return question.strip("，。！？!?、,. ")

    def ask_qwen(self, question: str) -> str:
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        now = time.localtime()
        today = time.strftime("%Y年%m月%d日", now) + " " + weekdays[now.tm_wday]
        short_question = f"当前日期是{today}。请只用一句话简短回答：{question}"
        self.chat_messages.append({
            "role": "user",
            "content": short_question,
        })

        system_msg = self.chat_messages[:1]
        history = self.chat_messages[1:]
        if len(history) > 20:
            history = history[-20:]
        self.chat_messages = system_msg + history

        print("Qwen:", end="", flush=True)

        extra_body = {}
        if self.args.enable_search:
            extra_body = {
                "enable_search": True,
                "search_options": {
                    "forced_search": True,
                    "search_strategy": "turbo",
                },
            }

        chunks = []

        response = self.qwen_client.chat.completions.create(
            model=self.args.qwen_model,
            messages=self.chat_messages,
            temperature=self.args.temperature,
            max_tokens=self.args.max_qwen_tokens,
            stream=True,
            extra_body=extra_body,
        )

        for chunk in response:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            part = getattr(delta, "content", None)

            if part:
                print(part, end="", flush=True)
                chunks.append(part)

        print()

        answer = "".join(chunks).strip()
        if not answer:
            answer = "这个问题我暂时没有回答出来。"

        self.chat_messages.append({
            "role": "assistant",
            "content": answer,
        })

        return answer

    def speak_answer(self, answer: str):
        answer = clean_tts_text(answer)

        if not answer:
            return

        if len(answer) > self.args.max_speak_chars:
            answer = answer[:self.args.max_speak_chars] + "。"

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
            dir=str(self.runtime_dir),
        ) as f:
            wav_path = Path(f.name)

        try:
            print("正在合成回答语音...")
            t0 = time.perf_counter()
            self.synthesize_to_wav(answer, wav_path)
            t1 = time.perf_counter()
            print(f"Kokoro 回答合成耗时: {(t1 - t0) * 1000:.2f} ms")

            self.play_wav(wav_path, interruptible=False)

        finally:
            try:
                wav_path.unlink()
            except Exception:
                pass

    def synth_answer_sentence(self, sentence: str, index: int):
        sentence = clean_tts_text(sentence)
        if not sentence:
            return None

        if len(sentence) > self.args.max_speak_chars:
            sentence = sentence[:self.args.max_speak_chars] + "。"

        with tempfile.NamedTemporaryFile(
            suffix=f"_stream_answer_{index}.wav",
            delete=False,
            dir=str(self.runtime_dir),
        ) as f:
            wav_path = Path(f.name)

        print(f"\n[answer:{index}] {sentence}", flush=True)
        t0 = time.perf_counter()
        self.synthesize_to_wav(sentence, wav_path)
        t1 = time.perf_counter()
        print(f"[answer:{index}] Kokoro 合成耗时: {(t1 - t0) * 1000:.2f} ms", flush=True)
        return wav_path

    def play_answer_wav(self, wav_path: Path):
        try:
            self.play_wav(wav_path, interruptible=False)
        finally:
            try:
                wav_path.unlink()
            except Exception:
                pass

    def speak_one_answer_sentence(self, sentence: str, index: int = 1):
        wav_path = self.synth_answer_sentence(sentence, index)
        if wav_path is not None:
            self.play_answer_wav(wav_path)

    def start_answer_pipeline(self):
        synth_queue = queue.Queue()
        audio_queue = queue.Queue()

        def synth_worker():
            while True:
                item = synth_queue.get()
                try:
                    if item is None:
                        audio_queue.put(None)
                        return
                    index, sentence = item
                    wav_path = self.synth_answer_sentence(sentence, index)
                    if wav_path is not None:
                        audio_queue.put((index, wav_path))
                except Exception as exc:
                    print("\n[voice] 回答合成出错:", repr(exc), flush=True)
                finally:
                    synth_queue.task_done()

        def play_worker():
            while True:
                item = audio_queue.get()
                try:
                    if item is None:
                        return
                    _, wav_path = item
                    self.play_answer_wav(wav_path)
                except Exception as exc:
                    print("\n[voice] 回答播放出错:", repr(exc), flush=True)
                finally:
                    audio_queue.task_done()

        synth_thread = threading.Thread(target=synth_worker, daemon=True)
        play_thread = threading.Thread(target=play_worker, daemon=True)
        synth_thread.start()
        play_thread.start()
        return synth_queue, audio_queue, synth_thread, play_thread

    def stream_qwen_and_speak_by_sentence(self, question: str):
        question = question.strip()
        if not question:
            return

        max_sentences = max(1, min(self.args.max_answer_sentences, 3))
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        now = time.localtime()
        today = time.strftime("%Y年%m月%d日", now) + " " + weekdays[now.tm_wday]
        prompt = (
            f"当前日期是{today}。请直接回答用户问题。"
            f"简单问题只用一句话；确需解释时最多{max_sentences}句话。"
            "每句话尽量短，不要 Markdown，不要列表符号。"
            f"用户问题：{question}"
        )

        self.chat_messages.append({"role": "user", "content": prompt})
        system_msg = self.chat_messages[:1]
        history = self.chat_messages[1:]
        if len(history) > 12:
            history = history[-12:]
        messages = system_msg + history
        self.chat_messages = messages

        extra_body = {}
        if self.args.enable_search:
            extra_body = {
                "enable_search": True,
                "search_options": {
                    "forced_search": True,
                    "search_strategy": "turbo",
                },
            }

        print()
        print("用户问题:", question, flush=True)
        print("Qwen(stream):", end="", flush=True)

        answer_parts = []
        pending = ""
        spoken = 0
        synth_queue, audio_queue, synth_thread, play_thread = self.start_answer_pipeline()

        try:
            stream = self.qwen_client.chat.completions.create(
                model=self.args.qwen_model,
                messages=messages,
                temperature=self.args.temperature,
                max_tokens=self.args.max_qwen_tokens,
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

                print(piece, end="", flush=True)
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
                    if tail[-1] not in "。！？!?；;":
                        tail += "。"
                    synth_queue.put((spoken, tail))
        finally:
            synth_queue.put(None)
            synth_queue.join()
            audio_queue.join()
            synth_thread.join(timeout=1.0)
            play_thread.join(timeout=1.0)

        final_answer = "".join(answer_parts).strip()
        if final_answer:
            self.chat_messages.append({"role": "assistant", "content": final_answer})

        print("\n[voice] 回答播放完成，恢复导览。", flush=True)
    def play_guide_sentence(self, index: int, sentence: str) -> bool:
        self.clear_question_queue()
        self.pop_pending_question_parts()
        self.stop_guide_event.clear()

        wav_path = self.ensure_guide_wav(index, sentence)

        self.set_mode("guide", sentence, index)

        print()
        print("=" * 70)
        print(f"开始播放第 {index + 1}/{len(TOUR_SENTENCES)} 句导览。")
        print("导览:", sentence)
        print("提示: 播放中说“小智 + 你的问题”即可打断。")
        print("=" * 70)

        interrupted = self.play_wav(wav_path, interruptible=True)

        if interrupted:
            print("\n检测到“小智”，已暂停导览。")
            self.set_mode("interrupted", sentence, index)
            return True

        print(f"第 {index + 1} 句导览播放完成。")
        return False

    def answer_question_once(self, question: str):
        question = question.strip()
        if not question:
            return

        print()
        print("用户问题:", question)

        self.set_mode("answer")
        try:
            self.stream_qwen_and_speak_by_sentence(question)
        except Exception as e:
            print("回答流程出错:", repr(e))
            try:
                self.speak_one_answer_sentence("抱歉，刚才回答时出现了问题。")
            except Exception:
                pass

    def guide_loop(self):
        idx = 0

        while self.running:
            if idx >= len(TOUR_SENTENCES):
                if self.args.loop_guide:
                    idx = 0
                    continue

                print()
                print("导览已全部播放完毕。现在进入普通问答模式。")
                self.set_mode("idle")

                while self.running:
                    try:
                        question = self.question_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    question = self.strip_guide_echo(question)
                    if question:
                        self.answer_question_once(question)
                        self.set_mode("idle")

                break

            sentence = TOUR_SENTENCES[idx]
            interrupted = self.play_guide_sentence(idx, sentence)

            if interrupted:
                question = self.wait_for_complete_question()

                if question:
                    self.answer_question_once(question)
                else:
                    print("没有收到完整问题，准备恢复导览。")

                print("回答结束，准备从被打断的当前句开头恢复导览。")
                self.stop_guide_event.clear()
                self.set_mode("idle")
                time.sleep(self.args.resume_delay)

                continue

            idx += 1
            self.set_mode("idle")
            time.sleep(self.args.sentence_gap)

    def run(self):
        self.init_audio()
        self.preload_kokoro()

        threading.Thread(target=self.stt_supervisor_loop, daemon=True).start()
        threading.Thread(target=self.audio_loop, daemon=True).start()

        self.start_stt_session()

        print()
        print("系统已启动。即将开始导览。")
        print("导览播放中请说：“小智，唐太宗是谁？” 这样的形式进行打断。")
        print("按 Ctrl+C 退出。")
        print()

        time.sleep(1.0)
        self.guide_loop()

    def stop(self):
        self.running = False

        try:
            if self.current_play_proc is not None:
                self.current_play_proc.terminate()
        except Exception:
            pass

        with self.recognition_lock:
            if self.recognition is not None:
                try:
                    self.recognition.stop()
                except Exception:
                    pass
                self.recognition = None

        self.close_audio()


class STTCallback(RecognitionCallback):
    def __init__(self, app: TourGuideAssistant):
        self.app = app

    def on_open(self):
        print("已连接阿里实时语音识别服务。")

    def on_close(self):
        print("STT 连接已关闭。")
        self.app.mark_stt_stopped()

    def on_complete(self):
        print("STT 识别任务完成，但程序不会退出，会自动重启 STT。")
        self.app.mark_stt_stopped()

    def on_error(self, message):
        print("STT 识别出错:", getattr(message, "message", message))
        self.app.mark_stt_stopped()

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()

        if not sentence or "text" not in sentence:
            return

        text = sentence.get("text", "").strip()
        if not text:
            return

        mode, _, _ = self.app.get_mode()

        if mode == "answer":
            return

        is_end = RecognitionResult.is_sentence_end(sentence)

        if not is_end:
            if mode == "guide":
                if self.app.contains_wake_word(text):
                    print("\r检测到唤醒词“小智”，正在暂停导览: " + text, end="", flush=True)
                    self.app.request_stop_guide(text)

            elif mode == "interrupted":
                print("\r等待你说完问题: " + text, end="", flush=True)
                if self.app.contains_wake_word(text):
                    self.app.request_stop_guide(text)

            elif mode == "idle":
                if self.app.contains_wake_word(text):
                    print("\r检测到唤醒词“小智”，请继续说问题: " + text, end="", flush=True)
                else:
                    print("\r正在听你说话: " + text, end="", flush=True)

            return

        if mode == "guide":
            if self.app.contains_wake_word(text):
                self.app.request_stop_guide(text)
                question = self.app.remove_wake_word(text)
                question = self.app.strip_guide_echo(question)
                if question:
                    self.app.question_queue.put(question)
            return

        if mode == "interrupted":
            if self.app.contains_wake_word(text):
                question = self.app.remove_wake_word(text)
            else:
                question = text

            question = self.app.strip_guide_echo(question)
            if question:
                print("\n识别到问题:", question)
                self.app.question_queue.put(question)
            return

        if mode == "idle":
            print("\n识别完成:", text)
            self.app.question_queue.put(text)
            return


def parse_args():
    parser = argparse.ArgumentParser(
        description="小智导览播报 + 唤醒打断 + Qwen 问答 + Kokoro 本地 TTS + 回答后恢复当前句"
    )

    parser.add_argument("--device-index", type=int, default=None)
    parser.add_argument("--alsa-input-device", default="")
    parser.add_argument("--alsa-input-channels", type=int, default=2)
    parser.add_argument("--input-sample-rate", type=int, default=48000)
    parser.add_argument("--stt-sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--chunk-ms", type=int, default=100)

    parser.add_argument("--stt-model", default="paraformer-realtime-v2")
    parser.add_argument("--qwen-model", default="qwen-plus")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-qwen-tokens", type=int, default=160)
    parser.add_argument("--max-answer-sentences", type=int, default=3)
    parser.add_argument("--max-speak-chars", type=int, default=130)
    parser.add_argument("--enable-search", action="store_true")

    parser.add_argument("--kokoro-repo-id", default=DEFAULT_KOKORO_REPO_ID)
    parser.add_argument("--kokoro-voice", default=DEFAULT_KOKORO_VOICE)
    parser.add_argument("--kokoro-speed", type=float, default=1.0)

    parser.add_argument("--max-sentence-silence", type=int, default=700)
    parser.add_argument("--question-settle-time", type=float, default=0.55)
    parser.add_argument("--question-timeout", type=float, default=4.0)

    parser.add_argument("--sentence-gap", type=float, default=0.4)
    parser.add_argument("--resume-delay", type=float, default=0.5)
    parser.add_argument("--loop-guide", action="store_true")

    parser.add_argument("--aplay-device", default=None)
    parser.add_argument("--base-dir", default="/mnt/ssd/pc/projects/voice")

    return parser.parse_args()


def main():
    args = parse_args()
    app = TourGuideAssistant(args)

    def handle_sigint(sig, frame):
        print("\n收到 Ctrl+C，正在退出...")
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        app.run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
