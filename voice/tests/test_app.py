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
        self.states["tour"] = "active"
        return {"ok": True, "state": "active", "message": "导览启动命令已提交"}

    def stop_tour(self):
        self.states["tour"] = "inactive"
        return {"ok": True, "state": "inactive", "message": "导览已关闭，导航和手臂保持运行"}


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
        assert ">重启导航</button>" in html
        assert ">重启手臂控制</button>" in html
        assert "服务状态管理" not in html
        assert "开发人员选项" not in html
        assert "任务控制" in html and "点位台词" in html
        assert "serviceState==='active'||externalProcess" in html
        assert "getElementById('startNavigationBtn').disabled=false" in html
        assert "getElementById('startArmBtn').disabled=false" in html

        status = client.get("/api/status").json()
        assert status["services"]["tour"] == "inactive"
        assert status["external_processes"] == {"navigation": False, "arm": False, "tour": False}
        assert client.post("/api/navigation/start").status_code == 200
        assert client.post("/api/arm/start").status_code == 200
        assert client.post("/api/tour/start").status_code == 200
        stopped = client.post("/api/tour/stop").json()
        assert "导航和手臂保持运行" in stopped["message"]
        assert manager.states["navigation"] == "active"
        assert manager.states["arm"] == "active"

        rows = client.get("/api/dialogue/hot-rows")
        assert rows.status_code == 200
        assert rows.json()["rows"][0]["row_type"] == "opening"


def test_invalid_leader_returns_400(config_and_store):
    config, store = config_and_store
    app = create_app(config, manager=FakeManager(), store=store, tracker=FakeTracker())
    with TestClient(app) as client:
        response = client.post("/api/dialogue/leader-calling", json={"leader_calling": ""})
        assert response.status_code == 400
