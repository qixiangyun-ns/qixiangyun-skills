"""登录状态支持模块。

说明：
- 该文件放在每个 skill 内部，避免运行环境缺少 `skills/shared` 时导入失败。
- 三个 skill 各自携带一份，优先保证安装后的独立可用性。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LoginStateError(RuntimeError):
    """登录联动状态异常。"""


DEFAULT_LOGIN_STATE_MAX_AGE_SECONDS = 12 * 60 * 60


def resolve_skills_root(current_file: str | Path) -> Path:
    """根据当前脚本路径尽量稳健地推导 skills 根目录。"""

    current_path = Path(current_file).resolve()
    for parent in (current_path.parent, *current_path.parents):
        if parent.name == "skills":
            return parent

    if len(current_path.parents) >= 3:
        return current_path.parents[2]
    raise LoginStateError("无法从当前脚本路径推导 skills 根目录。")


def resolve_login_skill_root(current_file: str | Path) -> Path:
    """返回登录 skill 根目录。"""

    return resolve_skills_root(current_file) / "login-api-skill"


def resolve_login_state_path(current_file: str | Path) -> Path:
    """返回共享登录状态文件路径。"""

    override_path = os.environ.get("QXY_LOGIN_STATE_PATH")
    if override_path:
        return Path(override_path).expanduser().resolve()
    return resolve_skills_root(current_file) / ".qxy_login_state.json"


def is_login_skill_installed(current_file: str | Path) -> bool:
    """检查登录 skill 是否存在。"""

    login_skill_root = resolve_login_skill_root(current_file)
    required_paths = (
        login_skill_root / "SKILL.md",
        login_skill_root / "scripts" / "workflow.py",
    )
    return all(path.exists() for path in required_paths)


def read_login_state(current_file: str | Path) -> dict[str, Any] | None:
    """读取共享登录状态。"""

    state_path = resolve_login_state_path(current_file)
    if not state_path.exists():
        return None

    with state_path.open("r", encoding="utf-8") as file_obj:
        raw_state = json.load(file_obj)
    if not isinstance(raw_state, dict):
        raise LoginStateError("登录状态文件格式错误，请重新使用登录 skill 完成登录。")
    return raw_state


def clear_login_state(current_file: str | Path) -> Path:
    """清理共享登录状态。"""

    state_path = resolve_login_state_path(current_file)
    if state_path.exists():
        state_path.unlink()
    return state_path


def save_login_state(
    current_file: str | Path,
    *,
    agg_org_id: str,
    account_id: str,
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入共享登录状态，供申报和缴款 skill 复用。"""

    state_path = resolve_login_state_path(current_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "version": 1,
        "ready": True,
        "aggOrgId": str(agg_org_id),
        "accountId": str(account_id),
        "source": str(source),
        "loginSkill": "login-api-skill",
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if extra:
        payload.update(extra)

    with state_path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")

    return {
        "state_file": str(state_path),
        "state": payload,
    }


def ensure_login_prerequisites(
    current_file: str | Path,
    *,
    agg_org_id: str | None = None,
) -> dict[str, Any]:
    """校验登录 skill 和共享登录态是否可用。"""

    if not is_login_skill_installed(current_file):
        raise LoginStateError(
            "未检测到 `login-api-skill`。请先安装登录 skill 后再执行申报或缴款。"
        )

    state = read_login_state(current_file)
    if state is None:
        raise LoginStateError(
            "未检测到共享登录态。请先使用 `login-api-skill` 完成企业登录后再执行申报或缴款。"
        )

    if state.get("ready") is not True:
        raise LoginStateError(
            "当前共享登录态不是可用状态。请先使用 `login-api-skill` 重新完成企业登录。"
        )

    updated_at = str(state.get("updatedAt") or "").strip()
    if updated_at:
        try:
            normalized_updated_at = updated_at.replace("Z", "+00:00")
            updated_at_dt = datetime.fromisoformat(normalized_updated_at)
            max_age_seconds = int(
                os.environ.get(
                    "QXY_LOGIN_STATE_MAX_AGE_SECONDS",
                    str(DEFAULT_LOGIN_STATE_MAX_AGE_SECONDS),
                )
            )
            age_seconds = (
                datetime.now(timezone.utc) - updated_at_dt.astimezone(timezone.utc)
            ).total_seconds()
            if age_seconds > max_age_seconds:
                raise LoginStateError(
                    "共享登录态已过期。请重新运行 `login-api-skill` 完成登录后再继续。"
                )
        except ValueError:
            raise LoginStateError(
                "共享登录态时间戳格式错误。请重新运行 `login-api-skill` 完成登录。"
            )

    state_agg_org_id = str(state.get("aggOrgId") or "").strip()
    state_account_id = str(state.get("accountId") or "").strip()
    if not state_agg_org_id or not state_account_id:
        raise LoginStateError(
            "共享登录态缺少 `aggOrgId` 或 `accountId`。请重新使用登录 skill 完成登录。"
        )

    if agg_org_id is not None and str(agg_org_id).strip() != state_agg_org_id:
        raise LoginStateError(
            "当前共享登录态对应的企业与本次任务不一致。"
            f"登录态企业为 `{state_agg_org_id}`，本次任务企业为 `{agg_org_id}`。"
            "请先重新登录目标企业。"
        )

    return state


def apply_login_state_to_config(
    current_file: str | Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """将共享登录态绑定到工作流配置。"""

    if not isinstance(config, dict):
        raise LoginStateError("工作流配置必须是对象。")

    agg_org_id = config.get("aggOrgId")
    state = ensure_login_prerequisites(current_file, agg_org_id=str(agg_org_id))

    bound_config = dict(config)
    state_account_id = str(state["accountId"])
    config_account_id = bound_config.get("accountId")

    if config_account_id in (None, ""):
        bound_config["accountId"] = state_account_id
        return bound_config, state

    if str(config_account_id).strip() != state_account_id:
        raise LoginStateError(
            "配置中的 `accountId` 与当前共享登录态不一致。"
            f"配置值为 `{config_account_id}`，登录态值为 `{state_account_id}`。"
            "请先确认是否已登录正确的企业账号。"
        )

    bound_config["accountId"] = state_account_id
    return bound_config, state
