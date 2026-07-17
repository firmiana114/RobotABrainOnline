#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pyaudio

p = pyaudio.PyAudio()

print("可用输入设备：")
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    name = info.get("name", "")
    max_input = info.get("maxInputChannels", 0)
    default_rate = info.get("defaultSampleRate", 0)

    if max_input > 0:
        print(f"[{i}] {name} | input_channels={max_input} | default_rate={default_rate}")

p.terminate()
