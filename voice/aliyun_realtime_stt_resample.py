#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import audioop
import argparse

import dashscope
import pyaudio
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult


def clean_api_key(key):
    if not key:
        return key
    return re.sub(r"[\x00-\x20\x7f]+", "", key)


class Callback(RecognitionCallback):
    def on_open(self):
        print("已连接阿里实时语音识别服务，开始说话。按 Ctrl+C 停止。")

    def on_close(self):
        print("\n连接已关闭。")

    def on_complete(self):
        print("\nSTT 识别任务完成。")

    def on_error(self, message):
        print("\n识别出错：", getattr(message, "message", message))

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()
        if not sentence or "text" not in sentence:
            return

        text = sentence["text"].strip()
        if not text:
            return

        if RecognitionResult.is_sentence_end(sentence):
            print("\n最终识别：", text)
        else:
            print("\r临时识别：" + text, end="", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-index", type=int, default=None)
    parser.add_argument("--input-sample-rate", type=int, default=44100)
    parser.add_argument("--stt-sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument("--max-sentence-silence", type=int, default=700)
    args = parser.parse_args()

    api_key = clean_api_key(os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY"))
    if not api_key:
        raise RuntimeError("没有找到 DASHSCOPE_API_KEY，请先 export DASHSCOPE_API_KEY=你的key")

    dashscope.api_key = api_key

    pa = pyaudio.PyAudio()

    if args.device_index is not None:
        info = pa.get_device_info_by_index(args.device_index)
    else:
        info = pa.get_default_input_device_info()

    print("当前输入设备：", info.get("name"))
    print("输入通道数：", info.get("maxInputChannels"))
    print("设备默认采样率：", info.get("defaultSampleRate"))
    print("麦克风打开采样率：", args.input_sample_rate)
    print("发送给 STT 采样率：", args.stt_sample_rate)

    frames_per_buffer = int(args.input_sample_rate * args.chunk_ms / 1000)
    ratecv_state = None

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=args.channels,
        rate=args.input_sample_rate,
        input=True,
        input_device_index=args.device_index,
        frames_per_buffer=frames_per_buffer,
    )

    recognition = Recognition(
        model="paraformer-realtime-v2",
        format="pcm",
        sample_rate=args.stt_sample_rate,
        language_hints=["zh", "en"],
        semantic_punctuation_enabled=False,
        punctuation_prediction_enabled=True,
        inverse_text_normalization_enabled=True,
        max_sentence_silence=args.max_sentence_silence,
        callback=Callback(),
    )

    recognition.start()

    try:
        while True:
            raw = stream.read(frames_per_buffer, exception_on_overflow=False)

            if args.input_sample_rate != args.stt_sample_rate:
                converted, ratecv_state = audioop.ratecv(
                    raw,
                    2,
                    args.channels,
                    args.input_sample_rate,
                    args.stt_sample_rate,
                    ratecv_state,
                )
            else:
                converted = raw

            recognition.send_audio_frame(converted)

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在停止...")

    finally:
        try:
            recognition.stop()
        except Exception:
            pass

        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass

        pa.terminate()


if __name__ == "__main__":
    main()
