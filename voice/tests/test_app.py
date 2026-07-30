import json

from fastapi.testclient import TestClient

from voice_console.app import create_app


class FakeManager:
    def __init__(self):
        self.states = {"navigation": "inactive", "arm": "inactive", "tour": "inactive"}

    def all_states(self):
        return dict(self.states)

    def external_processes(self):
        return {"navigation": False, "arm": False, "tour": False}

    def start_navigation(self):
        self.states["navigation"] = "active"
        return {"ok": True, "state": "active", "message": "导航启动命令已提交"}

    def start_arm(self):
        self.states["arm"] = "active"
        return {"ok": True, "state": "active", "message": "手臂控制启动命令已提交"}

    def start_tour(self):
        return {"ok": True, "state": "starting", "message": "导览任务已立即启动"}

    def restart_tour_service(self):
        self.states["tour"] = "active"
        return {"ok": True, "state": "active", "message": "导览服务正在预热"}

    def stop_tour(self):
        return {"ok": True, "state": "stopping", "message": "导览任务停止请求已提交，预热服务保持运行"}


class FakeTracker:
    def start(self):
        pass

    def stop(self):
        pass

    def snapshot(self):
        return {"available": False, "localized": False, "fresh": False, "message": "暂无定位位姿"}


def test_page_structure_and_apis(config_and_store):
    config, store = config_and_store
    manager = FakeManager()
    app = create_app(config, manager=manager, store=store, tracker=FakeTracker())
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        html = page.text
        assert html.count('class="btn nav-start"') == 1
        assert html.count('class="btn arm-start"') == 1
        assert html.count('class="btn tour-start"') == 1
        assert html.count('class="btn tour-stop"') == 1
        assert html.count('class="btn tour-restart"') == 1
        assert ">重启导航</button>" in html
        assert ">重启手臂控制</button>" in html
        assert ">重启导览服务</button>" in html
        assert "服务状态管理" not in html
        assert "开发人员选项" not in html
        assert "任务控制" in html and "点位台词" in html
        assert "serviceState==='active'||externalProcess" in html
        assert "getElementById('startNavigationBtn').disabled=false" in html
        assert "getElementById('startArmBtn').disabled=false" in html
        assert 'id="dialogueFileSelect"' in html
        assert "/api/dialogue/files" in html
        assert '{"speak_during_navigation":true}' in html
        assert '{"background_command":"coffee_delivery_run"}' in html
        assert "startTour(this)" in html
        assert 'id="activeDialogueFile"' in html

        status = client.get("/api/status").json()
        assert status["services"]["tour"] == "inactive"
        assert status["external_processes"] == {"navigation": False, "arm": False, "tour": False}
        assert status["active_dialogue_file"] == "dialogue_0.json"
        assert client.post("/api/navigation/start").status_code == 200
        assert client.post("/api/arm/start").status_code == 200
        assert client.post("/api/tour/restart-service").status_code == 200
        assert client.post("/api/tour/start").status_code == 200
        stopped = client.post("/api/tour/stop").json()
        assert "预热服务保持运行" in stopped["message"]
        assert manager.states["navigation"] == "active"
        assert manager.states["arm"] == "active"

        rows = client.get("/api/dialogue/hot-rows")
        assert rows.status_code == 200
        assert rows.json()["rows"][0]["row_type"] == "opening"
        files = client.get("/api/dialogue/files").json()
        assert files["default_file"] == "dialogue_0.json"
        assert files["active_file"] == "dialogue_0.json"
        assert files["files"] == [{
            "name": "dialogue_0.json",
            "is_default": True,
            "is_active": True,
        }]


def test_invalid_leader_returns_400(config_and_store):
    config, store = config_and_store
    app = create_app(config, manager=FakeManager(), store=store, tracker=FakeTracker())
    with TestClient(app) as client:
        response = client.post("/api/dialogue/leader-calling", json={"leader_calling": ""})
        assert response.status_code == 400


def test_hot_rows_can_switch_and_save_another_dialogue_file(config_and_store):
    config, store = config_and_store
    alternate_file = config.dialogue_file.with_name("dialogue_1.json")
    alternate_data = store.load()
    alternate_data["opening"]["handshake_welcome"] = "备用台词文件。"
    alternate_file.write_text(
        json.dumps(alternate_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    alternate_route = alternate_file.with_name("dialogue_1_route.json")
    alternate_route.write_text("[]\n", encoding="utf-8")
    default_before = config.dialogue_file.read_text(encoding="utf-8")

    app = create_app(config, manager=FakeManager(), store=store, tracker=FakeTracker())
    with TestClient(app) as client:
        files = client.get("/api/dialogue/files")
        assert files.status_code == 200
        assert [item["name"] for item in files.json()["files"]] == [
            "dialogue_0.json",
            "dialogue_1.json",
        ]

        response = client.get(
            "/api/dialogue/hot-rows",
            params={"dialogue_file": "dialogue_1.json"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["dialogue_file"] == "dialogue_1.json"
        assert payload["is_default"] is False
        opening = payload["rows"][0]
        opening_data = json.loads(opening["script"])
        assert opening_data["handshake_welcome"] == "备用台词文件。"
        opening_data["handshake_welcome"] = "备用台词已更新。"
        opening["script"] = json.dumps(opening_data, ensure_ascii=False)

        saved = client.post(
            "/api/dialogue/hot-rows",
            json={
                "dialogue_file": "dialogue_1.json",
                "rows": payload["rows"],
            },
        )
        assert saved.status_code == 200
        assert "当前正式导览仍使用 dialogue_0.json" in saved.json()["message"]

        started = client.post(
            "/api/tour/start",
            json={"dialogue_file": "dialogue_1.json"},
        )
        assert started.status_code == 200
        assert started.json()["dialogue_file"] == "dialogue_1.json"
        assert "台词文件：dialogue_1.json" in started.json()["message"]
        active_files = client.get("/api/dialogue/files").json()
        assert active_files["active_file"] == "dialogue_1.json"
        assert next(
            item for item in active_files["files"] if item["name"] == "dialogue_1.json"
        )["is_active"] is True
        assert client.get("/api/status").json()["active_dialogue_file"] == "dialogue_1.json"

    assert config.dialogue_file.read_text(encoding="utf-8") == default_before
    updated = json.loads(alternate_file.read_text(encoding="utf-8"))
    assert updated["opening"]["handshake_welcome"] == "备用台词已更新。"
    assert alternate_route.is_file()
    assert json.loads(alternate_route.read_text(encoding="utf-8"))
    assert alternate_file.with_name("dialogue_1_qa_context.txt").is_file()
    active_route = json.loads(config.route_file.read_text(encoding="utf-8"))
    assert any(
        segment.get("text") == "备用台词已更新。"
        for node in active_route
        for segment in node.get("segments", [])
    )
    selection = json.loads(
        (config.project_root / "runtime" / "active_dialogue.json").read_text(
            encoding="utf-8"
        )
    )
    assert selection["dialogue_file"] == "dialogue_1.json"


def test_hot_rows_reject_file_name_outside_conf(config_and_store):
    config, store = config_and_store
    app = create_app(config, manager=FakeManager(), store=store, tracker=FakeTracker())
    with TestClient(app) as client:
        traversal = client.get(
            "/api/dialogue/hot-rows",
            params={"dialogue_file": "../dialogue_0.json"},
        )
        route_file = client.get(
            "/api/dialogue/hot-rows",
            params={"dialogue_file": "dialogue_0_route.json"},
        )
        missing = client.get(
            "/api/dialogue/hot-rows",
            params={"dialogue_file": "dialogue_missing.json"},
        )

    assert traversal.status_code == 400
    assert route_file.status_code == 400
    assert missing.status_code == 400
