from types import SimpleNamespace
import pytest


def test_offline_tts_does_not_construct_cloud_client(monkeypatch, tmp_path):
    import tour_guide_xiaozhi_interrupt_resume as module
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline TTS must not create a cloud client")
    monkeypatch.setattr(module, "OpenAI", forbidden)
    args = SimpleNamespace(base_dir=str(tmp_path), input_sample_rate=48000,
                           chunk_ms=100, kokoro_repo_id="hexgrad/Kokoro-82M",
                           kokoro_voice="zm_yunxi")
    assistant = module.TourGuideAssistant(args, offline_tts=True)
    assert assistant.qwen_client is None
    assert assistant.kokoro_ready is False
    with pytest.raises(RuntimeError, match="DashScope"):
        module.TourGuideAssistant(args)
