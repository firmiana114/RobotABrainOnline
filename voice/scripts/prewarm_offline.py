"""Generate route audio only: no cloud, recording, playback or robot calls."""
from pathlib import Path
import json
from types import SimpleNamespace
import soundfile as sf
import numpy as np
import torch
from nav_route_then_speak import load_route, load_speech_segments, precache_route_audio
from tour_guide_xiaozhi_interrupt_resume import TourGuideAssistant
from voice_console.config import DEFAULT_KOKORO_REPO_ID, DEFAULT_KOKORO_VOICE
from voice_console.dialogue import DialogueStore

root = Path(__file__).resolve().parents[1]
route = root / "runtime/offline_route.json"
store = DialogueStore(root / "conf/dialogue_0.json", route,
                      root / "runtime/offline_qa_context.txt", Path("/home/unitree"))
store.sync_runtime_files()
args = SimpleNamespace(base_dir=str(root), input_sample_rate=48000, chunk_ms=100,
                       kokoro_repo_id=DEFAULT_KOKORO_REPO_ID,
                       kokoro_voice=DEFAULT_KOKORO_VOICE, kokoro_speed=1.0)
assert torch.cuda.is_available(), "CUDA unavailable"
app = TourGuideAssistant(args, offline_tts=True)
app.preload_kokoro()
# Force a fresh inference even if route audio is already cached.
probe = root / "runtime/offline_tts_probe.wav"
app.synthesize_to_wav("离线语音部署验证。", probe)
count, generated = precache_route_audio(SimpleNamespace(route_file=str(route)), app)
seconds = 0.0
for index, point in enumerate(load_route(route), 1):
    for segment_index, segment in enumerate(load_speech_segments(point, route.parent)):
        audio, rate = sf.read(app.guide_wav_path((index - 1) * 100 + segment_index, segment["text"]))
        assert audio.size > 0 and np.isfinite(audio).all() and rate == 24000
        seconds += len(audio) / rate
print(json.dumps({"cuda": True, "segments": count, "generated": generated,
                  "audio_seconds": round(seconds, 2)}, ensure_ascii=False))
