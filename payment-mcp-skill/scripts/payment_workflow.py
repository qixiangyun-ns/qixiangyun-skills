#!/usr/bin/env python3
"""缴款 Skill 的闭环编排脚本。"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from login_state_support import LoginStateError, apply_login_state_to_config

from qxy_mcp_lib import (
    QXYMCPError,
    QXYWorkflowError,
    call_tool,
    extract_task_id,
    merge_non_null,
    poll_tool,
    validate_workflow_config,
)

LOGGER = logging.getLogger(__name__)

STEP_ORDER: tuple[str, ...] = (
    "payment",
    "certificate",
)


def _resolve_sample_year_period(
    year: int | None = None,
    period: int | None = None,
) -> tuple[int, int]:
    """解析样例配置使用的所属年和所属期。"""

    today = date.today()
    return year or today.year, period or today.month


def _month_range(year: int, period: int) -> tuple[str, str]:
    """根据所属年月返回当月起止日期。"""

    last_day = calendar.monthrange(year, period)[1]
    return f"{year:04d}-{period:02d}-01", f"{year:04d}-{period:02d}-{last_day:02d}"


def build_sample_config(year: int | None = None, period: int | None = None) -> dict[str, Any]:
    """生成示例配置。"""

    sample_year, sample_period = _resolve_sample_year_period(year, period)
    month_start, month_end = _month_range(sample_year, sample_period)
    return {
        "aggOrgId": "请替换为企业ID",
        "year": sample_year,
        "period": sample_period,
        "accountId": None,
        "poll_interval_seconds": 10,
        "max_poll_attempts": 30,
        "steps": {
            "payment": {
                "enabled": True,
                "detail": [
                    {
                        "yzpzzlDm": "BDA0610606",
                        "fromDate": month_start,
                        "toDate": month_end,
                        "taxAmount": 10.0,
                        "jkfs": "1",
                        "yhzh": "请替换为银行账号",
                        "agreementAccount": None,
                        "zspmDm": None,
                        "zsxmDm": None,
                        "bsswjg": None,
                        "kqyswjgmc": None,
                        "sebyz": "N",
                    }
                ],
                "duration": None,
                "tdztswjgmc": None,
                "poll_result": True,
            },
            "certificate": {
                "enabled": False,
                "zsxmDtos": [
                    {
                        "ssqQ": month_start,
                        "ssqZ": month_end,
                        "yzpzzlDm": "BDA0610606",
                        "zspmDm": None,
                    }
                ],
                "tdztswjgmc": None,
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
    """构建共用参数。"""

    return merge_non_null(
        {
            "aggOrgId": config["aggOrgId"],
            "year": config["year"],
            "period": config["period"],
        },
        {"accountId": config.get("accountId")},
    )


def _parse_iso_date(raw_value: Any, field_name: str) -> date:
    """解析 ISO 日期并在失败时给出明确错误。"""

    if not isinstance(raw_value, str) or not raw_value.strip():
        raise QXYWorkflowError(f"`{field_name}` 是必填字符串，格式必须为 YYYY-MM-DD。")
    try:
        return date.fromisoformat(raw_value.strip())
    except ValueError as exc:
        raise QXYWorkflowError(f"`{field_name}` 格式非法，必须为 YYYY-MM-DD。") from exc


def _normalize_optional_string(raw_value: Any, field_name: str) -> str | None:
    """标准化可选字符串字段。"""

    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise QXYWorkflowError(f"`{field_name}` 必须是字符串或 null。")
    stripped = raw_value.strip()
    return stripped or None


def _normalize_required_string(
    raw_value: Any, field_name: str, *, allow_empty: bool = False
) -> str:
    """标准化必填字符串字段。"""

    if not isinstance(raw_value, str):
        raise QXYWorkflowError(f"`{field_name}` 必须是字符串。")
    stripped = raw_value.strip()
    if not allow_empty and not stripped:
        raise QXYWorkflowError(f"`{field_name}` 不能为空。")
    return stripped


def _normalize_payment_detail_item(item: Any, index: int) -> dict[str, Any]:
    """校验并标准化单条缴款明细。"""

    if not isinstance(item, dict):
        raise QXYWorkflowError(f"`payment.detail[{index}]` 必须是对象。")

    normalized = dict(item)
    normalized["yzpzzlDm"] = _normalize_required_string(
        item.get("yzpzzlDm"),
        f"payment.detail[{index}].yzpzzlDm",
    )
    from_date = _parse_iso_date(item.get("fromDate"), f"payment.detail[{index}].fromDate")
    to_date = _parse_iso_date(item.get("toDate"), f"payment.detail[{index}].toDate")
    if from_date > to_date:
        raise QXYWorkflowError(
            f"`payment.detail[{index}]` 的 fromDate 不能晚于 toDate。"
        )

    tax_amount = item.get("taxAmount")
    if isinstance(tax_amount, bool) or not isinstance(tax_amount, (int, float)):
        raise QXYWorkflowError(f"`payment.detail[{index}].taxAmount` 必须是数值。")
    if tax_amount <= 0:
        raise QXYWorkflowError(f"`payment.detail[{index}].taxAmount` 必须大于 0。")

    normalized["fromDate"] = from_date.isoformat()
    normalized["toDate"] = to_date.isoformat()
    normalized["taxAmount"] = tax_amount

    for field_name in (
        "jkfs",
        "agreementAccount",
        "yhzh",
        "zspmDm",
        "zsxmDm",
        "bsswjg",
        "kqyswjgmc",
        "sebyz",
    ):
        if field_name in normalized:
            normalized[field_name] = _normalize_optional_string(
                normalized.get(field_name),
                f"payment.detail[{index}].{field_name}",
            )

    return normalized


def _normalize_payment_detail(detail: Any) -> list[dict[str, Any]]:
    """校验并标准化缴款明细列表。"""

    if not isinstance(detail, list) or not detail:
        raise QXYWorkflowError("`payment.detail` 不能为空。")
    return [_normalize_payment_detail_item(item, index) for index, item in enumerate(detail)]


def _normalize_certificate_item(item: Any, index: int) -> dict[str, Any]:
    """校验并标准化单条完税证明请求项。"""

    if not isinstance(item, dict):
        raise QXYWorkflowError(f"`certificate.zsxmDtos[{index}]` 必须是对象。")

    normalized = dict(item)
    normalized["yzpzzlDm"] = _normalize_required_string(
        item.get("yzpzzlDm"),
        f"certificate.zsxmDtos[{index}].yzpzzlDm",
    )
    start_date = _parse_iso_date(item.get("ssqQ"), f"certificate.zsxmDtos[{index}].ssqQ")
    end_date = _parse_iso_date(item.get("ssqZ"), f"certificate.zsxmDtos[{index}].ssqZ")
    if start_date > end_date:
        raise QXYWorkflowError(
            f"`certificate.zsxmDtos[{index}]` 的 ssqQ 不能晚于 ssqZ。"
        )

    normalized["ssqQ"] = start_date.isoformat()
    normalized["ssqZ"] = end_date.isoformat()
    if "zspmDm" in normalized:
        normalized["zspmDm"] = _normalize_optional_string(
            normalized.get("zspmDm"),
            f"certificate.zsxmDtos[{index}].zspmDm",
        )
    return normalized


def _normalize_certificate_items(zsxm_dtos: Any) -> list[dict[str, Any]]:
    """校验并标准化完税证明请求项。"""

    if not isinstance(zsxm_dtos, list) or not zsxm_dtos:
        raise QXYWorkflowError("`certificate.zsxmDtos` 不能为空。")
    if len(zsxm_dtos) > 20:
        raise QXYWorkflowError(
            "`certificate.zsxmDtos` 最多支持 20 条，超过会触发官方 5003 错误。"
        )

    normalized_items = [
        _normalize_certificate_item(item, index)
        for index, item in enumerate(zsxm_dtos)
    ]

    seen_keys: set[tuple[str, str, str, str]] = set()
    min_year: int | None = None
    max_year: int | None = None
    for index, item in enumerate(normalized_items):
        start_date = date.fromisoformat(item["ssqQ"])
        end_date = date.fromisoformat(item["ssqZ"])
        item_min_year = min(start_date.year, end_date.year)
        item_max_year = max(start_date.year, end_date.year)
        min_year = item_min_year if min_year is None else min(min_year, item_min_year)
        max_year = item_max_year if max_year is None else max(max_year, item_max_year)

        unique_key = (
            item["yzpzzlDm"],
            item["ssqQ"],
            item["ssqZ"],
            item.get("zspmDm") or "",
        )
        if unique_key in seen_keys:
            raise QXYWorkflowError(
                f"`certificate.zsxmDtos[{index}]` 与前面的记录重复，"
                "会触发官方 5002 错误。"
            )
        seen_keys.add(unique_key)

    # 官方接口明确要求“最早起 + 最晚止”不能跨自然年，这里在本地提前拦截。
    if min_year is not None and max_year is not None and min_year != max_year:
        raise QXYWorkflowError(
            "`certificate.zsxmDtos` 的最早所属期起与最晚所属期止不可跨自然年。"
        )

    return normalized_items


def _poll_after_start(
    *,
    step_name: str,
    query_service: str,
    query_tool: str,
    agg_org_id: str,
    task_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """对异步任务进行标准轮询。"""

    poll_interval = int(config["poll_interval_seconds"])
    max_attempts = int(config["max_poll_attempts"])
    LOGGER.info("步骤 `%s` 开始轮询，taskId=%s", step_name, task_id)
    return poll_tool(
        query_service,
        query_tool,
        {"aggOrgId": agg_org_id, "taskId": task_id},
        interval_seconds=poll_interval,
        max_attempts=max_attempts,
    )


def _run_async_step(
    *,
    step_name: str,
    step_cfg: dict[str, Any],
    config: dict[str, Any],
    start_service: str,
    start_tool: str,
    payload_builder: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    query_service: str,
    query_tool: str,
) -> dict[str, Any]:
    """执行标准异步发起 + 轮询查询步骤。"""

    if not step_cfg.get("enabled", False):
        return {"skipped": True}

    start_result = call_tool(start_service, start_tool, payload_builder(step_cfg, config))
    output: dict[str, Any] = {"start": start_result}
    if not step_cfg.get("poll_result", True):
        return output

    task_id = extract_task_id(start_result)
    if not task_id:
        raise QXYWorkflowError(f"步骤 `{step_name}` 已发起，但未能从响应中提取 taskId。")

    output["taskId"] = task_id
    output["query"] = _poll_after_start(
        step_name=step_name,
        query_service=query_service,
        query_tool=query_tool,
        agg_org_id=config["aggOrgId"],
        task_id=task_id,
        config=config,
    )
    return output


def _build_payment_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    detail = _normalize_payment_detail(step_cfg.get("detail"))
    return merge_non_null(
        build_common_args(config),
        {
            "detail": detail,
            "duration": step_cfg.get("duration"),
            "tdztswjgmc": step_cfg.get("tdztswjgmc"),
        },
    )


def _build_certificate_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    zsxm_dtos = _normalize_certificate_items(step_cfg.get("zsxmDtos"))
    return merge_non_null(
        build_common_args(config),
        {
            "zsxmDtos": zsxm_dtos,
            "tdztswjgmc": step_cfg.get("tdztswjgmc"),
        },
    )


def run_payment(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """执行税款缴纳闭环步骤。"""

    return _run_async_step(
        step_name="payment",
        step_cfg=step_cfg,
        config=config,
        start_service="tax_payment",
        start_tool="load_payment_task",
        payload_builder=_build_payment_args,
        query_service="tax_payment",
        query_tool="query_tax_payment_task_result_auto",
    )


def run_certificate(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """执行完税证明闭环步骤。"""

    return _run_async_step(
        step_name="certificate",
        step_cfg=step_cfg,
        config=config,
        start_service="tax_payment_certificate",
        start_tool="initiate_wszm_parse_task_auto",
        payload_builder=_build_certificate_args,
        query_service="tax_payment_certificate",
        query_tool="query_wszm_parse_task_result_auto",
    )


def run_workflow(config: dict[str, Any], only_steps: set[str] | None = None) -> dict[str, Any]:
    """按固定顺序执行缴款闭环。"""

    config, login_state = apply_login_state_to_config(__file__, config)
    selected_steps = only_steps or set(STEP_ORDER)
    results: dict[str, Any] = {
        "aggOrgId": config["aggOrgId"],
        "accountId": config.get("accountId"),
        "year": config["year"],
        "period": config["period"],
        "login": {
            "aggOrgId": login_state["aggOrgId"],
            "accountId": login_state["accountId"],
            "source": login_state.get("source"),
        },
        "steps": {},
    }
    step_configs = config.get("steps", {})

    handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
        "payment": run_payment,
        "certificate": run_certificate,
    }

    for step_name in STEP_ORDER:
        if step_name not in selected_steps:
            continue
        LOGGER.info("开始执行步骤 `%s`", step_name)
        handler = handlers[step_name]
        step_cfg = step_configs.get(step_name, {})
        if not isinstance(step_cfg, dict):
            raise QXYWorkflowError(f"步骤 `{step_name}` 的配置必须是对象。")
        results["steps"][step_name] = handler(step_cfg, config)

    return results


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(description="企享云缴款闭环编排脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold-config", help="生成配置样例")
    scaffold_parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")
    scaffold_parser.add_argument("--year", type=int, help="所属年；默认使用当前年份")
    scaffold_parser.add_argument("--period", type=int, help="所属期；默认使用当前月份")

    run_parser = subparsers.add_parser("run", help="执行缴款闭环")
    run_parser.add_argument("--config", required=True, help="工作流配置 JSON 文件")
    run_parser.add_argument(
        "--steps",
        help="只运行指定步骤，使用逗号分隔，例如 payment,certificate",
    )

    return parser


def _write_json(payload: Any, output_path: str | None = None) -> None:
    """输出 JSON 结果。"""

    if output_path:
        target_path = Path(output_path).expanduser().resolve()
        with target_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
            file_obj.write("\n")
        LOGGER.info("已写入 %s", target_path)
        return

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


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
            only_steps: set[str] | None = None
            if args.steps:
                only_steps = {item.strip() for item in args.steps.split(",") if item.strip()}
            result = run_workflow(config, only_steps=only_steps)
            _write_json(result)
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
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
