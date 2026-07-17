#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import audioop
import os
import re
import signal
import subprocess
import time

import dashscope
import numpy as np
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult


def clean_api_key(key):
    if not key:
        return key
    return re.sub(r"[\x00-\x20\x7f]+", "", key)


class PrintCallback(RecognitionCallback):
    def on_open(self):
        print("[stt] 已连接实时语音识别 API")

    def on_close(self):
        print("\n[stt] 连接已关闭")

    def on_complete(self):
        print("\n[stt] 识别任务完成")

    def on_error(self, message):
        print("\n[stt] 识别出错:", getattr(message, "message", message))

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()
        if not sentence:
            return
        text = (sentence.get("text") or "").strip()
        if not text:
            return
        if RecognitionResult.is_sentence_end(sentence):
            print(f"\n[final] {text}")
        else:
            print(f"\r[partial] {text}", end="", flush=True)


def run_cmd(args):
    try:
        return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5).stdout
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def list_devices():
    print("ALSA 录音设备：")
    print(run_cmd(["arecord", "-l"]))
    print("PulseAudio sources：")
    print(run_cmd(["pactl", "list", "short", "sources"]))


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


def start_arecord(args):
    cmd = [
        "arecord",
        "-q",
        "-D",
        args.alsa_device,
        "-f",
        "S16_LE",
        "-c",
        str(args.alsa_channels),
        "-r",
        str(args.input_sample_rate),
        "-t",
        "raw",
    ]
    print("[mic] arecord:", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)


def stop_arecord(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


def run_stt(args):
    api_key = clean_api_key(os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY"))
    if not api_key:
        raise RuntimeError("没有找到 DASHSCOPE_API_KEY，请先 source ~/.bashrc 或设置环境变量")
    dashscope.api_key = api_key

    bytes_per_frame = args.alsa_channels * 2
    chunk_frames = max(1, int(args.input_sample_rate * args.chunk_ms / 1000))
    chunk_bytes = chunk_frames * bytes_per_frame

    proc = None
    recognition = None
    rate_state = None
    try:
        proc = start_arecord(args)
        print("[mic] 当前输入设备:", args.alsa_device)
        print("[mic] 打开采样率:", args.input_sample_rate)
        print("[mic] ALSA 通道数:", args.alsa_channels)
        print("[stt] 发送采样率:", args.stt_sample_rate)
        print("[stt] 模型:", args.stt_model)

        recognition = Recognition(
            model=args.stt_model,
            format="pcm",
            sample_rate=args.stt_sample_rate,
            language_hints=["zh", "en"],
            semantic_punctuation_enabled=False,
            max_sentence_silence=args.max_sentence_silence,
            punctuation_prediction_enabled=True,
            inverse_text_normalization_enabled=True,
            callback=PrintCallback(),
        )
        recognition.start()
        print("[test] 开始说话，识别结果会显示在终端。Ctrl+C 退出。")

        start = time.time()
        last_rms_time = 0.0
        while args.seconds <= 0 or time.time() - start < args.seconds:
            raw = proc.stdout.read(chunk_bytes)
            if not raw:
                err = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
                raise RuntimeError(f"arecord 没有输出音频，退出码={proc.poll()} {err}")

            mono = best_mono_channel(raw, args.alsa_channels)
            if not mono:
                continue

            rms = audioop.rms(mono, 2)
            if time.time() - last_rms_time > 1.0:
                print(f"\n[mic] RMS={rms}")
                last_rms_time = time.time()

            if args.input_sample_rate != args.stt_sample_rate:
                mono, rate_state = audioop.ratecv(
                    mono, 2, 1, args.input_sample_rate, args.stt_sample_rate, rate_state
                )
            recognition.send_audio_frame(mono)
    finally:
        if recognition is not None:
            try:
                recognition.stop()
            except Exception:
                pass
        stop_arecord(proc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="列出 ALSA/Pulse 输入设备")
    parser.add_argument("--alsa-device", default="plughw:CARD=Rx,DEV=0", help="DJI Mic Mini ALSA 设备")
    parser.add_argument("--alsa-channels", type=int, default=2, help="DJI 接收器通常是 2 通道")
    parser.add_argument("--input-sample-rate", type=int, default=48000)
    parser.add_argument("--stt-sample-rate", type=int, default=16000)
    parser.add_argument("--stt-model", default="paraformer-realtime-v2")
    parser.add_argument("--max-sentence-silence", type=int, default=700)
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--device-index", type=int, default=None, help="兼容旧命令；新版本不使用 PyAudio index")
    parser.add_argument("--channels", type=int, default=None, help="兼容旧命令；请改用 --alsa-channels")
    args = parser.parse_args()

    if args.list:
        list_devices()
        return

    run_stt(args)


if __name__ == "__main__":
    main()
