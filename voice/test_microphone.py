#!/usr/bin/env python3
import argparse
import audioop
import time

import pyaudio


def setup_dji_mic_source():
    import shutil
    import subprocess
    source = "alsa_input.usb-DJI_Technology_Co.__Ltd._Wireless_Mic_Rx_XSP12345678B-01.analog-stereo"
    if shutil.which("pactl") is None:
        return
    try:
        sources = subprocess.run(["pactl", "list", "short", "sources"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if source not in sources.stdout:
            return
        subprocess.run(["pactl", "set-default-source", source], check=False)
        subprocess.run(["pactl", "set-source-mute", source, "0"], check=False)
        subprocess.run(["pactl", "set-source-volume", source, "100%"], check=False)
        print("[mic] using DJI Mic Mini source:", source)
    except Exception as exc:
        print("[mic] failed to setup DJI source:", repr(exc))


def list_devices():
    audio = pyaudio.PyAudio()
    try:
        print("输入设备列表：")
        for i in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                print(
                    f"[{i}] {info.get('name')} | "
                    f"channels={info.get('maxInputChannels')} | "
                    f"default_rate={info.get('defaultSampleRate')}"
                )
    finally:
        audio.terminate()


def test_device(device_index, sample_rate, channels, seconds):
    audio = pyaudio.PyAudio()
    stream = None
    try:
        info = audio.get_device_info_by_index(device_index)
        print("当前输入设备:", info.get("name"))
        print("默认采样率:", info.get("defaultSampleRate"))
        print("测试采样率:", sample_rate)
        print("通道数:", channels)
        print("开始测试，请对着麦克风说话...")

        chunk = int(sample_rate * 0.1)
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=chunk,
        )

        end = time.time() + seconds
        while time.time() < end:
            data = stream.read(chunk, exception_on_overflow=False)
            rms = audioop.rms(data, 2)
            bar = "#" * min(60, rms // 300)
            print(f"RMS={rms:5d} {bar}", flush=True)
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()


def main():
    setup_dji_mic_source()
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--device-index", type=int, default=25)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()

    if args.list:
        list_devices()
    else:
        test_device(args.device_index, args.sample_rate, args.channels, args.seconds)


if __name__ == "__main__":
    main()
