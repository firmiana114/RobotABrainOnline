import json

import pytest

from voice_console.dialogue import DialogueError, build_qa_context, build_route


def test_route_generation_and_context(config_and_store):
    _config, store = config_and_store
    data = store.load()
    data["variables"]["leader_calling"] = "测试嘉宾"
    data["opening"]["handshake_greeting"] = "{leader_calling}您好。"
    route = build_route(data)
    assert route[0]["skip_navigation"] is True
    assert len(route[0]["segments"]) == 4
    assert route[0]["segments"][0]["text"] == "测试嘉宾您好。"
    assert route[0]["segments"][0]["action"] == "shake_hand"
    assert all(item["name"] != "point_1" for item in route)
    context = build_qa_context(data)
    assert "测试嘉宾您好" in context
    assert "复星G1机器人在线导览知识上下文" in context


def test_save_is_atomic_and_generates_files(config_and_store):
    config, store = config_and_store
    original = config.dialogue_file.read_text(encoding="utf-8")
    with pytest.raises(DialogueError):
        store.write_editor('{"steps":')
    assert config.dialogue_file.read_text(encoding="utf-8") == original

    result = store.write_leader_calling("各位测试嘉宾")
    assert result["leader_calling"] == "各位测试嘉宾"
    assert config.route_file.is_file()
    assert config.qa_context_file.is_file()
    route = json.loads(config.route_file.read_text(encoding="utf-8"))
    assert route and isinstance(route, list)


def test_hot_rows_preserve_action_when_segment_count_unchanged(config_and_store):
    _config, store = config_and_store
    data = store.load()
    target_index = next(index for index, step in enumerate(data["steps"]) if any("action" in segment for segment in step["segments"]))
    original_actions = [segment.get("action") for segment in data["steps"][target_index]["segments"]]
    payload = store.read_hot_rows()
    target = next(row for row in payload["rows"] if row.get("step_index") == target_index)
    target["script"] = "\n\n".join(part + "（已更新）" for part in target["script"].split("\n\n"))
    store.write_hot_rows(payload["rows"])
    updated = store.load()["steps"][target_index]
    assert [segment.get("action") for segment in updated["segments"]] == original_actions


def test_hot_rows_expose_and_update_post_wait_directive(config_and_store):
    config, store = config_and_store
    payload = store.read_hot_rows()
    target = next(row for row in payload["rows"] if row.get("point_key") == "point_8")
    assert '{"post_wait_seconds":277}' in target["script"]
    target["script"] = target["script"].replace('{"post_wait_seconds":277}', '{"post_wait_seconds":2.5}')

    store.write_hot_rows(payload["rows"])

    point_8 = next(step for step in store.load()["steps"] if step.get("entity_key") == "point_8")
    assert point_8["segments"][2]["post_wait_seconds"] == 2.5
    route = json.loads(config.route_file.read_text(encoding="utf-8"))
    route_point_8 = next(node for node in route if node.get("name") == "point_8")
    assert route_point_8["segments"][2]["post_wait_seconds"] == 2.5


def test_invalid_post_wait_directive_does_not_change_file(config_and_store):
    config, store = config_and_store
    payload = store.read_hot_rows()
    target = next(row for row in payload["rows"] if row.get("point_key") == "point_8")
    target["script"] = target["script"].replace('{"post_wait_seconds":277}', '{"post_wait_seconds":[277]}')
    before = config.dialogue_file.read_text(encoding="utf-8")

    with pytest.raises(DialogueError, match="必须是数字"):
        store.write_hot_rows(payload["rows"])

    assert config.dialogue_file.read_text(encoding="utf-8") == before


def test_invalid_coordinate_does_not_change_file(config_and_store):
    config, store = config_and_store
    rows = store.read_hot_rows()["rows"]
    point_row = next(row for row in rows if row["row_type"] == "step")
    point_row["coordinate"] = '{"x":1}'
    before = config.dialogue_file.read_text(encoding="utf-8")
    with pytest.raises(DialogueError, match="缺少字段"):
        store.write_hot_rows(rows)
    assert config.dialogue_file.read_text(encoding="utf-8") == before


def test_map_path_rejects_traversal(config_and_store):
    config, store = config_and_store
    data = store.load()
    data["map_file"] = "../secret.pcd"
    config.dialogue_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DialogueError, match="不能包含目录"):
        store.map_path()
