from __future__ import annotations

from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading


logger = logging.getLogger(__name__)

COORDINATE_FIELDS = ("x", "y", "z", "ox", "oy", "oz", "ow")
OPENING_SEQUENCE = (
    ("handshake_greeting", "shake_hand"),
    ("handshake_welcome", "shake_hand"),
    ("group_welcome", "face_wave"),
    ("follow_intro", ""),
)
BACKUP_LIMIT = 20
MAX_POST_WAIT_SECONDS = 3600
POST_WAIT_DIRECTIVE = re.compile(r'^\s*\{\s*"post_wait_seconds"\s*:\s*([^}]+)\s*\}\s*$')


class DialogueError(RuntimeError):
    """导览配置不可读取、校验或保存。"""


def _normalize_post_wait_seconds(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DialogueError(f"{label} post_wait_seconds 必须是数字")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0 or seconds > MAX_POST_WAIT_SECONDS:
        raise DialogueError(f"{label} post_wait_seconds 必须在 0 到 {MAX_POST_WAIT_SECONDS} 秒之间")
    return int(seconds) if seconds.is_integer() else seconds


def _segments_to_script(segments: list) -> str:
    parts = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        lines = [text]
        if "post_wait_seconds" in segment:
            seconds = _normalize_post_wait_seconds(segment["post_wait_seconds"], "台词段")
            lines.append(json.dumps({"post_wait_seconds": seconds}, ensure_ascii=False, separators=(",", ":")))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _parse_script_segments(script: object) -> list[dict]:
    text = str(script or "").strip()
    if not text:
        return []
    segments: list[dict] = []
    current_lines: list[str] = []

    def flush_text() -> None:
        content = "\n".join(current_lines).strip()
        current_lines.clear()
        if content:
            segments.append({"text": content})

    for line_number, line in enumerate(text.splitlines(), start=1):
        directive = POST_WAIT_DIRECTIVE.fullmatch(line)
        if directive:
            flush_text()
            if not segments:
                raise DialogueError(f"第 {line_number} 行等待指令前必须先填写一段台词")
            if "post_wait_seconds" in segments[-1]:
                raise DialogueError(f"第 {line_number} 行重复设置了等待时间")
            try:
                value = json.loads(directive.group(1))
            except json.JSONDecodeError as exc:
                raise DialogueError(f"第 {line_number} 行 post_wait_seconds 不是有效数字") from exc
            segments[-1]["post_wait_seconds"] = _normalize_post_wait_seconds(value, f"第 {line_number} 行")
            continue
        if "post_wait_seconds" in line:
            raise DialogueError(
                f'第 {line_number} 行等待指令格式错误，应为 {{"post_wait_seconds":数字}}'
            )
        if not line.strip():
            flush_text()
            continue
        current_lines.append(line)
    flush_text()
    return segments


def _formatted_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = path.with_name(f"{path.name}.{stamp}.bak")
    shutil.copy2(path, backup)
    backups = sorted(path.parent.glob(f"{path.name}.*.bak"), key=lambda item: item.stat().st_mtime)
    for old in backups[:-BACKUP_LIMIT]:
        try:
            old.unlink()
        except OSError as exc:
            logger.warning("清理旧备份失败：path=%s error_type=%s", old, type(exc).__name__)
    return backup


def _normalize_coordinate(raw: object, label: str) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DialogueError(f"{label}坐标不是有效 JSON 对象") from exc
    if not isinstance(raw, dict):
        raise DialogueError(f"{label}坐标必须是 JSON 对象")
    missing = [field for field in COORDINATE_FIELDS if field not in raw]
    if missing:
        raise DialogueError(f"{label}坐标缺少字段：{', '.join(missing)}")
    coordinate: dict[str, float | int] = {}
    for field in COORDINATE_FIELDS:
        try:
            coordinate[field] = float(raw[field])
        except (TypeError, ValueError) as exc:
            raise DialogueError(f"{label}坐标字段 {field} 必须是数字") from exc
    try:
        coordinate["mode"] = int(raw.get("mode", 1))
    except (TypeError, ValueError) as exc:
        raise DialogueError(f"{label}坐标字段 mode 必须是整数") from exc
    if "speed" in raw and raw["speed"] is not None:
        try:
            coordinate["speed"] = float(raw["speed"])
        except (TypeError, ValueError) as exc:
            raise DialogueError(f"{label}坐标字段 speed 必须是数字") from exc
    return coordinate


def validate_dialogue(data: object, path: Path | None = None) -> dict:
    source = str(path or "导览配置")
    if not isinstance(data, dict):
        raise DialogueError(f"JSON 根节点必须是对象：{source}")
    for key, expected in (("variables", dict), ("opening", dict), ("points", dict), ("steps", list)):
        value = data.get(key)
        if not isinstance(value, expected):
            raise DialogueError(f"{key} 必须是{'对象' if expected is dict else '数组'}")
    back_points = data.get("back_points", [])
    if not isinstance(back_points, list):
        raise DialogueError("back_points 必须是数组")
    map_file = data.get("map_file", "")
    if not isinstance(map_file, str) or not map_file.strip():
        raise DialogueError("map_file 必须是非空字符串")
    for key, value in data["opening"].items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise DialogueError("opening 的键和值都必须是字符串")
    for point_key, point in data["points"].items():
        if not isinstance(point_key, str) or not point_key.strip() or not isinstance(point, dict):
            raise DialogueError("points 的每个条目必须是具名对象")
        locations = point.get("location", [])
        if not isinstance(locations, list):
            raise DialogueError(f"点位 {point_key} 的 location 必须是数组")
        point["location"] = [
            _normalize_coordinate(item, f"点位 {point_key} 第 {index + 1} 个")
            for index, item in enumerate(locations)
        ]
    for index, step in enumerate(data["steps"]):
        if not isinstance(step, dict):
            raise DialogueError(f"steps[{index}] 必须是对象")
        segments = step.get("segments", [])
        if not isinstance(segments, list):
            raise DialogueError(f"steps[{index}].segments 必须是数组")
        for segment_index, segment in enumerate(segments):
            if not isinstance(segment, dict) or not isinstance(segment.get("text", ""), str):
                raise DialogueError(f"steps[{index}].segments[{segment_index}] 必须包含字符串 text")
            if "post_wait_seconds" in segment:
                segment["post_wait_seconds"] = _normalize_post_wait_seconds(
                    segment["post_wait_seconds"], f"steps[{index}].segments[{segment_index}]"
                )
    return data


def read_dialogue(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.error("读取导览配置失败：path=%s error_type=%s", path, type(exc).__name__)
        raise DialogueError(f"读取导览配置失败：{path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("导览 JSON 解析失败：path=%s line=%s column=%s", path, exc.lineno, exc.colno)
        raise DialogueError(f"JSON 解析失败：第 {exc.lineno} 行，第 {exc.colno} 列") from exc
    return validate_dialogue(data, path)


def _substitute(text: object, variables: dict) -> str:
    result = str(text or "")
    for key, value in variables.items():
        result = result.replace("{" + str(key) + "}", str(value))
    return result


def build_route(data: dict) -> list[dict]:
    validate_dialogue(data)
    variables = data.get("variables", {})
    opening = data.get("opening", {})
    points = data.get("points", {})
    route: list[dict] = []
    opening_segments = []
    for key, action in OPENING_SEQUENCE:
        text = _substitute(opening.get(key, ""), variables).strip()
        if not text:
            continue
        segment = {"text": text}
        if action:
            segment.update({"action": action, "speak_with_action": True})
        opening_segments.append(segment)
    if opening_segments:
        route.append({"name": "opening", "scene": "开场", "skip_navigation": True, "segments": opening_segments})

    for step in data.get("steps", []):
        point_key = str(step.get("entity_key") or "").strip()
        if point_key == "point_1":
            continue
        point = points.get(point_key)
        locations = point.get("location", []) if isinstance(point, dict) else []
        if not locations:
            continue
        segments = []
        for original in step.get("segments", []):
            text = _substitute(original.get("text", ""), variables).strip()
            if not text:
                continue
            segment = dict(original)
            segment["text"] = text
            segments.append(segment)
        node = {
            "name": point_key,
            "scene": str(step.get("scene") or point.get("name") or point_key),
            "goal": dict(locations[0]),
            "segments": segments,
        }
        if step.get("skip_navigation"):
            node["skip_navigation"] = True
        route.append(node)
    if not route:
        raise DialogueError("导览配置没有生成任何可运行路线")
    return route


def build_qa_context(data: dict) -> str:
    validate_dialogue(data)
    variables = data.get("variables", {})
    lines = ["复星G1机器人在线导览知识上下文", ""]
    opening_texts = [_substitute(value, variables).strip() for value in data.get("opening", {}).values()]
    opening_texts = [text for text in opening_texts if text]
    if opening_texts:
        lines.extend(("【开场】", "".join(opening_texts), ""))
    for step in data.get("steps", []):
        texts = [_substitute(segment.get("text", ""), variables).strip() for segment in step.get("segments", [])]
        texts = [text for text in texts if text]
        if texts:
            lines.extend((f"【{step.get('scene') or step.get('entity_key') or '点位'}】", "".join(texts), ""))
    return "\n".join(lines).rstrip() + "\n"


def resolve_map_path(data: dict, map_root: Path) -> str:
    value = str(data.get("map_file") or "").strip()
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate)
    if candidate.name != value or value in {".", ".."}:
        raise DialogueError("相对 map_file 只能填写文件名，不能包含目录或路径穿越")
    return str(map_root / candidate)


def _summary(data: dict, path: Path) -> dict:
    return {
        "path": str(path),
        "leader_calling": str(data.get("variables", {}).get("leader_calling") or "").strip(),
        "map_file": str(data.get("map_file") or ""),
        "steps": len(data.get("steps", [])),
        "segments": sum(len(step.get("segments", [])) for step in data.get("steps", []) if isinstance(step, dict)),
        "points": len(data.get("points", {})),
    }


class DialogueStore:
    def __init__(self, dialogue_file: Path, route_file: Path, qa_context_file: Path, map_root: Path):
        self.dialogue_file = Path(dialogue_file)
        self.route_file = Path(route_file)
        self.qa_context_file = Path(qa_context_file)
        self.map_root = Path(map_root)
        self._lock = threading.RLock()

    def load(self) -> dict:
        with self._lock:
            return read_dialogue(self.dialogue_file)

    def sync_runtime_files(self, backup: bool = False) -> dict:
        with self._lock:
            data = self.load()
            route_content = _formatted_json(build_route(data))
            qa_content = build_qa_context(data)
            if backup:
                _backup(self.route_file)
                _backup(self.qa_context_file)
            _atomic_write(self.route_file, route_content)
            _atomic_write(self.qa_context_file, qa_content)
            logger.info(
                "导览派生文件已同步：dialogue=%s route=%s qa=%s route_nodes=%s",
                self.dialogue_file,
                self.route_file,
                self.qa_context_file,
                len(json.loads(route_content)),
            )
            return {"route_nodes": len(json.loads(route_content)), "route_file": str(self.route_file)}

    def _save_data(self, data: dict, message: str) -> dict:
        data = validate_dialogue(data, self.dialogue_file)
        route = build_route(data)
        qa_context = build_qa_context(data)
        canonical_content = _formatted_json(data)
        route_content = _formatted_json(route)
        with self._lock:
            backup = _backup(self.dialogue_file)
            _backup(self.route_file)
            _backup(self.qa_context_file)
            try:
                _atomic_write(self.dialogue_file, canonical_content)
                _atomic_write(self.route_file, route_content)
                _atomic_write(self.qa_context_file, qa_context)
            except OSError as exc:
                logger.exception("保存导览配置失败：path=%s", self.dialogue_file)
                raise DialogueError(f"保存导览配置失败：{self.dialogue_file}") from exc
        logger.info(
            "导览配置已保存：path=%s steps=%s points=%s route_nodes=%s backup=%s",
            self.dialogue_file,
            len(data["steps"]),
            len(data["points"]),
            len(route),
            backup,
        )
        return {
            "ok": True,
            "valid": True,
            "path": str(self.dialogue_file),
            "content": canonical_content,
            "message": message,
            "summary": _summary(data, self.dialogue_file),
        }

    def read_editor(self) -> dict:
        data = self.load()
        return {
            "ok": True,
            "valid": True,
            "path": str(self.dialogue_file),
            "content": _formatted_json(data),
            "message": "导览台词已加载",
            "summary": _summary(data, self.dialogue_file),
        }

    def write_editor(self, content: str) -> dict:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise DialogueError(f"JSON 解析失败：第 {exc.lineno} 行，第 {exc.colno} 列") from exc
        return self._save_data(data, "导览台词已保存，下一次导览生效")

    def read_leader_calling(self) -> dict:
        data = self.load()
        return {
            "ok": True,
            "leader_calling": str(data.get("variables", {}).get("leader_calling") or "").strip(),
            "message": "嘉宾称呼已加载",
            "summary": _summary(data, self.dialogue_file),
        }

    def write_leader_calling(self, value: object) -> dict:
        leader = str(value or "").strip()
        if not leader:
            raise DialogueError("嘉宾称呼不能为空")
        if len(leader) > 80:
            raise DialogueError("嘉宾称呼不能超过 80 个字符")
        data = self.load()
        data.setdefault("variables", {})["leader_calling"] = leader
        result = self._save_data(data, "嘉宾称呼已保存，下一次导览生效")
        result["leader_calling"] = leader
        return result

    def read_hot_rows(self) -> dict:
        data = self.load()
        rows = [{
            "id": "opening",
            "row_type": "opening",
            "point_name": "opening",
            "point_key": "",
            "coordinate": "",
            "script": _formatted_json(data.get("opening", {})).strip(),
        }]
        points = data.get("points", {})
        for index, step in enumerate(data.get("steps", [])):
            point_key = str(step.get("entity_key") or "")
            point = points.get(point_key, {}) if point_key else {}
            locations = point.get("location", []) if isinstance(point, dict) else []
            coordinate = json.dumps(locations[0], ensure_ascii=False, separators=(",", ":")) if locations else ""
            rows.append({
                "id": f"step_{index}",
                "row_type": "step",
                "step_index": index,
                "point_name": str(step.get("scene") or point_key or f"步骤{index + 1}"),
                "point_key": point_key,
                "coordinate": coordinate,
                "script": _segments_to_script(step.get("segments", [])),
            })
        return {
            "ok": True,
            "path": str(self.dialogue_file),
            "rows": rows,
            "message": "点位台词已加载",
            "summary": _summary(data, self.dialogue_file),
        }

    @staticmethod
    def _segments_from_script(script: object, original: list) -> list[dict]:
        parsed = _parse_script_segments(script)
        if len(parsed) == len(original) and all(isinstance(item, dict) for item in original):
            result = []
            for item, update in zip(original, parsed):
                segment = dict(item)
                segment["text"] = update["text"]
                segment.pop("post_wait_seconds", None)
                if "post_wait_seconds" in update:
                    segment["post_wait_seconds"] = update["post_wait_seconds"]
                result.append(segment)
            return result
        return parsed

    def write_hot_rows(self, rows: object) -> dict:
        if not isinstance(rows, list):
            raise DialogueError("点位台词表格必须是数组")
        data = self.load()
        original_steps = data.get("steps", [])
        points = data.get("points", {})
        new_steps = []
        opening = None
        used_keys = set(points)
        for row_index, row in enumerate(rows):
            number = row_index + 1
            if not isinstance(row, dict):
                raise DialogueError(f"第 {number} 行必须是对象")
            row_type = str(row.get("row_type") or "step")
            point_name = str(row.get("point_name") or "").strip()
            if row_type == "opening" or point_name.lower() == "opening":
                try:
                    opening = json.loads(str(row.get("script") or ""))
                except json.JSONDecodeError as exc:
                    raise DialogueError("opening 讲解台词必须是 JSON 对象") from exc
                if not isinstance(opening, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in opening.items()):
                    raise DialogueError("opening 的键和值都必须是字符串")
                continue
            if not point_name:
                raise DialogueError(f"第 {number} 行点位名字不能为空")
            match = re.fullmatch(r"step_(\d+)", str(row.get("id") or ""))
            step_index = int(match.group(1)) if match else -1
            original = dict(original_steps[step_index]) if 0 <= step_index < len(original_steps) else {}
            point_key = str(row.get("point_key") or original.get("entity_key") or "").strip()
            if not point_key:
                base = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", point_name).strip("_") or str(number)
                point_key = f"console_point_{base}"
                suffix = 2
                while point_key in used_keys:
                    point_key = f"console_point_{base}_{suffix}"
                    suffix += 1
            used_keys.add(point_key)
            coordinate = _normalize_coordinate(row.get("coordinate"), f"第 {number} 行")
            existing_point = points.get(point_key, {})
            point = dict(existing_point) if isinstance(existing_point, dict) else {}
            point.update({"name": point_name, "summary": str(point.get("summary") or point_name), "location": [coordinate]})
            point.setdefault("description", "网页控制台编辑点位。")
            points[point_key] = point
            original_segments = original.get("segments", []) if isinstance(original.get("segments", []), list) else []
            original.update({
                "scene": point_name,
                "entity_key": point_key,
                "segments": self._segments_from_script(row.get("script"), original_segments),
            })
            new_steps.append(original)
        if opening is not None:
            data["opening"] = opening
        data["points"] = points
        data["steps"] = new_steps
        self._save_data(data, "点位台词已保存，下一次导览生效")
        return self.read_hot_rows()

    def map_path(self) -> str:
        return resolve_map_path(self.load(), self.map_root)
