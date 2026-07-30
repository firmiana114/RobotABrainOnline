from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import threading

from .dialogue import DialogueError, DialogueStore


logger = logging.getLogger(__name__)
ROUTE_FILE_SUFFIX = "_route.json"


class DialogueFileCatalog:
    def __init__(self, default_store: DialogueStore):
        self.default_store = default_store
        self.default_file = default_store.dialogue_file.resolve()
        self.conf_dir = self.default_file.parent
        self.selection_file = self.conf_dir.parent / "runtime" / "active_dialogue.json"
        self._lock = threading.RLock()
        self._stores = {self.default_file: default_store}

    @staticmethod
    def _is_dialogue_file(path: Path) -> bool:
        return path.suffix.lower() == ".json" and not path.name.endswith(ROUTE_FILE_SUFFIX)

    def list_files(self) -> dict:
        try:
            candidates = []
            for path in self.conf_dir.glob("*.json"):
                if not path.is_file() or not self._is_dialogue_file(path):
                    continue
                resolved = path.resolve()
                if resolved.parent == self.conf_dir:
                    candidates.append(resolved)
        except OSError as exc:
            logger.exception("枚举台词配置文件失败：directory=%s", self.conf_dir)
            raise DialogueError(f"无法读取台词配置目录：{self.conf_dir}") from exc

        candidates.sort(
            key=lambda path: (
                path != self.default_file,
                path.name.casefold(),
            )
        )
        logger.info(
            "台词配置文件列表已刷新：directory=%s count=%s default=%s",
            self.conf_dir,
            len(candidates),
            self.default_file.name,
        )
        active_file = self.active_file_name()
        return {
            "ok": True,
            "directory": str(self.conf_dir),
            "default_file": self.default_file.name,
            "active_file": active_file,
            "files": [
                {
                    "name": path.name,
                    "is_default": path == self.default_file,
                    "is_active": path.name == active_file,
                }
                for path in candidates
            ],
        }

    def _resolve_file(self, file_name: str | None) -> Path:
        selected = str(file_name or self.default_file.name).strip()
        candidate = Path(selected)
        if (
            not selected
            or candidate.name != selected
            or candidate.suffix.lower() != ".json"
            or selected.endswith(ROUTE_FILE_SUFFIX)
        ):
            logger.warning("拒绝非法台词文件选择：file_name=%s", selected)
            raise DialogueError("请选择列表中的台词 JSON 文件")

        try:
            resolved = (self.conf_dir / candidate).resolve(strict=True)
        except FileNotFoundError as exc:
            logger.warning("选择的台词文件不存在：file_name=%s", selected)
            raise DialogueError(f"台词文件不存在：{selected}") from exc
        except OSError as exc:
            logger.exception("解析台词文件失败：file_name=%s", selected)
            raise DialogueError(f"无法读取台词文件：{selected}") from exc

        if resolved.parent != self.conf_dir or not resolved.is_file() or not self._is_dialogue_file(resolved):
            logger.warning("拒绝 conf 目录外的台词文件：file_name=%s resolved=%s", selected, resolved)
            raise DialogueError("请选择列表中的台词 JSON 文件")
        return resolved

    def _store_for(self, file_name: str | None) -> DialogueStore:
        dialogue_file = self._resolve_file(file_name)
        with self._lock:
            store = self._stores.get(dialogue_file)
            if store is not None:
                return store
            stem = dialogue_file.stem
            store = DialogueStore(
                dialogue_file,
                dialogue_file.with_name(f"{stem}_route.json"),
                dialogue_file.with_name(f"{stem}_qa_context.txt"),
                self.default_store.map_root,
            )
            self._stores[dialogue_file] = store
            return store

    def active_file_name(self) -> str:
        if not self.selection_file.exists():
            return self.default_file.name
        try:
            payload = json.loads(self.selection_file.read_text(encoding="utf-8"))
            selected = str(payload.get("dialogue_file") or "").strip()
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            logger.warning(
                "读取当前导览台词选择失败，回退默认文件：path=%s error_type=%s",
                self.selection_file,
                type(exc).__name__,
            )
            return self.default_file.name
        try:
            return self._resolve_file(selected).name
        except DialogueError:
            logger.warning(
                "当前导览台词选择不可用，回退默认文件：file_name=%s",
                selected,
            )
            return self.default_file.name

    def _write_active_file(self, file_name: str) -> None:
        self.selection_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.selection_file.name}.",
            suffix=".tmp",
            dir=self.selection_file.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"dialogue_file": file_name},
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.selection_file)
        except OSError as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            logger.exception(
                "保存当前导览台词选择失败：path=%s file=%s",
                self.selection_file,
                file_name,
            )
            raise DialogueError("保存当前导览台词选择失败") from exc

    def activate(self, file_name: str | None = None) -> dict:
        selected = file_name or self.active_file_name()
        source_store = self._store_for(selected)
        active_store = DialogueStore(
            source_store.dialogue_file,
            self.default_store.route_file,
            self.default_store.qa_context_file,
            self.default_store.map_root,
        )
        logger.info(
            "准备激活导览台词文件：file=%s route=%s qa=%s",
            source_store.dialogue_file.name,
            active_store.route_file,
            active_store.qa_context_file,
        )
        try:
            sync_result = active_store.sync_runtime_files(backup=False)
        except DialogueError:
            logger.warning(
                "导览台词文件激活被拒绝：file=%s",
                source_store.dialogue_file.name,
            )
            raise
        except OSError as exc:
            logger.exception(
                "导览台词派生文件激活失败：file=%s",
                source_store.dialogue_file.name,
            )
            raise DialogueError(
                f"激活台词文件失败：{source_store.dialogue_file.name}"
            ) from exc
        self._write_active_file(source_store.dialogue_file.name)
        logger.info(
            "导览台词文件已激活：file=%s route_nodes=%s",
            source_store.dialogue_file.name,
            sync_result.get("route_nodes"),
        )
        return {
            "ok": True,
            "dialogue_file": source_store.dialogue_file.name,
            "route_file": str(active_store.route_file),
            "qa_context_file": str(active_store.qa_context_file),
            "route_nodes": sync_result.get("route_nodes"),
        }

    def active_map_path(self) -> str:
        return self._store_for(self.active_file_name()).map_path()

    def read_hot_rows(self, file_name: str | None) -> dict:
        store = self._store_for(file_name)
        logger.info("加载点位台词文件：file=%s", store.dialogue_file.name)
        result = store.read_hot_rows()
        result["dialogue_file"] = store.dialogue_file.name
        result["is_default"] = store.dialogue_file.resolve() == self.default_file
        result["message"] = f"已加载台词文件：{store.dialogue_file.name}"
        return result

    def write_hot_rows(self, file_name: str | None, rows: object) -> dict:
        store = self._store_for(file_name)
        row_count = len(rows) if isinstance(rows, list) else -1
        logger.info(
            "准备保存点位台词文件：file=%s rows=%s",
            store.dialogue_file.name,
            row_count,
        )
        try:
            result = store.write_hot_rows(rows)
        except DialogueError:
            logger.warning(
                "点位台词文件保存被拒绝：file=%s rows=%s",
                store.dialogue_file.name,
                row_count,
            )
            raise
        is_default = store.dialogue_file.resolve() == self.default_file
        result["dialogue_file"] = store.dialogue_file.name
        result["is_default"] = is_default
        if is_default:
            result["message"] = f"已保存当前导览台词文件：{store.dialogue_file.name}"
        else:
            result["message"] = (
                f"已保存台词文件：{store.dialogue_file.name}；"
                f"当前正式导览仍使用 {self.default_file.name}"
            )
        logger.info(
            "点位台词文件保存完成：file=%s rows=%s is_default=%s",
            store.dialogue_file.name,
            row_count,
            is_default,
        )
        return result
