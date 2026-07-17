#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import numpy as np
import soundfile as sf
import torch
from kokoro import KModel, KPipeline

REPO_ID = "hexgrad/Kokoro-82M-v1.1-zh"
SAMPLE_RATE = 24000
VOICE = "zf_001"

device = "cuda" if torch.cuda.is_available() else "cpu"
print("使用设备:", device)
print("HF_HOME:", __import__("os").environ.get("HF_HOME"))

print("正在加载 Kokoro 中文模型，第一次运行会自动下载模型到 SSD 的 HF_HOME...")
t0 = time.perf_counter()

model = KModel(repo_id=REPO_ID).to(device).eval()

en_pipeline = KPipeline(lang_code="a", repo_id=REPO_ID, model=False)

def en_callable(text):
    return next(en_pipeline(text)).phonemes

zh_pipeline = KPipeline(
    lang_code="z",
    repo_id=REPO_ID,
    model=model,
    en_callable=en_callable,
)

t1 = time.perf_counter()
print(f"模型加载耗时: {(t1 - t0) * 1000:.2f} ms")

text = "这里是清华大学无锡应用技术研究院。"

print("开始合成:", text)
t2 = time.perf_counter()

with torch.inference_mode():
    generator = zh_pipeline(text, voice=VOICE, speed=1.0)
    result = next(generator)

if hasattr(result, "audio"):
    audio = result.audio
else:
    audio = result[2]

if isinstance(audio, torch.Tensor):
    audio = audio.detach().cpu().numpy()

audio = np.asarray(audio, dtype=np.float32)

t3 = time.perf_counter()

sf.write("/mnt/ssd/pc/projects/voice/kokoro_test.wav", audio, SAMPLE_RATE)

print(f"合成耗时: {(t3 - t2) * 1000:.2f} ms")
print("已保存: /mnt/ssd/pc/projects/voice/kokoro_test.wav")
