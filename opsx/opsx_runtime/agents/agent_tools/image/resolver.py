"""Единственный reader ``image_tasks`` — канонический APPLY-DESIGN."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.agent_tools.image.schemas import ImageDesignTask
class ImageTaskResolverError(Exception):
    """Не удалось разрешить задание."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ImageTaskResolver:
    """Читает только ``openspec/changes/<id>/apply-design/APPLY-DESIGN.json``."""

    def __init__(self, *, repo_root: Path) -> None:
        self._repo = repo_root.resolve()

    def _design_path(self, change_id: str) -> Path:
        return (
            self._repo
            / "openspec"
            / "changes"
            / change_id
            / "apply-design"
            / "APPLY-DESIGN.json"
        )

    def load_design(self, change_id: str) -> dict[str, Any]:
        path = self._design_path(change_id)
        if not path.is_file():
            raise ImageTaskResolverError(
                "DESIGN_MISSING",
                f"APPLY-DESIGN.json not found for {change_id}",
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ImageTaskResolverError("DESIGN_INVALID", str(exc)) from exc
        if not isinstance(payload, dict):
            raise ImageTaskResolverError("DESIGN_INVALID", "root must be object")
        return payload

    def list_tasks(self, change_id: str, *, slice_id: str | None = None) -> list[ImageDesignTask]:
        design = self.load_design(change_id)
        slices = design.get("slices")
        if not isinstance(slices, list):
            raise ImageTaskResolverError("IMAGE_TASKS_MISSING", "slices missing in design")
        tasks: list[ImageDesignTask] = []
        for sl in slices:
            if not isinstance(sl, dict):
                continue
            sid = str(sl.get("slice_id") or "").strip()
            if slice_id and sid != slice_id:
                continue
            raw = sl.get("image_tasks")
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                data = dict(item)
                if "slice_id" not in data:
                    data["slice_id"] = sid
                tasks.append(ImageDesignTask.model_validate(data))
        return tasks

    def resolve(
        self,
        change_id: str,
        task_id: str,
        *,
        slice_id: str | None = None,
    ) -> ImageDesignTask:
        matches = [
            t for t in self.list_tasks(change_id, slice_id=slice_id) if t.task_id == task_id
        ]
        if not matches:
            raise ImageTaskResolverError(
                "TASK_NOT_FOUND",
                f"task_id={task_id!r} not in design image_tasks",
            )
        if len(matches) > 1:
            raise ImageTaskResolverError("TASK_AMBIGUOUS", f"duplicate task_id {task_id!r}")
        return matches[0]
