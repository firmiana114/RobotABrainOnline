import json
import threading
import urllib.error
import urllib.request

import pytest

from voice_console.tour_control import (
    TourControlError,
    TourControlServer,
    TourTaskController,
)


def test_controller_preheats_once_and_controls_task_without_restarting_process():
    stop_calls = []
    controller = TourTaskController(stop_playback=lambda: stop_calls.append("stop"))

    with pytest.raises(TourControlError, match="预热"):
        controller.request_start()

    controller.mark_ready()
    started = controller.request_start()
    assert started["state"] == "starting"
    assert controller.wait_for_start(0) is True

    controller.mark_running()
    stopped = controller.request_stop()
    assert stopped["state"] == "stopping"
    assert controller.cancel_event.is_set()
    assert stop_calls == ["stop"]

    controller.mark_finished("stopped")
    assert controller.snapshot() == {
        "ok": True,
        "state": "stopped",
        "ready": True,
        "running": False,
    }
    assert controller.request_start()["state"] == "starting"


def test_control_http_server_exposes_only_task_actions():
    controller = TourTaskController()
    controller.mark_ready()
    server = TourControlServer(("127.0.0.1", 0), controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base_url}/status", timeout=1) as response:
            assert json.loads(response.read())["state"] == "ready"

        request = urllib.request.Request(f"{base_url}/start", data=b"{}", method="POST")
        with urllib.request.urlopen(request, timeout=1) as response:
            assert json.loads(response.read())["state"] == "starting"

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=1)
        assert exc_info.value.code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
