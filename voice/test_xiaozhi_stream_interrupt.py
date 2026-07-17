#!/usr/bin/env python3
import argparse
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from tour_guide_xiaozhi_interrupt_resume import TourGuideAssistant, clean_tts_text


DEFAULT_GUIDE_TEXT = [
    "这里是小智语音打断与流式回答测试。播放导览时，请用小智开头提出问题。",
    "当我听到小智后，会暂停导览，先推理并把回答通过音响播出，回答结束后再继续导览。",
    "这个脚本只测试语音播报、唤醒打断、流式问答和恢复播报，不会调用导航节点和手臂节点。",
]


def resolve_speaker_device(speaker_name, explicit_device):
    if explicit_device:
        return explicit_device
    if not speaker_name:
        return None

    try:
        result = subprocess.run(
            ["aplay", "-l"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None

    for line in result.stdout.splitlines():
        if speaker_name not in line:
            continue
        card = re.search(r"card\s+(\d+):", line)
        device = re.search(r"device\s+(\d+):", line)
        if card and device:
            resolved = f"plughw:{card.group(1)},{device.group(1)}"
            print(f"[voice] matched speaker '{speaker_name}': {resolved}", flush=True)
            return resolved
    return None


def load_guide_sentences(path):
    if not path:
        return list(DEFAULT_GUIDE_TEXT)

    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return list(DEFAULT_GUIDE_TEXT)

    parts = [x.strip() for x in re.split(r"(?<=[。！？!?])\s*|\n+", text) if x.strip()]
    return parts or [text]


def split_complete_sentences(buffer):
    sentences = []
    start = 0
    for match in re.finditer(r"[。！？!?；;]", buffer):
        end = match.end()
        sentence = buffer[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
    return sentences, buffer[start:].strip()


def speak_one_answer_sentence(app, sentence, index):
    sentence = clean_tts_text(sentence)
    if not sentence:
        return

    with tempfile.NamedTemporaryFile(
        suffix=f"_stream_answer_{index}.wav",
        delete=False,
        dir=str(app.runtime_dir),
    ) as f:
        wav_path = Path(f.name)

    try:
        print(f"\n[answer:{index}] {sentence}", flush=True)
        t0 = time.perf_counter()
        app.synthesize_to_wav(sentence, wav_path)
        t1 = time.perf_counter()
        print(f"[answer:{index}] Kokoro 合成耗时: {(t1 - t0) * 1000:.2f} ms", flush=True)
        app.play_wav(wav_path, interruptible=False)
    finally:
        try:
            wav_path.unlink()
        except Exception:
            pass


def stream_qwen_and_speak_by_sentence(app, question, max_sentences):
    question = question.strip()
    if not question:
        return

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    now = time.localtime()
    today = time.strftime("%Y年%m月%d日", now) + " " + weekdays[now.tm_wday]
    prompt = (
        f"当前日期是{today}。请用中文口语化回答用户问题，最多{max_sentences}句话，"
        "不要使用Markdown，不要列项目符号。用户问题："
        f"{question}"
    )

    app.chat_messages.append({"role": "user", "content": prompt})
    system_msg = app.chat_messages[:1]
    history = app.chat_messages[1:]
    if len(history) > 20:
        history = history[-20:]
    messages = system_msg + history
    app.chat_messages = messages

    extra_body = {}
    if app.args.enable_search:
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

    stream = app.qwen_client.chat.completions.create(
        model=app.args.qwen_model,
        messages=messages,
        temperature=app.args.temperature,
        max_tokens=app.args.max_qwen_tokens,
        stream=True,
        extra_body=extra_body or None,
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
            speak_one_answer_sentence(app, sentence, spoken)

    if spoken < max_sentences:
        tail = pending.strip()
        if tail:
            spoken += 1
            if tail[-1] not in "。！？!?":
                tail += "。"
            speak_one_answer_sentence(app, tail, spoken)

    final_answer = "".join(answer_parts).strip()
    if final_answer:
        app.chat_messages.append({"role": "assistant", "content": final_answer})
    print("\n[voice] 回答播放完成，恢复导览。", flush=True)


def answer_then_resume(app, question, args):
    app.set_mode("answer")
    try:
        stream_qwen_and_speak_by_sentence(app, question, args.max_answer_sentences)
    except Exception as exc:
        print("\n[voice] 流式回答出错:", repr(exc), flush=True)
        try:
            speak_one_answer_sentence(app, "抱歉，刚才回答时出现了问题。", 1)
        except Exception:
            pass
    finally:
        app.stop_guide_event.clear()
        app.set_mode("idle")


def run_loop(app, guide_sentences, args):
    index = 0
    while app.running:
        if index >= len(guide_sentences):
            if args.loop_guide:
                index = 0
            else:
                print("[voice] 导览测试播报结束。", flush=True)
                break

        sentence = guide_sentences[index]
        interrupted = app.play_guide_sentence(index, sentence)
        if not interrupted:
            app.set_mode("idle")
            index += 1
            time.sleep(args.sentence_gap)
            continue

        question = app.wait_for_complete_question(
            timeout=args.question_timeout,
            settle_time=args.question_settle_time,
        )
        if question:
            answer_then_resume(app, question, args)
        else:
            print("[voice] 没有收到完整问题，恢复当前导览句。", flush=True)
            app.stop_guide_event.clear()
            app.set_mode("idle")

        time.sleep(args.resume_delay)


def build_parser():
    parser = argparse.ArgumentParser(description="只测试小智语音播报、打断、流式问答和恢复播报")
    parser.add_argument("--guide-file", default="")
    parser.add_argument("--loop-guide", action="store_true")
    parser.add_argument("--speaker-name", default="REDMI Speaker 2-6002")
    parser.add_argument("--aplay-device", default=None)
    parser.add_argument("--device-index", type=int, default=25)
    parser.add_argument("--alsa-input-device", default="plughw:CARD=Rx,DEV=0")
    parser.add_argument("--alsa-input-channels", type=int, default=2)
    parser.add_argument("--input-sample-rate", type=int, default=48000)
    parser.add_argument("--stt-sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument("--stt-model", default="paraformer-realtime-v2")
    parser.add_argument("--qwen-model", default="qwen-plus")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-qwen-tokens", type=int, default=260)
    parser.add_argument("--max-answer-sentences", type=int, default=4)
    parser.add_argument("--max-speak-chars", type=int, default=220)
    parser.add_argument("--enable-search", action="store_true")
    parser.add_argument("--kokoro-repo-id", default="hexgrad/Kokoro-82M-v1.1-zh")
    parser.add_argument("--kokoro-voice", default="zf_001")
    parser.add_argument("--kokoro-speed", type=float, default=1.0)
    parser.add_argument("--max-sentence-silence", type=int, default=700)
    parser.add_argument("--question-settle-time", type=float, default=0.55)
    parser.add_argument("--question-timeout", type=float, default=4.0)
    parser.add_argument("--sentence-gap", type=float, default=0.4)
    parser.add_argument("--resume-delay", type=float, default=0.5)
    parser.add_argument("--base-dir", default="/mnt/ssd/pc/projects/voice")
    return parser


def main():
    args = build_parser().parse_args()
    args.aplay_device = resolve_speaker_device(args.speaker_name, args.aplay_device)

    app = TourGuideAssistant(args)

    def stop(_sig, _frame):
        print("\n[voice] 收到退出信号，正在停止。", flush=True)
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        app.init_audio()
        app.preload_kokoro()
        app.start_stt_session()
        app.set_mode("idle")
        guide_sentences = load_guide_sentences(args.guide_file)
        print("[voice] 测试开始：播报中说“小智 + 问题”会暂停导览并流式回答。", flush=True)
        run_loop(app, guide_sentences, args)
    finally:
        app.stop()


if __name__ == "__main__":
    main()
