#!/usr/bin/env python3
"""企业级申报编排脚本。"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from filing_period import ensure_current_filing_period, previous_month_range, resolve_tax_period_range
from login_state_support import LoginStateError, ensure_login_prerequisites
from qxy_mcp_lib import (
    QXYMCPError,
    QXYWorkflowError,
    call_tool,
    extract_business_code,
    extract_message,
    extract_task_id,
    extract_tax_amount,
    infer_task_state,
    merge_non_null,
    poll_tool,
)
from rules_engine import classify_tax_codes, get_tax_code_entry, get_tax_code_label, load_rule_sets

LOGGER = logging.getLogger(__name__)

SUPPORTED_FINANCIAL_CODES = {"CWBBSB", "CWBBNDSB", "CWBBJTHB"}
SUPPORTED_ZLBSXL_DMS = {"ZL1001001", "ZL1001002", "ZL1001003", "ZL1001050"}
SUPPORTED_TEMPLATE_CODES = {"1", "2"}
SCOPED_TAX_CODES = {"BDA0610606", "BDA0611159"} | SUPPORTED_FINANCIAL_CODES
TERMINAL_ENTERPRISE_STATUSES = {
    "awaiting_financial_report",
    "manual_review_required",
    "already_declared",
    "success",
    "failed",
}


def _utc_now() -> str:
    """返回当前 UTC 时间戳。"""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_retry_at(minutes: int | None) -> str | None:
    """计算建议重试时间。"""

    if not minutes:
        return None
    return (
        datetime.now(UTC) + timedelta(minutes=minutes)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(payload: Any, output_path: str | None = None) -> None:
    """输出 JSON。"""

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
    """输出结构化错误。"""

    payload = {
        "success": False,
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
        },
    }
    _write_json(payload)


def _parse_iso_date(raw_value: Any, field_name: str) -> date:
    """解析 ISO 日期。"""

    if not isinstance(raw_value, str) or not raw_value.strip():
        raise QXYWorkflowError(f"`{field_name}` 是必填字符串，格式必须为 YYYY-MM-DD。")
    try:
        return date.fromisoformat(raw_value.strip())
    except ValueError as exc:
        raise QXYWorkflowError(f"`{field_name}` 格式非法，必须为 YYYY-MM-DD。") from exc


def _normalize_required_string(raw_value: Any, field_name: str) -> str:
    """标准化必填字符串。"""

    if not isinstance(raw_value, str) or not raw_value.strip():
        raise QXYWorkflowError(f"`{field_name}` 不能为空字符串。")
    return raw_value.strip()


def _normalize_optional_string(raw_value: Any, field_name: str) -> str | None:
    """标准化可选字符串。"""

    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise QXYWorkflowError(f"`{field_name}` 必须是字符串或 null。")
    stripped = raw_value.strip()
    return stripped or None


def _safe_float(raw_value: Any) -> float | None:
    """安全转换数字。"""

    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    text = str(raw_value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_success_like(step_result: dict[str, Any] | None) -> bool:
    """判断步骤是否属于成功态。"""

    if not step_result:
        return False
    return str(step_result.get("normalized_status")) in {"success", "already_declared", "skipped"}


def _is_already_declared_payload(payload: Any) -> bool:
    """判断返回是否表示已申报。"""

    business_code = extract_business_code(payload)
    message = extract_message(payload)
    return business_code in {"2002", "4601"} or "已申报" in message


def _extract_detail_list(payload: Any) -> list[dict[str, Any]]:
    """提取常见 detail 列表。"""

    if not isinstance(payload, dict):
        return []
    candidates: list[Any] = []
    if isinstance(payload.get("detail"), list):
        candidates.append(payload.get("detail"))
    data = payload.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("detail"), list):
            candidates.append(data.get("detail"))
        if isinstance(data.get("records"), list):
            candidates.append(data.get("records"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _normalize_financial_report_input(raw_value: Any, field_name: str) -> dict[str, Any] | None:
    """标准化财报输入。"""

    if raw_value in (None, {}):
        return None
    if not isinstance(raw_value, dict):
        raise QXYWorkflowError(f"`{field_name}` 必须是对象。")

    mode = _normalize_required_string(raw_value.get("mode"), f"{field_name}.mode").lower()
    if mode not in {"excel", "json"}:
        raise QXYWorkflowError(f"`{field_name}.mode` 仅支持 `excel` 或 `json`。")

    normalized: dict[str, Any] = {"mode": mode, "isDirectDeclare": bool(raw_value.get("isDirectDeclare", True))}
    if mode == "excel":
        file_path = Path(_normalize_required_string(raw_value.get("file_path"), f"{field_name}.file_path")).expanduser().resolve()
        if not file_path.exists() or not file_path.is_file():
            raise QXYWorkflowError(f"`{field_name}.file_path` 对应文件不存在：{file_path}")
        yzpzzl_dm = _normalize_required_string(raw_value.get("yzpzzlDm"), f"{field_name}.yzpzzlDm").upper()
        if yzpzzl_dm not in SUPPORTED_FINANCIAL_CODES:
            raise QXYWorkflowError(
                f"`{field_name}.yzpzzlDm` 仅支持 {', '.join(sorted(SUPPORTED_FINANCIAL_CODES))}。"
            )
        ssq_q = _parse_iso_date(raw_value.get("ssqQ"), f"{field_name}.ssqQ")
        ssq_z = _parse_iso_date(raw_value.get("ssqZ"), f"{field_name}.ssqZ")
        if ssq_q > ssq_z:
            raise QXYWorkflowError(f"`{field_name}` 的 ssqQ 不能晚于 ssqZ。")
        zlbsxl_dm = _normalize_required_string(raw_value.get("zlbsxlDm"), f"{field_name}.zlbsxlDm").upper()
        if zlbsxl_dm not in SUPPORTED_ZLBSXL_DMS:
            raise QXYWorkflowError(
                f"`{field_name}.zlbsxlDm` 仅支持 {', '.join(sorted(SUPPORTED_ZLBSXL_DMS))}。"
            )
        template_code = _normalize_optional_string(raw_value.get("templateCode"), f"{field_name}.templateCode")
        if zlbsxl_dm == "ZL1001001":
            if template_code is None:
                raise QXYWorkflowError(f"`{field_name}.templateCode` 在 `ZL1001001` 场景下必填。")
            template_code = template_code.upper()
            if template_code not in SUPPORTED_TEMPLATE_CODES:
                raise QXYWorkflowError(f"`{field_name}.templateCode` 仅支持 `1` 或 `2`。")
        else:
            template_code = "0"
        normalized.update(
            {
                "file_path": str(file_path),
                "yzpzzlDm": yzpzzl_dm,
                "ssqQ": ssq_q.isoformat(),
                "ssqZ": ssq_z.isoformat(),
                "zlbsxlDm": zlbsxl_dm,
                "templateCode": template_code,
            }
        )
        return normalized

    cb_data = raw_value.get("cbData")
    cbnb_data = raw_value.get("cbnbData")
    if cb_data is None and cbnb_data is None:
        raise QXYWorkflowError(f"`{field_name}` 至少需要 `cbData` 或 `cbnbData` 其中之一。")
    if cb_data is not None and not isinstance(cb_data, dict):
        raise QXYWorkflowError(f"`{field_name}.cbData` 必须是对象。")
    if cbnb_data is not None and not isinstance(cbnb_data, dict):
        raise QXYWorkflowError(f"`{field_name}.cbnbData` 必须是对象。")
    normalized["cbData"] = cb_data
    normalized["cbnbData"] = cbnb_data
    return normalized


def _normalize_vat_adjustment(raw_value: Any, field_name: str) -> dict[str, Any]:
    """标准化增值税调整输入。"""

    if raw_value in (None, {}):
        return {"no_ticket_income_amount": 0.0}
    if not isinstance(raw_value, dict):
        raise QXYWorkflowError(f"`{field_name}` 必须是对象。")
    amount = _safe_float(raw_value.get("no_ticket_income_amount"))
    if amount is None:
        return {"no_ticket_income_amount": 0.0}
    if amount < 0:
        raise QXYWorkflowError(f"`{field_name}.no_ticket_income_amount` 不能为负数。")
    return {"no_ticket_income_amount": float(amount)}


def _normalize_single_enterprise(raw_value: Any, *, field_name: str) -> dict[str, Any]:
    """标准化单企业配置。"""

    if not isinstance(raw_value, dict):
        raise QXYWorkflowError(f"`{field_name}` 必须是对象。")

    agg_org_id = _normalize_required_string(raw_value.get("aggOrgId"), f"{field_name}.aggOrgId")
    year = raw_value.get("year")
    period = raw_value.get("period")
    if not isinstance(year, int):
        raise QXYWorkflowError(f"`{field_name}.year` 必须是整数。")
    if not isinstance(period, int) or not 1 <= period <= 12:
        raise QXYWorkflowError(f"`{field_name}.period` 必须在 1 到 12 之间，表示申报月份。")

    normalized = {
        "aggOrgId": agg_org_id,
        "year": year,
        "period": period,
        "accountId": _normalize_optional_string(raw_value.get("accountId"), f"{field_name}.accountId"),
        "display_name": _normalize_optional_string(raw_value.get("display_name"), f"{field_name}.display_name"),
        "financial_report_input": _normalize_financial_report_input(
            raw_value.get("financial_report_input"),
            f"{field_name}.financial_report_input",
        ),
        "vat_adjustment": _normalize_vat_adjustment(
            raw_value.get("vat_adjustment"),
            f"{field_name}.vat_adjustment",
        ),
    }
    return normalized


def validate_enterprise_filing_config(config: Any) -> dict[str, Any]:
    """校验并标准化企业级编排配置。"""

    if not isinstance(config, dict):
        raise QXYWorkflowError("工作流配置必须是对象。")

    checkpoint = config.get("checkpoint", {})
    if checkpoint not in ({}, None) and not isinstance(checkpoint, dict):
        raise QXYWorkflowError("`checkpoint` 必须是对象。")

    if "enterprises" in config:
        raw_enterprises = config.get("enterprises")
        if not isinstance(raw_enterprises, list) or not raw_enterprises:
            raise QXYWorkflowError("`enterprises` 不能为空数组。")
        enterprises = [
            _normalize_single_enterprise(item, field_name=f"enterprises[{index}]")
            for index, item in enumerate(raw_enterprises)
        ]
    else:
        enterprises = [_normalize_single_enterprise(config, field_name="config")]

    poll_interval_seconds = int(config.get("poll_interval_seconds", 10))
    max_poll_attempts = int(config.get("max_poll_attempts", 30))
    if poll_interval_seconds <= 0:
        raise QXYWorkflowError("`poll_interval_seconds` 必须大于 0。")
    if max_poll_attempts <= 0:
        raise QXYWorkflowError("`max_poll_attempts` 必须大于 0。")

    return {
        "poll_interval_seconds": poll_interval_seconds,
        "max_poll_attempts": max_poll_attempts,
        "checkpoint": {
            "enabled": bool((checkpoint or {}).get("enabled", True)),
            "path": (checkpoint or {}).get("path"),
        },
        "enterprises": enterprises,
    }


def build_sample_config(year: int | None = None, period: int | None = None) -> dict[str, Any]:
    """生成样例配置。"""

    today = date.today()
    sample_year = year or today.year
    sample_period = period or today.month
    month_start, month_end = previous_month_range(sample_year, sample_period)
    quarter_start, quarter_end = resolve_tax_period_range(sample_year, sample_period, "quarterly")
    return {
        "poll_interval_seconds": 10,
        "max_poll_attempts": 30,
        "checkpoint": {
            "enabled": True,
            "path": None,
        },
        "enterprises": [
            {
                "aggOrgId": "请替换为企业ID",
                "accountId": None,
                "display_name": "示例企业",
                "year": sample_year,
                "period": sample_period,
                "financial_report_input": {
                    "mode": "excel",
                    "file_path": "/tmp/example-financial-report.xlsx",
                    "yzpzzlDm": "CWBBSB",
                    "ssqQ": quarter_start,
                    "ssqZ": quarter_end,
                    "zlbsxlDm": "ZL1001003",
                    "templateCode": "0",
                    "isDirectDeclare": True,
                },
                "vat_adjustment": {
                    "no_ticket_income_amount": 0,
                },
            }
        ],
        "notes": {
            "vat_range_example": {"ssqQ": month_start, "ssqZ": month_end},
            "cit_range_example": {"ssqQ": quarter_start, "ssqZ": quarter_end},
        },
    }


def load_workflow_config(config_path: str | Path) -> dict[str, Any]:
    """加载配置文件。"""

    path = Path(config_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file_obj:
        raw_config = json.load(file_obj)
    return validate_enterprise_filing_config(raw_config)


def _default_batch_checkpoint_path(config: dict[str, Any]) -> Path:
    """生成批量 checkpoint 路径。"""

    enterprise_count = len(config["enterprises"])
    return (
        Path(__file__).resolve().parents[1]
        / ".enterprise-checkpoints"
        / f"batch-{enterprise_count}-enterprises.json"
    )


def _resolve_batch_checkpoint_path(config: dict[str, Any]) -> Path:
    """解析批量 checkpoint 路径。"""

    raw_path = config.get("checkpoint", {}).get("path")
    if raw_path:
        return Path(str(raw_path)).expanduser().resolve()
    return _default_batch_checkpoint_path(config)


def _enterprise_checkpoint_path(batch_checkpoint_path: Path, enterprise_config: dict[str, Any]) -> Path:
    """生成单企业 checkpoint 路径。"""

    return (
        batch_checkpoint_path.parent
        / f"{enterprise_config['aggOrgId']}-{enterprise_config['year']}-{enterprise_config['period']}.json"
    )


def _build_common_args(config: dict[str, Any]) -> dict[str, Any]:
    """构建公共参数。"""

    return merge_non_null(
        {
            "aggOrgId": config["aggOrgId"],
            "year": config["year"],
            "period": config["period"],
        },
        {"accountId": config.get("accountId")},
    )


class EnterpriseRunner:
    """单企业申报编排器。"""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        poll_interval_seconds: int,
        max_poll_attempts: int,
        checkpoint_path: Path,
        existing_context: dict[str, Any] | None = None,
    ) -> None:
        self.config = dict(config)
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts
        self.checkpoint_path = checkpoint_path.resolve()
        self.rule_sets = load_rule_sets()
        if existing_context is None:
            self.context = self._build_initial_context()
        else:
            self.context = existing_context
            self.context.setdefault("checkpoint_path", str(self.checkpoint_path))
            self.context.setdefault("steps", {})
            self.context.setdefault("tracked_periods", {})
            self.context.setdefault("successful_declarations", [])
            self.context.setdefault("payment_preparation", {"detail": [], "certificate": [], "payment_config": None})
            self.context.setdefault("operator_advice", [])

    def _build_initial_context(self) -> dict[str, Any]:
        """构建初始上下文。"""

        login_state = ensure_login_prerequisites(__file__)
        bound_config = dict(self.config)
        if bound_config.get("accountId") in (None, ""):
            bound_config["accountId"] = str(login_state["accountId"])
        self.config = bound_config
        return {
            "success": True,
            "status": "running",
            "aggOrgId": self.config["aggOrgId"],
            "accountId": self.config.get("accountId"),
            "display_name": self.config.get("display_name"),
            "year": self.config["year"],
            "period": self.config["period"],
            "login": {
                "aggOrgId": login_state["aggOrgId"],
                "accountId": login_state["accountId"],
                "source": login_state.get("source"),
            },
            "config": self.config,
            "checkpoint_path": str(self.checkpoint_path),
            "steps": {},
            "roster": None,
            "financial_report": None,
            "income_tax": None,
            "vat": None,
            "declare_info": None,
            "pdfs": None,
            "tracked_periods": {},
            "successful_declarations": [],
            "payment_preparation": {"detail": [], "certificate": [], "payment_config": None},
            "operator_advice": [],
            "updated_at": _utc_now(),
        }

    def save_checkpoint(self) -> None:
        """保存 checkpoint。"""

        self.context["updated_at"] = _utc_now()
        self.context["config"] = self.config
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint_path.open("w", encoding="utf-8") as file_obj:
            json.dump(self.context, file_obj, ensure_ascii=False, indent=2)
            file_obj.write("\n")

    def _base_step_result(self, step_name: str) -> dict[str, Any]:
        """构造步骤骨架。"""

        return {
            "step_name": step_name,
            "service": None,
            "tool": None,
            "request_payload": None,
            "taskId": None,
            "normalized_status": "pending",
            "poll_state": "idle",
            "attempt_count": 0,
            "next_retry_at": None,
            "business_code": None,
            "business_message": None,
            "raw_response": None,
            "start": None,
            "query": None,
            "finalized_at": None,
        }

    def _record_step(self, key: str, step_result: dict[str, Any]) -> dict[str, Any]:
        """记录步骤结果。"""

        self.context["steps"][key] = step_result
        self.save_checkpoint()
        return step_result

    def _run_async_step(
        self,
        *,
        step_name: str,
        service: str,
        start_tool: str,
        query_tool: str,
        payload: dict[str, Any],
        log_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行通用异步步骤。"""

        step_result = self._base_step_result(step_name)
        step_result["service"] = service
        step_result["tool"] = start_tool
        step_result["request_payload"] = payload

        LOGGER.info(
            "企业 %s 步骤 `%s` 发起，service=%s tool=%s",
            self.config["aggOrgId"],
            step_name,
            service,
            start_tool,
        )
        start_result = call_tool(service, start_tool, payload)
        task_id = extract_task_id(start_result)
        step_result["start"] = start_result
        step_result["raw_response"] = start_result
        step_result["taskId"] = task_id
        step_result["business_code"] = extract_business_code(start_result) or None
        step_result["business_message"] = extract_message(start_result) or None

        if not task_id:
            normalized_status = infer_task_state(start_result)
            if _is_already_declared_payload(start_result):
                normalized_status = "already_declared"
            step_result["normalized_status"] = normalized_status
            step_result["poll_state"] = normalized_status
            step_result["finalized_at"] = _utc_now()
            return step_result

        query_result = poll_tool(
            service,
            query_tool,
            {"aggOrgId": self.config["aggOrgId"], "taskId": task_id},
            interval_seconds=self.poll_interval_seconds,
            max_attempts=self.max_poll_attempts,
            short_interval_seconds=self.poll_interval_seconds,
            short_max_attempts=self.max_poll_attempts,
            long_backoff_minutes=[30, 60, 120, 240, 300],
            log_context=merge_non_null({"taskId": task_id}, log_context or {}),
        )
        normalized_status = infer_task_state(query_result["result"])
        if normalized_status == "unknown":
            normalized_status = str(query_result["state"])
        if _is_already_declared_payload(query_result["result"]):
            normalized_status = "already_declared"

        step_result["query"] = query_result
        step_result["poll_state"] = str(query_result["state"])
        step_result["attempt_count"] = int(query_result["attempts"])
        step_result["next_retry_at"] = _next_retry_at(query_result.get("next_retry_after_minutes"))
        step_result["normalized_status"] = normalized_status
        step_result["business_code"] = extract_business_code(query_result["result"]) or step_result["business_code"]
        step_result["business_message"] = extract_message(query_result["result"]) or step_result["business_message"]
        step_result["raw_response"] = query_result["result"]
        step_result["finalized_at"] = _utc_now()
        return step_result

    def _append_operator_advice(self, message: str) -> None:
        """附加操作建议。"""

        if message not in self.context["operator_advice"]:
            self.context["operator_advice"].append(message)

    def _resolve_tracked_period(self, tax_code: str) -> dict[str, Any]:
        """根据税种生成所属期。"""

        entry = get_tax_code_entry(self.rule_sets, tax_code)
        if entry is None:
            raise QXYWorkflowError(f"税种 `{tax_code}` 未在附录中定义。")
        period_cycle = str(entry.get("period_cycle") or "").strip()
        if tax_code == "BDA0611159":
            period_cycle = "quarterly"
        elif tax_code == "BDA0610606":
            period_cycle = "monthly"
        if period_cycle not in {"monthly", "quarterly", "annual"}:
            raise QXYWorkflowError(f"税种 `{tax_code}` 缺少可计算的周期定义。")
        ssq_q, ssq_z = resolve_tax_period_range(
            self.config["year"],
            self.config["period"],
            period_cycle,
        )
        return {
            "yzpzzlDm": tax_code,
            "ssqQ": ssq_q,
            "ssqZ": ssq_z,
            "fromDate": ssq_q,
            "toDate": ssq_z,
            "tax_label": get_tax_code_label(self.rule_sets, tax_code) or tax_code,
        }

    def _build_pdf_items(self) -> list[dict[str, Any]]:
        """构造 PDF 下载税种列表。"""

        items: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for item in self.context["successful_declarations"]:
            tax_code = str(item.get("yzpzzlDm") or "")
            ssq_q = str(item.get("ssqQ") or "")
            ssq_z = str(item.get("ssqZ") or "")
            key = (tax_code, ssq_q, ssq_z)
            if not all(key) or key in seen_keys:
                continue
            seen_keys.add(key)
            items.append({"yzpzzlDm": tax_code, "ssqQ": ssq_q, "ssqZ": ssq_z})
        return items

    def _mark_declaration_success(
        self,
        *,
        tax_code: str,
        step_result: dict[str, Any],
        period_info: dict[str, Any],
        source: str,
    ) -> None:
        """登记成功或已申报的税种。"""

        status = str(step_result.get("normalized_status") or "")
        if status not in {"success", "already_declared"}:
            return
        item = {
            "yzpzzlDm": tax_code,
            "status": status,
            "source": source,
            "ssqQ": period_info["ssqQ"],
            "ssqZ": period_info["ssqZ"],
            "fromDate": period_info["fromDate"],
            "toDate": period_info["toDate"],
            "tax_label": period_info["tax_label"],
            "raw_response": step_result.get("raw_response"),
        }
        self.context["tracked_periods"][tax_code] = {
            "fromDate": period_info["fromDate"],
            "toDate": period_info["toDate"],
            "ssqQ": period_info["ssqQ"],
            "ssqZ": period_info["ssqZ"],
            "zspmDm": None,
            "tax_label": period_info["tax_label"],
        }
        existing_codes = {str(record.get("yzpzzlDm")) for record in self.context["successful_declarations"]}
        if tax_code not in existing_codes:
            self.context["successful_declarations"].append(item)

    def _run_fetch_roster(self) -> dict[str, Any]:
        """获取应申报清册。"""

        ensure_current_filing_period(self.config["year"], self.config["period"], action="获取应申报清册")
        step_result = self._run_async_step(
            step_name="fetch_roster",
            service="roster_entry",
            start_tool="initiate_declaration_entry_task_auto",
            query_tool="query_roster_entry_task_auto",
            payload=_build_common_args(self.config),
        )
        if step_result["normalized_status"] == "success":
            detail_list = _extract_detail_list(step_result["raw_response"])
            tax_codes = [str(item.get("yzpzzlDm")) for item in detail_list if item.get("yzpzzlDm")]
            step_result["detail"] = detail_list
            step_result["catalog"] = classify_tax_codes(self.rule_sets, tax_codes)
            self.context["roster"] = step_result
        return self._record_step("fetch_roster", step_result)

    def _run_financial_report(self, financial_input: dict[str, Any]) -> dict[str, Any]:
        """执行财报申报。"""

        if financial_input["mode"] == "excel":
            file_path = Path(financial_input["file_path"]).expanduser().resolve()
            attach_encode = base64.b64encode(file_path.read_bytes()).decode("ascii")
            payload = merge_non_null(
                _build_common_args(self.config),
                {
                    "zsxmList": [
                        {
                            "yzpzzlDm": financial_input["yzpzzlDm"],
                            "ssqQ": financial_input["ssqQ"],
                            "ssqZ": financial_input["ssqZ"],
                            "zlbsxlDm": financial_input["zlbsxlDm"],
                            "templateCode": financial_input["templateCode"],
                            "attachEncode": attach_encode,
                            "attachName": file_path.name,
                            "isDirectDeclare": bool(financial_input.get("isDirectDeclare", True)),
                        }
                    ]
                },
            )
            step_result = self._run_async_step(
                step_name="financial_report_excel",
                service="declaration_submission",
                start_tool="upload_tax_report_data_excel_auto",
                query_tool="query_upload_tax_report_result_auto",
                payload=payload,
                log_context={"yzpzzlDm": financial_input["yzpzzlDm"]},
            )
            if step_result["normalized_status"] in {"success", "already_declared"}:
                period_info = {
                    "yzpzzlDm": financial_input["yzpzzlDm"],
                    "ssqQ": financial_input["ssqQ"],
                    "ssqZ": financial_input["ssqZ"],
                    "fromDate": financial_input["ssqQ"],
                    "toDate": financial_input["ssqZ"],
                    "tax_label": get_tax_code_label(self.rule_sets, financial_input["yzpzzlDm"]) or financial_input["yzpzzlDm"],
                }
                self._mark_declaration_success(
                    tax_code=financial_input["yzpzzlDm"],
                    step_result=step_result,
                    period_info=period_info,
                    source="financial_report",
                )
            self.context["financial_report"] = step_result
            return self._record_step("financial_report", step_result)

        payload = merge_non_null(
            _build_common_args(self.config),
            {
                "cbData": financial_input.get("cbData"),
                "cbnbData": financial_input.get("cbnbData"),
                "isDirectDeclare": bool(financial_input.get("isDirectDeclare", True)),
            },
        )
        step_result = self._run_async_step(
            step_name="financial_report_json",
            service="declaration_submission",
            start_tool="upload_financial_report_data",
            query_tool="query_upload_financial_report_result_auto",
            payload=payload,
        )
        self.context["financial_report"] = step_result
        return self._record_step("financial_report", step_result)

    def _run_init_data(self, tax_code: str) -> dict[str, Any]:
        """初始化单个税种。"""

        ensure_current_filing_period(self.config["year"], self.config["period"], action="初始化申报数据")
        period_info = self._resolve_tracked_period(tax_code)
        payload = merge_non_null(
            _build_common_args(self.config),
            {"zsxmList": [{"yzpzzlDm": tax_code, "ssqQ": period_info["ssqQ"], "ssqZ": period_info["ssqZ"]}]},
        )
        step_result = self._base_step_result(f"init_data_{tax_code}")
        step_result["service"] = "initialize_data"
        step_result["tool"] = "load_init_data_task"
        step_result["request_payload"] = payload
        LOGGER.info("企业 %s 初始化税种 %s", self.config["aggOrgId"], tax_code)
        start_result = call_tool("initialize_data", "load_init_data_task", payload)
        step_result["start"] = start_result
        step_result["raw_response"] = start_result
        step_result["taskId"] = extract_task_id(start_result)
        step_result["business_code"] = extract_business_code(start_result) or None
        step_result["business_message"] = extract_message(start_result) or None
        start_state = infer_task_state(start_result)
        if step_result["taskId"] is None and start_state not in {"success", "pending", "unknown"}:
            step_result["normalized_status"] = "already_declared" if _is_already_declared_payload(start_result) else start_state
            step_result["poll_state"] = step_result["normalized_status"]
            step_result["finalized_at"] = _utc_now()
        else:
            query_result = poll_tool(
                "initialize_data",
                "get_init_data",
                merge_non_null(
                    _build_common_args(self.config),
                    {"yzpzzlDm": tax_code},
                ),
                interval_seconds=self.poll_interval_seconds,
                max_attempts=self.max_poll_attempts,
                short_interval_seconds=self.poll_interval_seconds,
                short_max_attempts=self.max_poll_attempts,
                long_backoff_minutes=[30, 60, 120, 240, 300],
                log_context={"taskId": step_result["taskId"] or "-", "yzpzzlDm": tax_code},
            )
            normalized_status = infer_task_state(query_result["result"])
            if normalized_status == "unknown":
                normalized_status = str(query_result["state"])
            if _is_already_declared_payload(query_result["result"]):
                normalized_status = "already_declared"
            step_result["query"] = query_result
            step_result["poll_state"] = str(query_result["state"])
            step_result["attempt_count"] = int(query_result["attempts"])
            step_result["next_retry_at"] = _next_retry_at(query_result.get("next_retry_after_minutes"))
            step_result["normalized_status"] = normalized_status
            step_result["business_code"] = extract_business_code(query_result["result"]) or step_result["business_code"]
            step_result["business_message"] = extract_message(query_result["result"]) or step_result["business_message"]
            step_result["raw_response"] = query_result["result"]
            step_result["finalized_at"] = _utc_now()
        self.context["tracked_periods"][tax_code] = merge_non_null(
            self.context["tracked_periods"].get(tax_code, {}),
            {
                "fromDate": period_info["fromDate"],
                "toDate": period_info["toDate"],
                "ssqQ": period_info["ssqQ"],
                "ssqZ": period_info["ssqZ"],
                "zspmDm": None,
                "tax_label": period_info["tax_label"],
            },
        )
        return self._record_step(f"init_data_{tax_code}", step_result)

    def _run_tax_report(self, tax_type: str, tax_code: str, label: str) -> dict[str, Any]:
        """提交税表。"""

        payload = merge_non_null(
            _build_common_args(self.config),
            {
                "tax_data": {},
                "tax_type": tax_type,
                "isDirectDeclare": True,
                "allowRepeatDeclare": False,
            },
        )
        step_result = self._run_async_step(
            step_name=f"{label}_tax_report",
            service="declaration_submission",
            start_tool="upload_tax_report_data_auto",
            query_tool="query_upload_tax_report_result_auto",
            payload=payload,
            log_context={"yzpzzlDm": tax_code},
        )
        period_info = self._resolve_tracked_period(tax_code)
        if step_result["normalized_status"] in {"success", "already_declared"}:
            self._mark_declaration_success(
                tax_code=tax_code,
                step_result=step_result,
                period_info=period_info,
                source="tax_report",
            )
        return self._record_step(f"tax_report_{tax_code}", step_result)

    def _run_declare_info(self) -> dict[str, Any]:
        """查询申报信息。"""

        step_result = self._run_async_step(
            step_name="declare_info",
            service="declaration_query",
            start_tool="load_declare_info_task",
            query_tool="query_declare_info_task_result_auto",
            payload=_build_common_args(self.config),
        )
        detail_list = _extract_detail_list(step_result.get("raw_response"))
        if detail_list:
            step_result["detail"] = detail_list
        self.context["declare_info"] = step_result
        return self._record_step("declare_info", step_result)

    def _run_pdf_download(self, pdf_items: list[dict[str, Any]]) -> dict[str, Any]:
        """下载 PDF。"""

        step_result = self._run_async_step(
            step_name="current_pdf",
            service="pdf_download",
            start_tool="load_pdf_task",
            query_tool="query_pdf_task_result_auto",
            payload=merge_non_null(
                _build_common_args(self.config),
                {"zsxmList": pdf_items, "analysisPdf": "Y"},
            ),
        )
        self.context["pdfs"] = step_result
        return self._record_step("current_pdf", step_result)

    def _build_payment_preparation(self) -> dict[str, Any]:
        """构造缴款准备数据。"""

        declare_info_step = self.context.get("declare_info")
        detail_list = declare_info_step.get("detail", []) if isinstance(declare_info_step, dict) else []
        detail_by_code: dict[str, dict[str, Any]] = {}
        for item in detail_list:
            code = str(item.get("yzpzzlDm") or "").strip()
            if not code or code not in self.context["tracked_periods"]:
                continue
            tracked = self.context["tracked_periods"][code]
            from_date = str(item.get("fromDate") or tracked.get("fromDate") or "")
            to_date = str(item.get("toDate") or tracked.get("toDate") or "")
            if from_date != tracked.get("fromDate") or to_date != tracked.get("toDate"):
                continue
            detail_by_code[code] = item

        payment_detail: list[dict[str, Any]] = []
        certificate_items: list[dict[str, Any]] = []
        for record in self.context["successful_declarations"]:
            tax_code = str(record.get("yzpzzlDm") or "")
            tracked = self.context["tracked_periods"].get(tax_code, {})
            matched_detail = detail_by_code.get(tax_code, {})
            zspm_dm = matched_detail.get("zspmDm") or tracked.get("zspmDm")
            certificate_items.append(
                {
                    "yzpzzlDm": tax_code,
                    "ssqQ": tracked.get("ssqQ"),
                    "ssqZ": tracked.get("ssqZ"),
                    "zspmDm": zspm_dm,
                }
            )
            pay_state = str(matched_detail.get("payState")) if matched_detail.get("payState") is not None else None
            tax_amount = _safe_float(matched_detail.get("taxAmountOfPaying"))
            if tax_amount is None:
                tax_amount = _safe_float(matched_detail.get("taxAmount"))
            if tax_amount is None:
                tax_amount = extract_tax_amount(record.get("raw_response"))
            if pay_state != "0" or tax_amount is None or tax_amount <= 0:
                continue
            payment_detail.append(
                merge_non_null(
                    {
                        "yzpzzlDm": tax_code,
                        "fromDate": tracked.get("fromDate"),
                        "toDate": tracked.get("toDate"),
                        "taxAmount": round(tax_amount, 2),
                    },
                    {"zspmDm": zspm_dm},
                )
            )

        payment_config = None
        if payment_detail:
            payment_config = {
                "aggOrgId": self.config["aggOrgId"],
                "accountId": self.config.get("accountId"),
                "year": self.config["year"],
                "period": self.config["period"],
                "steps": {
                    "payment": {
                        "enabled": True,
                        "detail": payment_detail,
                        "poll_result": True,
                    },
                    "certificate": {
                        "enabled": False,
                        "zsxmDtos": certificate_items,
                        "poll_result": True,
                    },
                },
            }
        result = {
            "detail": payment_detail,
            "certificate": certificate_items,
            "payment_config": payment_config,
        }
        self.context["payment_preparation"] = result
        return result

    def run(self) -> dict[str, Any]:
        """执行单企业闭环。"""

        roster_step = self._run_fetch_roster()
        roster_status = str(roster_step["normalized_status"])
        if roster_status in {"pending", "timeout"}:
            self.context["status"] = "pending"
            return self.context
        if roster_status not in {"success", "already_declared"}:
            self.context["status"] = "failed"
            self.context["success"] = False
            return self.context

        roster_detail = roster_step.get("detail", [])
        roster_codes = [str(item.get("yzpzzlDm") or "").strip() for item in roster_detail if item.get("yzpzzlDm")]
        scoped_codes = [code for code in roster_codes if code in SCOPED_TAX_CODES]
        ignored_codes = [code for code in roster_codes if code and code not in SCOPED_TAX_CODES]
        if ignored_codes:
            self._append_operator_advice(
                "以下税种当前未纳入企业级自动申报编排范围："
                + "、".join(sorted(set(ignored_codes)))
            )

        financial_codes = [code for code in scoped_codes if code in SUPPORTED_FINANCIAL_CODES]
        if financial_codes:
            financial_input = self.config.get("financial_report_input")
            if financial_input is None:
                self.context["status"] = "awaiting_financial_report"
                self._append_operator_advice("清册包含财务报表，请先补充财报文件或财报 JSON 报文。")
                self.save_checkpoint()
                return self.context
            if financial_input["mode"] == "excel" and financial_input["yzpzzlDm"] not in financial_codes:
                raise QXYWorkflowError(
                    "财报输入的 `yzpzzlDm` 与清册不匹配。"
                    f" 清册财报税种为 {financial_codes}，输入为 {financial_input['yzpzzlDm']}。"
                )
            financial_step = self._run_financial_report(financial_input)
            financial_status = str(financial_step["normalized_status"])
            if financial_status in {"pending", "timeout"}:
                self.context["status"] = "pending"
                return self.context
            if financial_status == "manual_review_required":
                self.context["status"] = "manual_review_required"
                return self.context
            if financial_status not in {"success", "already_declared"}:
                self.context["status"] = "failed"
                self.context["success"] = False
                return self.context

        if "BDA0611159" in scoped_codes:
            init_step = self._run_init_data("BDA0611159")
            init_status = str(init_step["normalized_status"])
            if init_status in {"pending", "timeout"}:
                self.context["status"] = "pending"
                self.context["income_tax"] = init_step
                return self.context
            if init_status == "manual_review_required":
                self.context["status"] = "manual_review_required"
                self.context["income_tax"] = init_step
                return self.context
            if init_status not in {"success", "already_declared"}:
                self.context["status"] = "failed"
                self.context["success"] = False
                self.context["income_tax"] = init_step
                return self.context

            period_info = self._resolve_tracked_period("BDA0611159")
            if _is_already_declared_payload(init_step.get("raw_response")):
                init_step["normalized_status"] = "already_declared"
                self._mark_declaration_success(
                    tax_code="BDA0611159",
                    step_result=init_step,
                    period_info=period_info,
                    source="init_data",
                )
                self.context["income_tax"] = init_step
            else:
                income_tax_step = self._run_tax_report("sdsData", "BDA0611159", "income_tax")
                income_tax_status = str(income_tax_step["normalized_status"])
                self.context["income_tax"] = income_tax_step
                if income_tax_status in {"pending", "timeout"}:
                    self.context["status"] = "pending"
                    return self.context
                if income_tax_status == "manual_review_required":
                    self.context["status"] = "manual_review_required"
                    return self.context
                if income_tax_status not in {"success", "already_declared"}:
                    self.context["status"] = "failed"
                    self.context["success"] = False
                    return self.context

        if "BDA0610606" in scoped_codes:
            init_step = self._run_init_data("BDA0610606")
            init_status = str(init_step["normalized_status"])
            if init_status in {"pending", "timeout"}:
                self.context["status"] = "pending"
                self.context["vat"] = init_step
                return self.context
            if init_status == "manual_review_required":
                self.context["status"] = "manual_review_required"
                self.context["vat"] = init_step
                return self.context
            if init_status not in {"success", "already_declared"}:
                self.context["status"] = "failed"
                self.context["success"] = False
                self.context["vat"] = init_step
                return self.context

            period_info = self._resolve_tracked_period("BDA0610606")
            if _is_already_declared_payload(init_step.get("raw_response")):
                init_step["normalized_status"] = "already_declared"
                self._mark_declaration_success(
                    tax_code="BDA0610606",
                    step_result=init_step,
                    period_info=period_info,
                    source="init_data",
                )
                self.context["vat"] = init_step
            else:
                no_ticket_income_amount = float(self.config["vat_adjustment"]["no_ticket_income_amount"])
                if no_ticket_income_amount > 0:
                    self.context["status"] = "manual_review_required"
                    self.context["vat"] = {
                        "step_name": "vat_adjustment",
                        "normalized_status": "manual_review_required",
                        "business_message": "检测到无票收入大于 0，V1 暂不支持自动改写增值税报文。",
                        "no_ticket_income_amount": no_ticket_income_amount,
                    }
                    self._append_operator_advice("存在无票收入调整，请人工确认增值税报文后再继续。")
                    self.save_checkpoint()
                    return self.context
                vat_step = self._run_tax_report("ybData", "BDA0610606", "vat")
                vat_status = str(vat_step["normalized_status"])
                self.context["vat"] = vat_step
                if vat_status in {"pending", "timeout"}:
                    self.context["status"] = "pending"
                    return self.context
                if vat_status == "manual_review_required":
                    self.context["status"] = "manual_review_required"
                    return self.context
                if vat_status not in {"success", "already_declared"}:
                    self.context["status"] = "failed"
                    self.context["success"] = False
                    return self.context

        if self.context["successful_declarations"]:
            declare_info_step = self._run_declare_info()
            if str(declare_info_step["normalized_status"]) in {"pending", "timeout"}:
                self.context["status"] = "pending"
                return self.context
            if str(declare_info_step["normalized_status"]) not in {"success", "already_declared"}:
                self._append_operator_advice("申报信息查询未成功，缴款准备将退回到申报返回报文兜底。")

            pdf_items = self._build_pdf_items()
            if pdf_items:
                pdf_step = self._run_pdf_download(pdf_items)
                pdf_status = str(pdf_step["normalized_status"])
                if pdf_status in {"pending", "timeout"}:
                    self.context["status"] = "pending"
                    return self.context
                if pdf_status not in {"success", "already_declared"}:
                    self.context["status"] = "failed"
                    self.context["success"] = False
                    return self.context
            self._build_payment_preparation()

        success_statuses = {str(item.get("status")) for item in self.context["successful_declarations"]}
        if success_statuses and success_statuses <= {"already_declared"}:
            self.context["status"] = "already_declared"
        else:
            self.context["status"] = "success"
        self.save_checkpoint()
        return self.context


class BatchRunner:
    """批量企业串行执行器。"""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        checkpoint_path: Path | None = None,
        existing_context: dict[str, Any] | None = None,
    ) -> None:
        self.config = validate_enterprise_filing_config(config)
        self.checkpoint_path = (checkpoint_path or _resolve_batch_checkpoint_path(self.config)).resolve()
        login_state = ensure_login_prerequisites(__file__)
        if existing_context is None:
            self.context = self._build_initial_context(login_state)
        else:
            self.context = existing_context
            self.context.setdefault("checkpoint_path", str(self.checkpoint_path))
            self.context.setdefault("enterprises", [])
            self.context.setdefault("summary", {})

    def _build_initial_context(self, login_state: dict[str, Any]) -> dict[str, Any]:
        """构建批量上下文。"""

        return {
            "success": True,
            "status": "running",
            "checkpoint_path": str(self.checkpoint_path),
            "config": self.config,
            "login": {
                "aggOrgId": login_state["aggOrgId"],
                "accountId": login_state["accountId"],
                "source": login_state.get("source"),
            },
            "enterprises": [],
            "summary": {},
            "successful_declarations": [],
            "pdf_download_requests": [],
            "payment_preparation": [],
            "updated_at": _utc_now(),
        }

    def save_checkpoint(self) -> None:
        """保存批量 checkpoint。"""

        if not self.config.get("checkpoint", {}).get("enabled", True):
            return
        self.context["updated_at"] = _utc_now()
        self.context["config"] = self.config
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint_path.open("w", encoding="utf-8") as file_obj:
            json.dump(self.context, file_obj, ensure_ascii=False, indent=2)
            file_obj.write("\n")

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str | Path) -> "BatchRunner":
        """从 checkpoint 恢复。"""

        path = Path(checkpoint_path).expanduser().resolve()
        with path.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        config = payload.get("config")
        if not isinstance(config, dict):
            raise QXYWorkflowError("checkpoint 中缺少 config，无法恢复执行。")
        return cls(config, checkpoint_path=path, existing_context=payload)

    def _bind_enterprise_account(self, enterprise_config: dict[str, Any]) -> dict[str, Any]:
        """为企业绑定 accountId。"""

        if enterprise_config.get("accountId"):
            return dict(enterprise_config)
        login_state = self.context.get("login", {})
        if not login_state.get("accountId"):
            raise LoginStateError("共享登录态缺少 accountId，无法执行企业申报编排。")
        bound_config = dict(enterprise_config)
        bound_config["accountId"] = str(login_state["accountId"])
        return bound_config

    def _summarize(self) -> None:
        """刷新批量汇总。"""

        enterprise_results = [item.get("result", {}) for item in self.context["enterprises"] if isinstance(item, dict)]
        statuses = [str(item.get("status") or "") for item in enterprise_results]
        summary = {
            "total": len(enterprise_results),
            "success": sum(status == "success" for status in statuses),
            "already_declared": sum(status == "already_declared" for status in statuses),
            "awaiting_financial_report": sum(status == "awaiting_financial_report" for status in statuses),
            "manual_review_required": sum(status == "manual_review_required" for status in statuses),
            "pending": sum(status == "pending" for status in statuses),
            "failed": sum(status == "failed" for status in statuses),
        }
        self.context["summary"] = summary
        self.context["successful_declarations"] = [
            {
                "aggOrgId": item.get("aggOrgId"),
                "display_name": item.get("display_name"),
                "successful_declarations": item.get("successful_declarations", []),
            }
            for item in enterprise_results
            if item.get("successful_declarations")
        ]
        self.context["pdf_download_requests"] = [
            {
                "aggOrgId": item.get("aggOrgId"),
                "display_name": item.get("display_name"),
                "pdf_step": item.get("pdfs"),
            }
            for item in enterprise_results
            if item.get("pdfs")
        ]
        self.context["payment_preparation"] = [
            {
                "aggOrgId": item.get("aggOrgId"),
                "display_name": item.get("display_name"),
                "payment_preparation": item.get("payment_preparation"),
            }
            for item in enterprise_results
            if item.get("payment_preparation", {}).get("detail")
        ]

    def run(self, *, resume: bool = False) -> dict[str, Any]:
        """串行执行所有企业。"""

        existing_map = {
            str(item.get("aggOrgId")): item
            for item in self.context.get("enterprises", [])
            if isinstance(item, dict) and item.get("aggOrgId")
        }

        for enterprise_config in self.config["enterprises"]:
            agg_org_id = enterprise_config["aggOrgId"]
            existing_entry = existing_map.get(agg_org_id)
            if resume and existing_entry:
                existing_status = str(existing_entry.get("result", {}).get("status") or "")
                if existing_status in TERMINAL_ENTERPRISE_STATUSES:
                    LOGGER.info("企业 %s 已是终态 `%s`，resume 时跳过。", agg_org_id, existing_status)
                    continue

            bound_config = self._bind_enterprise_account(enterprise_config)
            checkpoint_path = _enterprise_checkpoint_path(self.checkpoint_path, bound_config)
            enterprise_runner = EnterpriseRunner(
                bound_config,
                poll_interval_seconds=self.config["poll_interval_seconds"],
                max_poll_attempts=self.config["max_poll_attempts"],
                checkpoint_path=checkpoint_path,
                existing_context=existing_entry.get("result") if resume and existing_entry else None,
            )
            result = enterprise_runner.run()
            payload = {
                "aggOrgId": agg_org_id,
                "display_name": bound_config.get("display_name"),
                "checkpoint_path": str(checkpoint_path),
                "result": result,
            }
            if existing_entry:
                existing_entry.update(payload)
            else:
                self.context["enterprises"].append(payload)
            self._summarize()
            self.save_checkpoint()

        self._summarize()
        if self.context["summary"].get("failed"):
            self.context["status"] = "failed"
            self.context["success"] = False
        elif self.context["summary"].get("pending"):
            self.context["status"] = "pending"
        elif self.context["summary"].get("manual_review_required"):
            self.context["status"] = "manual_review_required"
        elif self.context["summary"].get("awaiting_financial_report"):
            self.context["status"] = "awaiting_financial_report"
        else:
            self.context["status"] = "success"
        self.save_checkpoint()
        return self.context


def run_workflow(config: dict[str, Any]) -> dict[str, Any]:
    """执行企业级编排。"""

    runner = BatchRunner(config)
    return runner.run()


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI。"""

    parser = argparse.ArgumentParser(description="企业级申报编排脚本（`period` 表示申报月份）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold-config", help="生成配置样例")
    scaffold_parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")
    scaffold_parser.add_argument("--year", type=int, help="申报年份")
    scaffold_parser.add_argument("--period", type=int, help="申报月份")

    run_parser = subparsers.add_parser("run", help="执行企业级申报编排")
    run_parser.add_argument("--config", required=True, help="配置文件路径")
    run_parser.add_argument("--checkpoint", help="显式指定批量 checkpoint 路径")

    resume_parser = subparsers.add_parser("resume", help="从 checkpoint 恢复执行")
    resume_parser.add_argument("--checkpoint", required=True, help="批量 checkpoint 路径")
    return parser


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
            runner = BatchRunner(
                config,
                checkpoint_path=Path(args.checkpoint).expanduser().resolve() if args.checkpoint else None,
            )
            _write_json(runner.run())
            return 0

        if args.command == "resume":
            runner = BatchRunner.from_checkpoint(args.checkpoint)
            _write_json(runner.run(resume=True))
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
