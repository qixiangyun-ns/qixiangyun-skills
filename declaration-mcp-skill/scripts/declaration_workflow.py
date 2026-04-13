#!/usr/bin/env python3
"""申报 Skill 的闭环编排脚本。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from filing_period import (
    ensure_current_filing_period,
    previous_month_range,
    resolve_filing_year_period,
    resolve_tax_period_range,
)
from login_state_support import LoginStateError, apply_login_state_to_config
from qxy_mcp_lib import (
    QXYMCPError,
    QXYWorkflowError,
    call_tool,
    extract_business_code,
    extract_message,
    extract_task_id,
    infer_task_state,
    is_retryable_response,
    merge_non_null,
    poll_tool,
    resolve_init_query_items,
    validate_workflow_config,
)
from rules_engine import (
    apply_accrual_rules,
    classify_tax_codes,
    get_tax_code_entry,
    get_tax_code_label,
    load_rule_sets,
    match_response_rule,
    validate_init_tax_codes,
)

LOGGER = logging.getLogger(__name__)

STEP_ORDER: tuple[str, ...] = (
    "fetch_roster",
    "init_data",
    "tax_report",
    "financial_report",
    "current_pdf",
    "history_pdf",
    "declare_info",
    "missing_check",
)

def _resolve_sample_year_period(
    year: int | None = None,
    period: int | None = None,
) -> tuple[int, int]:
    """解析样例配置使用的申报月份。"""

    return resolve_filing_year_period(year, period)


def _parse_iso_date(raw_value: Any, field_name: str) -> date:
    """解析 ISO 日期并输出明确错误。"""

    if not isinstance(raw_value, str) or not raw_value.strip():
        raise QXYWorkflowError(f"`{field_name}` 是必填字符串，格式必须为 YYYY-MM-DD。")
    try:
        return date.fromisoformat(raw_value.strip())
    except ValueError as exc:
        raise QXYWorkflowError(f"`{field_name}` 格式非法，必须为 YYYY-MM-DD。") from exc


def _normalize_period_cycle(raw_value: Any, field_name: str) -> str | None:
    """标准化初始化条目中的周期字段。"""

    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise QXYWorkflowError(f"`{field_name}` 必须是字符串。")
    normalized = raw_value.strip()
    allowed = {"monthly", "quarterly", "annual"}
    if normalized not in allowed:
        raise QXYWorkflowError(
            f"`{field_name}` 仅支持 `monthly`、`quarterly`、`annual`。"
        )
    return normalized


def _resolve_catalog_period_cycle(rule_sets: dict[str, Any], tax_code: str) -> str | None:
    """从税种目录读取默认周期。"""

    entry = get_tax_code_entry(rule_sets, tax_code)
    if not entry:
        return None
    raw_cycle = entry.get("period_cycle")
    if raw_cycle is None:
        return None
    return str(raw_cycle).strip() or None


def _has_meaningful_value(value: Any) -> bool:
    """判断配置节点是否包含有效业务内容。"""

    if value in (None, "", [], (), {}):
        return False
    if isinstance(value, dict):
        return any(_has_meaningful_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    return True


def _normalize_current_pdf_zsxm_list(step_cfg: dict[str, Any]) -> list[dict[str, str]]:
    """标准化当期 PDF 下载税种列表。"""

    raw_zsxm_list = step_cfg.get("zsxmList")
    if not isinstance(raw_zsxm_list, list) or not raw_zsxm_list:
        raise QXYWorkflowError("`current_pdf.zsxmList` 不能为空数组。")

    normalized_list: list[dict[str, str]] = []
    for index, raw_item in enumerate(raw_zsxm_list):
        if isinstance(raw_item, str):
            tax_code = raw_item.strip()
            if not tax_code:
                raise QXYWorkflowError(
                    f"`current_pdf.zsxmList[{index}]` 不能为空字符串。"
                )
            normalized_list.append({"yzpzzlDm": tax_code})
            continue
        if not isinstance(raw_item, dict):
            raise QXYWorkflowError(
                f"`current_pdf.zsxmList[{index}]` 必须是字符串或对象。"
            )
        tax_code = str(raw_item.get("yzpzzlDm") or "").strip()
        if not tax_code:
            raise QXYWorkflowError(
                f"`current_pdf.zsxmList[{index}].yzpzzlDm` 不能为空。"
            )
        normalized_list.append({"yzpzzlDm": tax_code})
    step_cfg["zsxmList"] = normalized_list
    return normalized_list


def _extract_init_task_ids(payload: Any) -> dict[str, str]:
    """从初始化发起结果中提取税种到任务 ID 的映射。"""

    task_map: dict[str, str] = {}
    if not isinstance(payload, dict):
        return task_map
    data = payload.get("data")
    if not isinstance(data, dict):
        return task_map
    task_ids = data.get("taskIds")
    if not isinstance(task_ids, list):
        return task_map
    for item in task_ids:
        if not isinstance(item, dict):
            continue
        tax_code = str(item.get("yzpzzlDm") or "").strip()
        task_id = str(item.get("taskId") or "").strip()
        if tax_code and task_id:
            task_map[tax_code] = task_id
    return task_map


def _resolve_init_data_zsxm_list(
    step_cfg: dict[str, Any],
    config: dict[str, Any],
    rule_sets: dict[str, Any],
) -> list[dict[str, Any]]:
    """为初始化税种条目补齐所属期范围。"""

    zsxm_list = step_cfg.get("zsxmList")
    if not isinstance(zsxm_list, list) or not zsxm_list:
        raise QXYWorkflowError("`init_data.zsxmList` 不能为空。")

    normalized_items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(zsxm_list):
        if not isinstance(raw_item, dict):
            raise QXYWorkflowError(f"`init_data.zsxmList[{index}]` 必须是对象。")
        normalized_item = dict(raw_item)
        tax_code = str(normalized_item.get("yzpzzlDm") or "").strip()
        if not tax_code:
            raise QXYWorkflowError(f"`init_data.zsxmList[{index}].yzpzzlDm` 不能为空。")
        normalized_item["yzpzzlDm"] = tax_code
        catalog_period_cycle = _resolve_catalog_period_cycle(rule_sets, tax_code)

        has_start = normalized_item.get("ssqQ") not in (None, "")
        has_end = normalized_item.get("ssqZ") not in (None, "")
        if has_start or has_end:
            if not (has_start and has_end):
                raise QXYWorkflowError(
                    f"`init_data.zsxmList[{index}]` 传了 `ssqQ` 或 `ssqZ` 时必须成对出现。"
                )
            start_date = _parse_iso_date(
                normalized_item.get("ssqQ"),
                f"init_data.zsxmList[{index}].ssqQ",
            )
            end_date = _parse_iso_date(
                normalized_item.get("ssqZ"),
                f"init_data.zsxmList[{index}].ssqZ",
            )
            if start_date > end_date:
                raise QXYWorkflowError(
                    f"`init_data.zsxmList[{index}]` 的 ssqQ 不能晚于 ssqZ。"
                )
            normalized_item["ssqQ"] = start_date.isoformat()
            normalized_item["ssqZ"] = end_date.isoformat()
            normalized_item.pop("period_cycle", None)
            normalized_items.append(normalized_item)
            continue

        period_cycle = _normalize_period_cycle(
            normalized_item.get("period_cycle"),
            f"init_data.zsxmList[{index}].period_cycle",
        )
        if (
            period_cycle is not None
            and catalog_period_cycle in {"monthly", "quarterly", "annual"}
            and period_cycle != catalog_period_cycle
        ):
            tax_label = get_tax_code_label(rule_sets, tax_code) or tax_code
            raise QXYWorkflowError(
                f"`init_data.zsxmList[{index}]` 的税种 {tax_label} "
                f"目录周期为 `{catalog_period_cycle}`，不能配置为 `{period_cycle}`。"
            )
        if period_cycle is None:
            period_cycle = catalog_period_cycle

        if period_cycle == "monthly_or_quarterly":
            tax_label = get_tax_code_label(rule_sets, tax_code) or tax_code
            raise QXYWorkflowError(
                f"`init_data.zsxmList[{index}]` 的税种 {tax_label} 可能是月报或季报，"
                "必须显式指定 `period_cycle` 或直接传所属期起止。"
            )
        if period_cycle is None:
            tax_label = get_tax_code_label(rule_sets, tax_code) or tax_code
            raise QXYWorkflowError(
                f"`init_data.zsxmList[{index}]` 的税种 {tax_label} 未配置默认周期，"
                "请直接传 `ssqQ`、`ssqZ`。"
            )

        try:
            start_date, end_date = resolve_tax_period_range(
                config["year"],
                config["period"],
                period_cycle,
            )
        except ValueError as exc:
            raise QXYWorkflowError(str(exc)) from exc
        normalized_item["ssqQ"] = start_date
        normalized_item["ssqZ"] = end_date
        normalized_item.pop("period_cycle", None)
        normalized_items.append(normalized_item)

    return normalized_items


def _ensure_current_filing_period(config: dict[str, Any], *, action: str) -> None:
    """校验当前步骤是否使用当前申报月份。"""

    try:
        ensure_current_filing_period(config["year"], config["period"], action=action)
    except ValueError as exc:
        raise QXYWorkflowError(str(exc)) from exc


def build_sample_config(year: int | None = None, period: int | None = None) -> dict[str, Any]:
    """生成示例配置。"""

    sample_year, sample_period = _resolve_sample_year_period(year, period)
    month_start, month_end = previous_month_range(sample_year, sample_period)
    return {
        "aggOrgId": "请替换为企业ID",
        "year": sample_year,
        "period": sample_period,
        "accountId": None,
        "poll_interval_seconds": 10,
        "max_poll_attempts": 30,
        "poll_strategy": {
            "short_interval_seconds": 10,
            "short_max_attempts": 30,
            "long_backoff_minutes": [30, 60, 120, 240, 300],
        },
        "checkpoint": {
            "enabled": True,
            "path": None,
            "resume_mode": "from_pending",
        },
        "rules": {
            "accrual_mode": "validate_and_suggest",
            "response_rule_set": "default",
            "tax_burden_enabled": False,
            "industry_code": None,
            "industry_name": None,
            "tax_burden_blocking": False,
            "allow_force_declare_on_4300": False,
        },
        "manual_review": {
            "enabled": True,
            "emit_customer_message": True,
        },
        "post_actions": {
            "auto_download_pdf": False,
            "auto_missing_check": False,
            "auto_prepare_payment": False,
        },
        "steps": {
            "fetch_roster": {
                "enabled": True,
                "poll_result": True,
            },
            "init_data": {
                "enabled": False,
                "zsxmList": [
                    {
                        "yzpzzlDm": "BDA0610606",
                        "period_cycle": "monthly",
                        "ssqQ": month_start,
                        "ssqZ": month_end,
                    }
                ],
                "query_after_start": True,
            },
            "tax_report": {
                "enabled": False,
                "tax_data": {},
                "tax_type": "ybData",
                "isDirectDeclare": True,
                "allowRepeatDeclare": False,
                "jrwc": None,
                "poll_result": True,
                "tax_label": "增值税",
            },
            "financial_report": {
                "enabled": False,
                "cbData": {},
                "cbnbData": None,
                "isDirectDeclare": True,
                "exAction": None,
                "duration": None,
                "jrwc": None,
                "gzDeclare": None,
                "poll_result": True,
                "tax_label": "财务报表",
            },
            "current_pdf": {
                "enabled": False,
                "zsxmList": [],
                "analysisPdf": "Y",
                "poll_result": True,
            },
            "history_pdf": {
                "enabled": False,
                "projectType": 1,
                "skssqq": month_start,
                "skssqz": month_end,
                "yzpzzlDms": [],
                "analysisPdf": "Y",
                "poll_result": True,
            },
            "declare_info": {
                "enabled": False,
                "poll_result": True,
            },
            "missing_check": {
                "enabled": False,
                "poll_result": True,
            },
        },
    }


def load_workflow_config(config_path: str | Path) -> dict[str, Any]:
    """加载并校验流程配置。"""

    path = Path(config_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file_obj:
        raw_config = json.load(file_obj)
    if not isinstance(raw_config, dict):
        raise ValueError("工作流配置必须是 JSON 对象。")
    return validate_workflow_config(raw_config)


def build_common_args(config: dict[str, Any]) -> dict[str, Any]:
    """构建多数步骤共用参数。"""

    return merge_non_null(
        {
            "aggOrgId": config["aggOrgId"],
            "year": config["year"],
            "period": config["period"],
        },
        {"accountId": config.get("accountId")},
    )


def _build_tax_report_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """构建税务申报参数。"""

    if "tax_data" not in step_cfg or not isinstance(step_cfg["tax_data"], dict):
        raise QXYWorkflowError("`tax_report.tax_data` 必须是对象。")
    return merge_non_null(
        build_common_args(config),
        {
            "tax_data": step_cfg["tax_data"],
            "tax_type": step_cfg.get("tax_type", "ybData"),
            "isDirectDeclare": step_cfg.get("isDirectDeclare", False),
            "allowRepeatDeclare": step_cfg.get("allowRepeatDeclare", False),
            "jrwc": step_cfg.get("jrwc"),
        },
    )


def _build_financial_report_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """构建财报申报参数。"""

    if "cbData" not in step_cfg or not isinstance(step_cfg["cbData"], dict):
        raise QXYWorkflowError("`financial_report.cbData` 必须是对象。")
    return merge_non_null(
        build_common_args(config),
        {
            "cbData": step_cfg.get("cbData"),
            "cbnbData": step_cfg.get("cbnbData"),
            "isDirectDeclare": step_cfg.get("isDirectDeclare", False),
            "exAction": step_cfg.get("exAction"),
            "duration": step_cfg.get("duration"),
            "jrwc": step_cfg.get("jrwc"),
            "gzDeclare": step_cfg.get("gzDeclare"),
        },
    )


def _build_current_pdf_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """构建当期 PDF 下载参数。"""

    zsxm_list = _normalize_current_pdf_zsxm_list(step_cfg)
    return merge_non_null(
        build_common_args(config),
        {
            "zsxmList": zsxm_list,
            "analysisPdf": step_cfg.get("analysisPdf"),
            "tdztswjgmc": step_cfg.get("tdztswjgmc"),
        },
    )


def _build_history_pdf_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """构建往期 PDF 下载参数。"""

    if not isinstance(step_cfg.get("yzpzzlDms"), list) or not step_cfg["yzpzzlDms"]:
        raise QXYWorkflowError("`history_pdf.yzpzzlDms` 不能为空数组。")
    if not isinstance(step_cfg.get("projectType"), int):
        raise QXYWorkflowError("`history_pdf.projectType` 必须是整数。")
    if not isinstance(step_cfg.get("skssqq"), str) or not step_cfg["skssqq"]:
        raise QXYWorkflowError("`history_pdf.skssqq` 是必填字符串。")
    if not isinstance(step_cfg.get("skssqz"), str) or not step_cfg["skssqz"]:
        raise QXYWorkflowError("`history_pdf.skssqz` 是必填字符串。")
    return merge_non_null(
        {"aggOrgId": config["aggOrgId"]},
        {
            "projectType": step_cfg.get("projectType"),
            "skssqq": step_cfg.get("skssqq"),
            "skssqz": step_cfg.get("skssqz"),
            "yzpzzlDms": step_cfg.get("yzpzzlDms"),
            "sbrqq": step_cfg.get("sbrqq"),
            "sbrqz": step_cfg.get("sbrqz"),
            "analysisPdf": step_cfg.get("analysisPdf"),
            "accountId": config.get("accountId"),
            "bsswjg": step_cfg.get("bsswjg"),
            "tdztswjgmc": step_cfg.get("tdztswjgmc"),
            "kqyswjgmc": step_cfg.get("kqyswjgmc"),
        },
    )


def _build_declare_info_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """构建申报信息查询参数。"""

    _ = step_cfg
    return build_common_args(config)


def _build_missing_check_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """构建漏报检查参数。"""

    _ = step_cfg
    return build_common_args(config)


STEP_DEFINITIONS: dict[str, dict[str, Any]] = {
    "fetch_roster": {
        "type": "async",
        "start_service": "roster_entry",
        "start_tool": "initiate_declaration_entry_task_auto",
        "query_service": "roster_entry",
        "query_tool": "query_roster_entry_task_auto",
        "payload_builder": lambda step_cfg, config: build_common_args(config),
    },
    "tax_report": {
        "type": "async",
        "start_service": "declaration_submission",
        "start_tool": "upload_tax_report_data_auto",
        "query_service": "declaration_submission",
        "query_tool": "query_upload_tax_report_result_auto",
        "payload_builder": _build_tax_report_args,
    },
    "financial_report": {
        "type": "async",
        "start_service": "declaration_submission",
        "start_tool": "upload_financial_report_data",
        "query_service": "declaration_submission",
        "query_tool": "query_upload_financial_report_result_auto",
        "payload_builder": _build_financial_report_args,
    },
    "current_pdf": {
        "type": "async",
        "start_service": "pdf_download",
        "start_tool": "load_pdf_task",
        "query_service": "pdf_download",
        "query_tool": "query_pdf_task_result_auto",
        "payload_builder": _build_current_pdf_args,
    },
    "history_pdf": {
        "type": "async",
        "start_service": "pdf_download",
        "start_tool": "load_wq_pdf_task",
        "query_service": "pdf_download",
        "query_tool": "query_pdf_task_result_auto",
        "payload_builder": _build_history_pdf_args,
    },
    "declare_info": {
        "type": "async",
        "start_service": "declaration_query",
        "start_tool": "load_declare_info_task",
        "query_service": "declaration_query",
        "query_tool": "query_declare_info_task_result_auto",
        "payload_builder": _build_declare_info_args,
    },
    "missing_check": {
        "type": "async",
        "start_service": "missing_declaration_check",
        "start_tool": "initiate_missing_declaration_check_task_auto",
        "query_service": "missing_declaration_check",
        "query_tool": "query_missing_declaration_check_task_auto",
        "payload_builder": _build_missing_check_args,
    },
}


def _default_checkpoint_path(config: dict[str, Any]) -> Path:
    """生成默认 checkpoint 文件路径。"""

    return (
        Path(__file__).resolve().parents[1]
        / ".checkpoints"
        / f"{config['aggOrgId']}-{config['year']}-{config['period']}.json"
    )


def _resolve_checkpoint_path(config: dict[str, Any]) -> Path:
    """解析 checkpoint 路径。"""

    raw_path = config.get("checkpoint", {}).get("path")
    if raw_path:
        return Path(str(raw_path)).expanduser().resolve()
    return _default_checkpoint_path(config).resolve()


def _utc_now() -> str:
    """返回当前 UTC 时间戳字符串。"""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_retry_at(minutes: int | None) -> str | None:
    """计算下次建议重试时间。"""

    if not minutes:
        return None
    return (
        datetime.now(UTC) + timedelta(minutes=minutes)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_step_config(config: dict[str, Any], step_name: str) -> dict[str, Any]:
    """获取并校验步骤配置。"""

    step_cfg = config.get("steps", {}).get(step_name, {})
    if not isinstance(step_cfg, dict):
        raise QXYWorkflowError(f"步骤 `{step_name}` 的配置必须是对象。")
    return step_cfg


def _determine_tax_label(step_name: str, step_cfg: dict[str, Any]) -> str:
    """确定展示用税种标签。"""

    if step_cfg.get("tax_label"):
        return str(step_cfg["tax_label"])
    mapping = {
        "tax_report": "增值税",
        "financial_report": "财务报表",
        "fetch_roster": "应申报清册",
        "init_data": "初始化",
        "current_pdf": "申报PDF",
        "history_pdf": "往期PDF",
        "declare_info": "申报信息",
        "missing_check": "漏报检查",
    }
    return mapping.get(step_name, step_name)


def _write_json(payload: Any, output_path: str | None = None) -> None:
    """输出 JSON 结果。"""

    if output_path:
        target_path = Path(output_path).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
            file_obj.write("\n")
        LOGGER.info("已写入 %s", target_path)
        return

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _write_error_payload(exc: Exception) -> None:
    """输出结构化错误，便于上层代理稳定解析。"""

    payload = {
        "success": False,
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
        },
    }
    _write_json(payload)


class WorkflowRunner:
    """负责执行闭环流程与补偿操作。"""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        checkpoint_path: Path | None = None,
        existing_context: dict[str, Any] | None = None,
    ) -> None:
        self.config = validate_workflow_config(config)
        self.rule_sets = load_rule_sets()
        self.checkpoint_path = (checkpoint_path or _resolve_checkpoint_path(self.config)).resolve()
        if existing_context is None:
            self.context = self._build_initial_context()
        else:
            self.context = existing_context
            self.context.setdefault("checkpoint_path", str(self.checkpoint_path))
            self.context.setdefault("steps", {})
            self.context.setdefault("artifacts", {})

    def _build_initial_context(self) -> dict[str, Any]:
        """构建流程初始上下文。"""

        config, login_state = apply_login_state_to_config(__file__, self.config)
        self.config = config
        return {
            "success": True,
            "workflow_state": "running",
            "current_step": None,
            "next_action": None,
            "requires_manual_review": False,
            "customer_visible": False,
            "customer_message": None,
            "payment_action": None,
            "pdf_action": None,
            "checkpoint_path": str(self.checkpoint_path),
            "aggOrgId": self.config["aggOrgId"],
            "accountId": self.config.get("accountId"),
            "year": self.config["year"],
            "period": self.config["period"],
            "login": {
                "aggOrgId": login_state["aggOrgId"],
                "accountId": login_state["accountId"],
                "source": login_state.get("source"),
            },
            "config": self.config,
            "steps": {},
            "artifacts": {
                "roster": None,
                "init_queries": [],
                "accrual_analysis": None,
            },
            "updated_at": _utc_now(),
        }

    def save_checkpoint(self) -> None:
        """保存 checkpoint。"""

        if not self.config.get("checkpoint", {}).get("enabled", True):
            return
        self.context["updated_at"] = _utc_now()
        self.context["config"] = self.config
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint_path.open("w", encoding="utf-8") as file_obj:
            json.dump(self.context, file_obj, ensure_ascii=False, indent=2)
            file_obj.write("\n")

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str | Path) -> "WorkflowRunner":
        """从 checkpoint 恢复运行器。"""

        path = Path(checkpoint_path).expanduser().resolve()
        try:
            with path.open("r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
        except json.JSONDecodeError as exc:
            raise QXYWorkflowError(f"checkpoint 文件损坏，无法解析：{path}") from exc
        if not isinstance(payload, dict):
            raise QXYWorkflowError(f"checkpoint 文件格式错误：{path}")
        config = payload.get("config")
        if not isinstance(config, dict):
            raise QXYWorkflowError("checkpoint 中缺少 config，无法恢复执行。")
        return cls(validate_workflow_config(config), checkpoint_path=path, existing_context=payload)

    def _base_step_result(self, step_name: str, step_cfg: dict[str, Any]) -> dict[str, Any]:
        """生成单步结果骨架。"""

        return {
            "step_name": step_name,
            "service": None,
            "tool": None,
            "request_payload": None,
            "taskId": None,
            "taskIds": None,
            "poll_state": "idle",
            "attempt_count": 0,
            "next_retry_at": None,
            "normalized_status": "pending",
            "business_code": None,
            "business_message": None,
            "retryable": False,
            "rule_match_id": None,
            "operator_advice": None,
            "customer_visible": False,
            "customer_message": None,
            "payment_action": None,
            "pdf_action": None,
            "raw_response": None,
            "start": None,
            "query": None,
            "finalized_at": None,
            "enabled": bool(step_cfg.get("enabled", False)),
        }

    def _update_top_level_from_step(self, step_result: dict[str, Any], *, workflow_state: str | None = None) -> None:
        """根据步骤结果刷新顶层状态。"""

        self.context["current_step"] = step_result["step_name"]
        if workflow_state:
            self.context["workflow_state"] = workflow_state
        else:
            self.context["workflow_state"] = step_result["normalized_status"]
        self.context["requires_manual_review"] = (
            self.context["requires_manual_review"]
            or step_result["normalized_status"] == "manual_review_required"
        )
        self.context["customer_visible"] = bool(step_result.get("customer_visible"))
        self.context["customer_message"] = step_result.get("customer_message")
        self.context["payment_action"] = step_result.get("payment_action")
        self.context["pdf_action"] = step_result.get("pdf_action")
        self.context["next_action"] = (
            "resume"
            if step_result["normalized_status"] in {"pending", "timeout"}
            else step_result.get("payment_action")
            or step_result.get("pdf_action")
        )

    def _interpret_payload(
        self,
        *,
        step_name: str,
        step_cfg: dict[str, Any],
        payload: Any,
        fallback_state: str | None = None,
    ) -> dict[str, Any]:
        """统一解释业务返回。"""

        tax_label = _determine_tax_label(step_name, step_cfg)
        matched = match_response_rule(
            payload=payload,
            step_name=step_name,
            step_cfg=step_cfg,
            config=self.config,
            rule_sets=self.rule_sets,
            tax_label=tax_label,
        )
        normalized_status = matched["normalized_status"]
        if normalized_status == "unknown":
            normalized_status = fallback_state or infer_task_state(payload)
        return {
            "normalized_status": normalized_status,
            "business_code": matched["business_code"] or extract_business_code(payload),
            "business_message": matched["business_message"] or extract_message(payload),
            "retryable": bool(matched["retryable"]) or is_retryable_response(payload),
            "rule_match_id": matched["rule_match_id"],
            "operator_advice": matched["operator_advice"],
            "customer_visible": matched["customer_visible"],
            "customer_message": matched["customer_message"],
            "payment_action": matched["payment_action"],
            "pdf_action": matched["pdf_action"],
            "raw_response": payload,
        }

    def _run_poll(
        self,
        *,
        step_name: str,
        service: str,
        tool: str,
        agg_org_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        """执行统一轮询。"""

        strategy = self.config["poll_strategy"]
        return poll_tool(
            service,
            tool,
            {"aggOrgId": agg_org_id, "taskId": task_id},
            interval_seconds=self.config["poll_interval_seconds"],
            max_attempts=self.config["max_poll_attempts"],
            short_interval_seconds=int(strategy["short_interval_seconds"]),
            short_max_attempts=int(strategy["short_max_attempts"]),
            long_backoff_minutes=list(strategy["long_backoff_minutes"]),
            log_context={"taskId": task_id},
        )

    def _apply_accrual_before_tax_report(self, step_cfg: dict[str, Any]) -> dict[str, Any] | None:
        """在税务申报前应用计提规则。"""

        init_queries = self.context.get("artifacts", {}).get("init_queries", [])
        analysis = apply_accrual_rules(
            config=self.config,
            init_queries=init_queries,
            tax_data=step_cfg.get("tax_data"),
            financial_data=self.config.get("steps", {}).get("financial_report", {}).get("cbData"),
            rule_sets=self.rule_sets,
        )
        self.context["artifacts"]["accrual_analysis"] = analysis
        if analysis.get("matched") and self.config.get("rules", {}).get("accrual_mode") == "auto_patch_payload":
            step_cfg["tax_data"] = analysis["patched_tax_data"]
        tax_burden = analysis.get("tax_burden", {})
        if (
            self.config.get("rules", {}).get("tax_burden_enabled")
            and tax_burden.get("blocked")
        ):
            raise QXYWorkflowError(
                "行业税负率校验未通过，已阻断申报："
                f"{tax_burden.get('industry_name')} 实际税负率 {tax_burden.get('actual_rate')}%"
            )
        return analysis

    def _run_fetch_roster(self, step_cfg: dict[str, Any], phase: str) -> dict[str, Any]:
        """执行获取应申报清册。"""

        step_result = self._base_step_result("fetch_roster", step_cfg)
        if not step_cfg.get("enabled", False):
            step_result["normalized_status"] = "skipped"
            return step_result
        _ensure_current_filing_period(self.config, action="获取应申报清册")

        definition = STEP_DEFINITIONS["fetch_roster"]
        payload = definition["payload_builder"](step_cfg, self.config)
        step_result["service"] = definition["start_service"]
        step_result["tool"] = definition["start_tool"]
        step_result["request_payload"] = payload

        existing = self.context["steps"].get("fetch_roster", {})
        task_id = existing.get("taskId")
        if phase != "query":
            start_result = call_tool(definition["start_service"], definition["start_tool"], payload)
            step_result["start"] = start_result
            step_result["raw_response"] = start_result
            task_id = extract_task_id(start_result) or task_id
        if not task_id:
            raise QXYWorkflowError("获取应申报清册已发起，但未能从响应中提取 taskId。")

        step_result["taskId"] = task_id
        if phase == "start" or not step_cfg.get("poll_result", True):
            step_result["poll_state"] = "started"
            step_result["normalized_status"] = "pending"
            step_result["next_retry_at"] = _next_retry_at(self.config["poll_strategy"]["long_backoff_minutes"][0])
            return step_result

        query_result = self._run_poll(
            step_name="fetch_roster",
            service=definition["query_service"],
            tool=definition["query_tool"],
            agg_org_id=self.config["aggOrgId"],
            task_id=task_id,
        )
        step_result["query"] = query_result
        step_result["attempt_count"] = int(query_result["attempts"])
        step_result["poll_state"] = str(query_result["state"])
        step_result["next_retry_at"] = _next_retry_at(query_result.get("next_retry_after_minutes"))
        interpreted = self._interpret_payload(
            step_name="fetch_roster",
            step_cfg=step_cfg,
            payload=query_result["result"],
            fallback_state=query_result["state"],
        )
        step_result.update(interpreted)

        if step_result["normalized_status"] == "success":
            self.context["artifacts"]["roster"] = query_result["result"]
            detail_list: list[dict[str, Any]] = []
            if isinstance(query_result["result"], dict):
                data = query_result["result"].get("data")
                if isinstance(data, dict) and isinstance(data.get("detail"), list):
                    detail_list = [item for item in data["detail"] if isinstance(item, dict)]
            tax_codes = [str(item.get("yzpzzlDm")) for item in detail_list if item.get("yzpzzlDm")]
            step_result["tax_code_catalog_matches"] = classify_tax_codes(self.rule_sets, tax_codes)
        return step_result

    def _run_init_data(self, step_cfg: dict[str, Any], phase: str) -> dict[str, Any]:
        """发起并查询初始化数据。"""

        step_result = self._base_step_result("init_data", step_cfg)
        if not step_cfg.get("enabled", False):
            step_result["normalized_status"] = "skipped"
            return step_result
        _ensure_current_filing_period(self.config, action="初始化申报数据")

        raw_zsxm_list = step_cfg.get("zsxmList")
        unsupported_messages = validate_init_tax_codes(
            self.rule_sets,
            raw_zsxm_list if isinstance(raw_zsxm_list, list) else [],
        )
        if unsupported_messages:
            raise QXYWorkflowError("检测到当前不支持初始化的税种：" + "；".join(unsupported_messages))
        zsxm_list = _resolve_init_data_zsxm_list(step_cfg, self.config, self.rule_sets)
        step_cfg["zsxmList"] = zsxm_list

        start_args = merge_non_null(build_common_args(self.config), {"zsxmList": zsxm_list})
        step_result["service"] = "initialize_data"
        step_result["tool"] = "load_init_data_task"
        step_result["request_payload"] = start_args
        previous_state = self.context["steps"].get("init_data", {})
        task_map = _extract_init_task_ids(previous_state.get("start"))

        if phase != "query":
            LOGGER.info(
                "步骤 `init_data` 发起初始化，请求税种数=%s",
                len(zsxm_list),
            )
            start_result = call_tool("initialize_data", "load_init_data_task", start_args)
            step_result["start"] = start_result
            step_result["raw_response"] = start_result
            task_map = _extract_init_task_ids(start_result)
            step_result["taskIds"] = task_map or None
            task_id = extract_task_id(start_result)
            step_result["taskId"] = task_id
            LOGGER.info(
                "步骤 `init_data` 发起完成，taskId=%s，税种任务数=%s，business_code=%s，message=%s",
                task_id or "-",
                len(task_map),
                extract_business_code(start_result) or "-",
                extract_message(start_result) or "-",
            )
            if not task_id:
                interpreted = self._interpret_payload(
                    step_name="init_data",
                    step_cfg=step_cfg,
                    payload=start_result,
                    fallback_state=infer_task_state(start_result),
                )
                if interpreted["normalized_status"] in {"failed", "manual_review_required"}:
                    step_result.update(interpreted)
                    step_result["poll_state"] = step_result["normalized_status"]
                    step_result["finalized_at"] = _utc_now()
                    return step_result

        if phase == "start" or not step_cfg.get("query_after_start", True):
            step_result["poll_state"] = "started"
            step_result["normalized_status"] = "pending"
            step_result["next_retry_at"] = _next_retry_at(self.config["poll_strategy"]["long_backoff_minutes"][0])
            return step_result

        query_results: list[dict[str, Any]] = []
        pending_retry_minutes: list[int] = []
        overall_status = "success"
        for item in resolve_init_query_items(step_cfg):
            query_args = merge_non_null(build_common_args(self.config), item)
            task_id = task_map.get(item["yzpzzlDm"])
            LOGGER.info(
                "步骤 `init_data` 开始查询税种 yzpzzlDm=%s taskId=%s",
                item["yzpzzlDm"],
                task_id or "-",
            )
            query_result = poll_tool(
                "initialize_data",
                "get_init_data",
                query_args,
                interval_seconds=self.config["poll_interval_seconds"],
                max_attempts=self.config["max_poll_attempts"],
                short_interval_seconds=int(self.config["poll_strategy"]["short_interval_seconds"]),
                short_max_attempts=int(self.config["poll_strategy"]["short_max_attempts"]),
                long_backoff_minutes=list(self.config["poll_strategy"]["long_backoff_minutes"]),
                log_context={
                    "taskId": task_id or "-",
                    "yzpzzlDm": item["yzpzzlDm"],
                },
            )
            interpreted = self._interpret_payload(
                step_name="init_data",
                step_cfg=step_cfg,
                payload=query_result["result"],
                fallback_state=str(query_result["state"]),
            )
            item_status = interpreted["normalized_status"]
            if item_status == "manual_review_required":
                overall_status = "manual_review_required"
            elif item_status == "failed" and overall_status != "manual_review_required":
                overall_status = "failed"
            elif item_status in {"pending", "timeout"} and overall_status == "success":
                overall_status = "pending"
            retry_after_minutes = query_result.get("next_retry_after_minutes")
            if isinstance(retry_after_minutes, int) and retry_after_minutes > 0:
                pending_retry_minutes.append(retry_after_minutes)
            query_results.append(
                {
                    "yzpzzlDm": item["yzpzzlDm"],
                    "taskId": task_id,
                    "tax_label": get_tax_code_label(self.rule_sets, item["yzpzzlDm"]) or item["yzpzzlDm"],
                    "catalog": classify_tax_codes(self.rule_sets, [item["yzpzzlDm"]])[0],
                    "alreadyFiled": interpreted["business_code"] in {"2002", "4601"}
                    or "已申报" in interpreted["business_message"],
                    "supported": "当前不支持操作" not in interpreted["business_message"]
                    and "敬请期待" not in interpreted["business_message"],
                    "businessHint": (
                        "当前所属期已申报，如需调整，请走申报更正与作废。"
                        if "已申报" in interpreted["business_message"]
                        else None
                    ),
                    "normalized_status": item_status,
                    "poll_state": str(query_result["state"]),
                    "attempt_count": int(query_result["attempts"]),
                    "result": query_result["result"],
                    "poll_history": query_result.get("history"),
                }
            )
        step_result["query"] = {
            "state": overall_status,
            "attempts": sum(int(item["attempt_count"]) for item in query_results),
            "result": query_results,
        }
        step_result["attempt_count"] = step_result["query"]["attempts"]
        step_result["poll_state"] = overall_status
        step_result["normalized_status"] = overall_status
        step_result["business_code"] = next(
            (
                extract_business_code(item["result"])
                for item in query_results
                if extract_business_code(item["result"])
            ),
            None,
        )
        step_result["business_message"] = (
            "初始化完成"
            if overall_status == "success"
            else (
                "初始化任务仍在执行中"
                if overall_status == "pending"
                else next(
                    (
                        extract_message(item["result"])
                        for item in query_results
                        if extract_message(item["result"])
                    ),
                    "初始化查询失败",
                )
            )
        )
        step_result["raw_response"] = query_results
        self.context["artifacts"]["init_queries"] = query_results
        if pending_retry_minutes:
            step_result["next_retry_at"] = _next_retry_at(min(pending_retry_minutes))
        if overall_status == "pending":
            return step_result
        if overall_status in {"failed", "manual_review_required"}:
            step_result["finalized_at"] = _utc_now()
            return step_result

        analysis = apply_accrual_rules(
            config=self.config,
            init_queries=query_results,
            tax_data=self.config.get("steps", {}).get("tax_report", {}).get("tax_data"),
            financial_data=self.config.get("steps", {}).get("financial_report", {}).get("cbData"),
            rule_sets=self.rule_sets,
        )
        self.context["artifacts"]["accrual_analysis"] = analysis
        step_result["accrual_analysis"] = analysis
        tax_burden = analysis.get("tax_burden", {})
        if self.config["rules"]["tax_burden_enabled"] and tax_burden.get("blocked"):
            step_result["normalized_status"] = "manual_review_required"
            step_result["operator_advice"] = (
                "行业税负率超阈值，已阻断自动申报。"
                f" 实际税负率 {tax_burden.get('actual_rate')}%，行业区间 {tax_burden.get('min_rate')}%-{tax_burden.get('max_rate')}%。"
            )
        step_result["finalized_at"] = _utc_now()
        return step_result

    def _run_async_step(self, step_name: str, step_cfg: dict[str, Any], phase: str) -> dict[str, Any]:
        """执行标准异步步骤。"""

        step_result = self._base_step_result(step_name, step_cfg)
        if not step_cfg.get("enabled", False):
            step_result["normalized_status"] = "skipped"
            return step_result

        definition = STEP_DEFINITIONS[step_name]
        payload_builder: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] = definition["payload_builder"]
        if step_name == "tax_report":
            self._apply_accrual_before_tax_report(step_cfg)
        if (
            step_name == "financial_report"
            and not _has_meaningful_value(step_cfg.get("cbData"))
            and not _has_meaningful_value(step_cfg.get("cbnbData"))
        ):
            LOGGER.warning("financial_report: cbData/cbnbData 为空，跳过财报申报。")
            step_result["normalized_status"] = "skipped"
            step_result["poll_state"] = "skipped"
            step_result["business_message"] = "cbData/cbnbData 为空，已跳过财报申报。"
            step_result["operator_advice"] = "如需申报财务报表，请先补充 cbData 或 cbnbData。"
            step_result["finalized_at"] = _utc_now()
            return step_result

        payload = payload_builder(step_cfg, self.config)
        step_result["service"] = definition["start_service"]
        step_result["tool"] = definition["start_tool"]
        step_result["request_payload"] = payload

        previous_state = self.context["steps"].get(step_name, {})
        task_id = previous_state.get("taskId")
        start_result = None
        if phase != "query":
            LOGGER.info(
                "步骤 `%s` 发起任务，service=%s tool=%s",
                step_name,
                definition["start_service"],
                definition["start_tool"],
            )
            start_result = call_tool(definition["start_service"], definition["start_tool"], payload)
            step_result["start"] = start_result
            step_result["raw_response"] = start_result
            task_id = extract_task_id(start_result) or task_id
            LOGGER.info(
                "步骤 `%s` 发起完成，taskId=%s，business_code=%s，message=%s",
                step_name,
                task_id or "-",
                extract_business_code(start_result) or "-",
                extract_message(start_result) or "-",
            )

            if not task_id:
                interpreted = self._interpret_payload(
                    step_name=step_name,
                    step_cfg=step_cfg,
                    payload=start_result,
                    fallback_state=infer_task_state(start_result),
                )
                step_result.update(interpreted)
                step_result["poll_state"] = step_result["normalized_status"]
                step_result["finalized_at"] = _utc_now()
                return step_result

        if not task_id:
            raise QXYWorkflowError(f"步骤 `{step_name}` 已发起，但未能从响应中提取 taskId。")

        step_result["taskId"] = task_id
        if phase == "start" or not step_cfg.get("poll_result", True):
            step_result["poll_state"] = "started"
            step_result["normalized_status"] = "pending"
            step_result["next_retry_at"] = _next_retry_at(self.config["poll_strategy"]["long_backoff_minutes"][0])
            return step_result

        LOGGER.info("步骤 `%s` 开始轮询，taskId=%s", step_name, task_id)
        query_result = self._run_poll(
            step_name=step_name,
            service=definition["query_service"],
            tool=definition["query_tool"],
            agg_org_id=self.config["aggOrgId"],
            task_id=task_id,
        )
        step_result["query"] = query_result
        step_result["attempt_count"] = int(query_result["attempts"])
        step_result["poll_state"] = str(query_result["state"])
        step_result["next_retry_at"] = _next_retry_at(query_result.get("next_retry_after_minutes"))
        interpreted = self._interpret_payload(
            step_name=step_name,
            step_cfg=step_cfg,
            payload=query_result["result"],
            fallback_state=query_result["state"],
        )
        step_result.update(interpreted)
        step_result["finalized_at"] = _utc_now()
        return step_result

    def execute_step(self, step_name: str, *, phase: str = "run") -> dict[str, Any]:
        """执行单个步骤。"""

        if step_name not in STEP_ORDER:
            raise QXYWorkflowError(f"未知步骤 `{step_name}`。可选步骤：{', '.join(STEP_ORDER)}")
        step_cfg = _safe_step_config(self.config, step_name)
        LOGGER.info("开始执行步骤 `%s`，phase=%s", step_name, phase)
        if step_name == "fetch_roster":
            step_result = self._run_fetch_roster(step_cfg, phase)
        elif step_name == "init_data":
            step_result = self._run_init_data(step_cfg, phase)
        else:
            step_result = self._run_async_step(step_name, step_cfg, phase)

        self.context["steps"][step_name] = step_result
        self._update_top_level_from_step(step_result)
        self.save_checkpoint()
        return step_result

    def run(self, *, only_steps: set[str] | None = None, resume: bool = False) -> dict[str, Any]:
        """按固定顺序执行申报闭环。"""

        selected_steps = only_steps or set(STEP_ORDER)
        resume_mode = str(self.config.get("checkpoint", {}).get("resume_mode", "from_pending"))
        for step_name in STEP_ORDER:
            if step_name not in selected_steps:
                continue
            existing = self.context["steps"].get(step_name, {})
            if resume:
                existing_status = str(existing.get("normalized_status") or "")
                if existing_status in {"success", "failed", "manual_review_required", "skipped"}:
                    if resume_mode == "rerun_failed" and existing_status == "failed":
                        step_result = self.execute_step(step_name, phase="run")
                    else:
                        LOGGER.info(
                            "步骤 `%s` 已是终态 `%s`，resume_mode=%s，跳过。",
                            step_name,
                            existing_status,
                            resume_mode,
                        )
                        continue
                elif existing_status in {"pending", "timeout"} or existing.get("taskId"):
                    step_result = self.execute_step(step_name, phase="query")
                else:
                    step_result = self.execute_step(step_name, phase="run")
            else:
                step_result = self.execute_step(step_name, phase="run")

            if step_result["normalized_status"] in {"pending", "timeout"}:
                self.context["workflow_state"] = "pending"
                self.context["next_action"] = "resume"
                break
            if step_result["normalized_status"] == "manual_review_required":
                self.context["workflow_state"] = "manual_review_required"
                self.context["requires_manual_review"] = True
                break
            if step_result["normalized_status"] == "failed":
                self.context["workflow_state"] = "failed"
                self.context["success"] = False
                break
        else:
            if self.context["workflow_state"] in {"running", "failed"}:
                self.context["workflow_state"] = "success"
                self.context["current_step"] = next(
                    (name for name in reversed(STEP_ORDER) if name in self.context["steps"]),
                    None,
                )

        self.save_checkpoint()
        return self.context


def run_workflow(config: dict[str, Any], only_steps: set[str] | None = None) -> dict[str, Any]:
    """保留原有函数入口，执行完整工作流。"""

    runner = WorkflowRunner(config)
    return runner.run(only_steps=only_steps)


def run_init_data(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """兼容旧测试与单步调用入口。"""

    _ensure_current_filing_period(config, action="初始化申报数据")
    rule_sets = load_rule_sets()
    raw_zsxm_list = step_cfg.get("zsxmList")
    unsupported_messages = validate_init_tax_codes(
        rule_sets,
        raw_zsxm_list if isinstance(raw_zsxm_list, list) else [],
    )
    if unsupported_messages:
        raise QXYWorkflowError("检测到当前不支持初始化的税种：" + "；".join(unsupported_messages))
    zsxm_list = _resolve_init_data_zsxm_list(step_cfg, config, rule_sets)
    step_cfg["zsxmList"] = zsxm_list
    runner = WorkflowRunner(config)
    return runner._run_init_data(step_cfg, "run")


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(description="企享云申报闭环编排脚本（`period` 表示申报月份）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold-config", help="生成配置样例")
    scaffold_parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")
    scaffold_parser.add_argument("--year", type=int, help="申报年份；默认使用当前年份")
    scaffold_parser.add_argument("--period", type=int, help="申报月份；默认使用当前月份")

    run_parser = subparsers.add_parser("run", help="执行申报闭环")
    run_parser.add_argument("--config", required=True, help="工作流配置 JSON 文件")
    run_parser.add_argument("--checkpoint", help="显式指定 checkpoint 文件")
    run_parser.add_argument("--steps", help="只运行指定步骤，使用逗号分隔")

    resume_parser = subparsers.add_parser("resume", help="从 checkpoint 继续执行")
    resume_parser.add_argument("--checkpoint", required=True, help="checkpoint 文件路径")
    resume_parser.add_argument("--steps", help="只恢复指定步骤，使用逗号分隔")

    run_step_parser = subparsers.add_parser("run-step", help="执行单个步骤")
    run_step_parser.add_argument("--config", required=True, help="工作流配置 JSON 文件")
    run_step_parser.add_argument("--checkpoint", help="checkpoint 文件路径")
    run_step_parser.add_argument("--step", required=True, choices=STEP_ORDER, help="步骤名")
    run_step_parser.add_argument(
        "--phase",
        choices=("run", "start", "query"),
        default="run",
        help="执行阶段：run=发起并查询，start=仅发起，query=仅查询",
    )

    query_step_parser = subparsers.add_parser("query-step", help="仅查询单个已存在任务")
    query_step_parser.add_argument("--config", help="工作流配置 JSON 文件")
    query_step_parser.add_argument("--checkpoint", help="checkpoint 文件路径")
    query_step_parser.add_argument("--step", required=True, choices=STEP_ORDER, help="步骤名")
    return parser


def _parse_steps(raw_steps: str | None) -> set[str] | None:
    """解析步骤列表。"""

    if not raw_steps:
        return None
    return {item.strip() for item in raw_steps.split(",") if item.strip()}


def main() -> int:
    """CLI 入口。"""

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "scaffold-config":
            _write_json(build_sample_config(args.year, args.period), args.output)
            return 0

        if args.command == "run":
            config = load_workflow_config(args.config)
            runner = WorkflowRunner(
                config,
                checkpoint_path=Path(args.checkpoint).expanduser().resolve() if args.checkpoint else None,
            )
            _write_json(runner.run(only_steps=_parse_steps(args.steps)))
            return 0

        if args.command == "resume":
            runner = WorkflowRunner.from_checkpoint(args.checkpoint)
            _write_json(runner.run(only_steps=_parse_steps(args.steps), resume=True))
            return 0

        if args.command == "run-step":
            config = load_workflow_config(args.config)
            runner = WorkflowRunner(
                config,
                checkpoint_path=Path(args.checkpoint).expanduser().resolve() if args.checkpoint else None,
            )
            step_result = runner.execute_step(args.step, phase=args.phase)
            _write_json(
                {
                    "success": step_result["normalized_status"] not in {"failed"},
                    "workflow_state": runner.context["workflow_state"],
                    "checkpoint_path": runner.context["checkpoint_path"],
                    "step": step_result,
                }
            )
            return 0

        if args.command == "query-step":
            if args.checkpoint:
                runner = WorkflowRunner.from_checkpoint(args.checkpoint)
            elif args.config:
                config = load_workflow_config(args.config)
                runner = WorkflowRunner(config)
            else:
                raise QXYWorkflowError("`query-step` 需要提供 `--checkpoint` 或 `--config`。")
            step_result = runner.execute_step(args.step, phase="query")
            _write_json(
                {
                    "success": step_result["normalized_status"] not in {"failed"},
                    "workflow_state": runner.context["workflow_state"],
                    "checkpoint_path": runner.context["checkpoint_path"],
                    "step": step_result,
                }
            )
            return 0

        parser.print_help()
        return 1
    except (
        LoginStateError,
        QXYMCPError,
        QXYWorkflowError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _write_error_payload(exc)
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
