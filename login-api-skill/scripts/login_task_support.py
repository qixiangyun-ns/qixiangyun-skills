"""登录验证码任务状态支持模块。

说明：
- 用于在“发送验证码”和“上传验证码”之间保存最近一次任务来源。
- 这样上层 CLI 即使只拿到 `taskId`，也能路由到正确的底层验码接口。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .login_state_support import resolve_skills_root


def resolve_pending_login_task_path(current_file: str | Path) -> Path:
    """返回待验码任务状态文件路径。"""

    override_path = os.environ.get("QXY_LOGIN_PENDING_TASK_PATH")
    if override_path:
        return Path(override_path).expanduser().resolve()
    return resolve_skills_root(current_file) / ".qxy_login_pending_task.json"


def save_pending_login_task(
    current_file: str | Path,
    *,
    task_id: str,
    flow: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入待验码任务状态。"""

    task_path = resolve_pending_login_task_path(current_file)
    task_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "taskId": str(task_id),
        "flow": str(flow),
    }
    if extra:
        payload.update(extra)

    with task_path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")

    return payload


def read_pending_login_task(
    current_file: str | Path,
    *,
    task_id: str | None = None,
) -> dict[str, Any] | None:
    """读取待验码任务状态。"""

    task_path = resolve_pending_login_task_path(current_file)
    if not task_path.exists():
        return None

    with task_path.open("r", encoding="utf-8") as file_obj:
        raw_payload = json.load(file_obj)
    if not isinstance(raw_payload, dict):
        return None

    if task_id is not None and str(raw_payload.get("taskId") or "").strip() != str(task_id).strip():
        return None
    return raw_payload


def clear_pending_login_task(current_file: str | Path) -> Path:
    """清理待验码任务状态。"""

    task_path = resolve_pending_login_task_path(current_file)
    if task_path.exists():
        task_path.unlink()
    return task_path
