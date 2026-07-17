#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import queue
import audioop
import signal
import argparse
import threading
import subprocess
import tempfile

import numpy as np
import soundfile as sf
import torch
import dashscope
import pyaudio

from openai import OpenAI
from kokoro import KModel, KPipeline
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from voice_console.config import DEFAULT_KOKORO_REPO_ID, DEFAULT_KOKORO_VOICE


def clean_api_key(key):
    if not key:
        return key
    return re.sub(r"[\x00-\x20\x7f]+", "", key)


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


class VoiceAssistant:
    def __init__(self, args):
        self.args = args

        api_key = clean_api_key(
            os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError("没有找到 DASHSCOPE_API_KEY，请先 export DASHSCOPE_API_KEY=你的key")

        dashscope.api_key = api_key

        self.qwen_client = OpenAI(
            api_key=api_key,
            base_url=os.getenv(
                "OPENAI_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
        )

        self.running = True
        self.speaking = False
        self.busy_answering = False

        self.audio = None
        self.stream = None
        self.ratecv_state = None
        self.frames_per_buffer = int(args.input_sample_rate * args.chunk_ms / 1000)

        self.recognition = None
        self.recognition_lock = threading.Lock()
        self.stt_alive = False
        self.stt_restart_event = threading.Event()

        self.question_queue = queue.Queue()

        self.chat_messages = [
            {
                "role": "system",
                "content": (
                    "你是一个简洁、可靠的中文语音助手。"
                    "用户通过语音提问，你要直接回答。"
                    "回答尽量简短，先给核心答案，再根据需要补充一句。"
                    "不要使用Markdown。"
                ),
            }
        ]

        self.kokoro_ready = False
        self.kokoro_model = None
        self.kokoro_pipeline = None
        self.kokoro_en_pipeline = None
        self.kokoro_repo_id = args.kokoro_repo_id
        self.kokoro_voice = args.kokoro_voice
        self.kokoro_sample_rate = 24000
        self.kokoro_device = "cuda" if torch.cuda.is_available() else "cpu"

    def init_audio(self):
        self.audio = pyaudio.PyAudio()

        if self.args.device_index is not None:
            info = self.audio.get_device_info_by_index(self.args.device_index)
        else:
            info = self.audio.get_default_input_device_info()

        print("当前输入设备:", info.get("name"))
        print("设备默认采样率:", info.get("defaultSampleRate"))
        print("实际打开采样率:", self.args.input_sample_rate)
        print("发送给 STT 采样率:", self.args.stt_sample_rate)
        print("Qwen 模型:", self.args.qwen_model)
        print("联网搜索:", self.args.enable_search)
        print("Kokoro 设备:", self.kokoro_device)
        print("Kokoro voice:", self.kokoro_voice)
        print("播放设备:", self.args.aplay_device or "系统默认")
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

        print("正在启动时预加载 Kokoro-82M，避免第一次回答时卡住 STT...")
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

    def create_recognition(self):
        callback = STTCallback(self)

        return Recognition(
            model=self.args.stt_model,
            format="pcm",
            sample_rate=self.args.stt_sample_rate,
            language_hints=["zh", "en"],
            semantic_punctuation_enabled=False,
            max_sentence_silence=self.args.max_sentence_silence,
            punctuation_prediction_enabled=True,
            inverse_text_normalization_enabled=True,
            callback=callback,
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
                print("STT 会话已启动，正在等待你说话...")
            except Exception as e:
                self.stt_alive = False
                print("STT 启动失败:", repr(e))
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

            while self.running and self.speaking:
                time.sleep(0.1)

            if not self.running:
                break

            print("正在自动重启 STT 会话，继续等待下一个问题...")
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

    def make_silence_16k(self):
        silence_samples = int(self.args.stt_sample_rate * self.args.chunk_ms / 1000)
        return b"\x00\x00" * silence_samples * self.args.channels

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

                if self.speaking or self.busy_answering:
                    self.send_audio_safe(self.make_silence_16k())
                    continue

                data_16k = self.resample_to_stt_rate(raw)
                self.send_audio_safe(data_16k)

            except Exception as e:
                if self.running:
                    print("\n读取麦克风失败:", repr(e))
                time.sleep(0.2)

    def answer_worker_loop(self):
        while self.running:
            try:
                question = self.question_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            question = question.strip()
            if not question:
                continue

            if self.busy_answering:
                continue

            self.busy_answering = True

            try:
                print("\n你:", question)
                answer = self.ask_qwen(question)
                self.speak_text(answer)
                print("\n继续等待下一个问题。")

                if not self.stt_alive:
                    self.stt_restart_event.set()

            except Exception as e:
                print("回答流程出错:", repr(e))

            finally:
                self.busy_answering = False

    def ask_qwen(self, question: str) -> str:
        self.chat_messages.append({
            "role": "user",
            "content": question,
        })

        system_msg = self.chat_messages[:1]
        history = self.chat_messages[1:]
        if len(history) > 20:
            history = history[-20:]
        self.chat_messages = system_msg + history

        print("Qwen:", end="", flush=True)

        chunks = []

        extra_body = {}
        if self.args.enable_search:
            extra_body = {
                "enable_search": True,
                "search_options": {
                    "forced_search": True,
                    "search_strategy": "turbo"
                }
            }

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

        if answer:
            self.chat_messages.append({
                "role": "assistant",
                "content": answer,
            })

        return answer

    def extract_kokoro_audio(self, result):
        if hasattr(result, "audio"):
            audio = result.audio
        else:
            audio = result[2]

        if isinstance(audio, torch.Tensor):
            audio = audio.detach().cpu().numpy()

        return np.asarray(audio, dtype=np.float32)

    def speak_text(self, text: str):
        text = clean_tts_text(text)

        if not text:
            return

        if len(text) > self.args.max_speak_chars:
            text = text[:self.args.max_speak_chars] + "。"

        print("正在使用本地 Kokoro-82M TTS 合成并播放...")

        self.speaking = True

        try:
            t0 = time.perf_counter()

            with torch.inference_mode():
                result = next(
                    self.kokoro_pipeline(
                        text,
                        voice=self.kokoro_voice,
                        speed=self.args.kokoro_speed,
                    )
                )

            audio = self.extract_kokoro_audio(result)

            t1 = time.perf_counter()

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/mnt/ssd/pc/projects/voice") as f:
                wav_path = f.name

            sf.write(wav_path, audio, self.kokoro_sample_rate)

            print(f"Kokoro 本地 TTS 合成耗时: {(t1 - t0) * 1000:.2f} ms")

            cmd = ["aplay", "-q"]
            if self.args.aplay_device:
                cmd.extend(["-D", self.args.aplay_device])
            cmd.append(wav_path)

            subprocess.run(cmd, check=False)

            try:
                os.remove(wav_path)
            except Exception:
                pass

        finally:
            time.sleep(self.args.answer_cooldown)
            self.speaking = False
            print("已恢复麦克风监听。")

    def run(self):
        self.init_audio()
        self.preload_kokoro()

        threading.Thread(target=self.stt_supervisor_loop, daemon=True).start()
        threading.Thread(target=self.audio_loop, daemon=True).start()
        threading.Thread(target=self.answer_worker_loop, daemon=True).start()

        self.start_stt_session()

        print("系统已启动。说话提问即可，按 Ctrl+C 退出。")

        while self.running:
            time.sleep(0.5)

    def stop(self):
        self.running = False

        with self.recognition_lock:
            if self.recognition is not None:
                try:
                    self.recognition.stop()
                except Exception:
                    pass
                self.recognition = None

        self.close_audio()


class STTCallback(RecognitionCallback):
    def __init__(self, app: VoiceAssistant):
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
        if self.app.speaking or self.app.busy_answering:
            return

        sentence = result.get_sentence()

        if not sentence or "text" not in sentence:
            return

        text = sentence["text"].strip()

        if not text:
            return

        if not RecognitionResult.is_sentence_end(sentence):
            print("\r正在听你说话: " + text, end="", flush=True)
            return

        print("\n识别完成:", text)
        self.app.question_queue.put(text)


def parse_args():
    parser = argparse.ArgumentParser(
        description="在线 STT + 在线 Qwen + 本地 Kokoro TTS，支持 STT 自动重连，持续多轮对话"
    )

    parser.add_argument("--device-index", type=int, default=None)
    parser.add_argument("--input-sample-rate", type=int, default=48000)
    parser.add_argument("--stt-sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--chunk-ms", type=int, default=100)

    parser.add_argument("--stt-model", default="paraformer-realtime-v2")
    parser.add_argument("--qwen-model", default="qwen-plus")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-qwen-tokens", type=int, default=120)
    parser.add_argument("--max-speak-chars", type=int, default=100)
    parser.add_argument("--enable-search", action="store_true")

    parser.add_argument("--kokoro-repo-id", default=DEFAULT_KOKORO_REPO_ID)
    parser.add_argument("--kokoro-voice", default=DEFAULT_KOKORO_VOICE)
    parser.add_argument("--kokoro-speed", type=float, default=1.0)

    parser.add_argument("--max-sentence-silence", type=int, default=900)
    parser.add_argument("--answer-cooldown", type=float, default=0.6)
    parser.add_argument("--aplay-device", default=None)

    return parser.parse_args()


def main():
    args = parse_args()
    app = VoiceAssistant(args)

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
