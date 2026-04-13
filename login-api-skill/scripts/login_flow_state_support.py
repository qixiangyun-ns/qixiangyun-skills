"""登录全流程状态支持模块。

说明：
- 用于把每一步的关键输出持久化，供后续步骤自动承接入参。
- 该状态文件只保存流程串联所需的业务字段，不保存明文密码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .login_state_support import resolve_skills_root


def resolve_login_flow_state_path(current_file: str | Path) -> Path:
    """返回登录流程状态文件路径。"""

    override_path = os.environ.get("QXY_LOGIN_FLOW_STATE_PATH")
    if override_path:
        return Path(override_path).expanduser().resolve()
    return resolve_skills_root(current_file) / ".qxy_login_flow_state.json"


def read_login_flow_state(current_file: str | Path) -> dict[str, Any]:
    """读取登录流程状态。"""

    state_path = resolve_login_flow_state_path(current_file)
    if not state_path.exists():
        return {"version": 1}

    with state_path.open("r", encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, dict):
        return {"version": 1}
    payload.setdefault("version", 1)
    return payload


def write_login_flow_state(current_file: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """写入完整登录流程状态。"""

    state_path = resolve_login_flow_state_path(current_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")
    return payload


def merge_login_flow_state(
    current_file: str | Path,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """合并并写入登录流程状态。"""

    state = read_login_flow_state(current_file)
    _deep_merge(state, patch)
    return write_login_flow_state(current_file, state)


def clear_login_flow_state(current_file: str | Path) -> Path:
    """清理登录流程状态。"""

    state_path = resolve_login_flow_state_path(current_file)
    if state_path.exists():
        state_path.unlink()
    return state_path


def _deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    """递归合并对象。"""

    for key, value in patch.items():
        if (
            isinstance(value, dict)
            and isinstance(target.get(key), dict)
        ):
            _deep_merge(target[key], value)
            continue
        target[key] = value
