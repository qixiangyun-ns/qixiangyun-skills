#!/usr/bin/env python3
"""申报 Skill 的闭环编排脚本。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from qxy_mcp_lib import (
    QXYMCPError,
    QXYWorkflowError,
    call_tool,
    extract_task_id,
    merge_non_null,
    poll_tool,
    resolve_init_query_items,
    validate_workflow_config,
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


def build_sample_config() -> dict[str, Any]:
    """生成示例配置。"""

    return {
        "aggOrgId": "请替换为企业ID",
        "year": 2026,
        "period": 3,
        "accountId": None,
        "poll_interval_seconds": 10,
        "max_poll_attempts": 12,
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
                        "ssqQ": "2026-03-01",
                        "ssqZ": "2026-03-31",
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
                "skssqq": "2026-01-01",
                "skssqz": "2026-03-31",
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


def run_fetch_roster(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """执行获取应申报清册。"""

    if not step_cfg.get("enabled", False):
        return {"skipped": True}

    start_result = call_tool(
        "roster_entry",
        "initiate_declaration_entry_task_auto",
        build_common_args(config),
    )
    output: dict[str, Any] = {"start": start_result}
    if not step_cfg.get("poll_result", True):
        return output

    task_id = extract_task_id(start_result)
    if not task_id:
        raise QXYWorkflowError("获取应申报清册已发起，但未能从响应中提取 taskId。")

    output["taskId"] = task_id
    output["query"] = _poll_after_start(
        step_name="fetch_roster",
        query_service="roster_entry",
        query_tool="query_roster_entry_task_auto",
        agg_org_id=config["aggOrgId"],
        task_id=task_id,
        config=config,
    )
    return output


def run_init_data(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """发起并查询初始化数据。"""

    if not step_cfg.get("enabled", False):
        return {"skipped": True}

    zsxm_list = step_cfg.get("zsxmList")
    if not isinstance(zsxm_list, list) or not zsxm_list:
        raise QXYWorkflowError("`init_data.zsxmList` 不能为空。")

    start_args = merge_non_null(build_common_args(config), {"zsxmList": zsxm_list})
    start_result = call_tool("initialize_data", "load_init_data_task", start_args)
    output: dict[str, Any] = {"start": start_result}

    if not step_cfg.get("query_after_start", True):
        return output

    query_results: list[dict[str, Any]] = []
    for item in resolve_init_query_items(step_cfg):
        query_args = merge_non_null(build_common_args(config), item)
        query_result = call_tool("initialize_data", "get_init_data", query_args)
        query_results.append(
            {
                "yzpzzlDm": item["yzpzzlDm"],
                "result": query_result,
            }
        )

    output["queries"] = query_results
    return output


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


def _build_tax_report_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
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


def _build_financial_report_args(
    step_cfg: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
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
    zsxm_list = step_cfg.get("zsxmList")
    if not isinstance(zsxm_list, list) or not zsxm_list:
        raise QXYWorkflowError("`current_pdf.zsxmList` 不能为空数组。")
    return merge_non_null(
        build_common_args(config),
        {
            "zsxmList": zsxm_list,
            "analysisPdf": step_cfg.get("analysisPdf"),
            "tdztswjgmc": step_cfg.get("tdztswjgmc"),
        },
    )


def _build_history_pdf_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
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


def _build_declare_info_args(
    step_cfg: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    _ = step_cfg
    return build_common_args(config)


def _build_missing_check_args(
    step_cfg: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    _ = step_cfg
    return build_common_args(config)


def run_workflow(config: dict[str, Any], only_steps: set[str] | None = None) -> dict[str, Any]:
    """按固定顺序执行申报闭环。"""

    selected_steps = only_steps or set(STEP_ORDER)
    results: dict[str, Any] = {
        "aggOrgId": config["aggOrgId"],
        "year": config["year"],
        "period": config["period"],
        "steps": {},
    }
    step_configs = config.get("steps", {})

    handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
        "fetch_roster": run_fetch_roster,
        "init_data": run_init_data,
        "tax_report": lambda step_cfg, cfg: _run_async_step(
            step_name="tax_report",
            step_cfg=step_cfg,
            config=cfg,
            start_service="declaration_submission",
            start_tool="upload_tax_report_data_auto",
            payload_builder=_build_tax_report_args,
            query_service="declaration_submission",
            query_tool="query_upload_tax_report_result_auto",
        ),
        "financial_report": lambda step_cfg, cfg: _run_async_step(
            step_name="financial_report",
            step_cfg=step_cfg,
            config=cfg,
            start_service="declaration_submission",
            start_tool="upload_financial_report_data",
            payload_builder=_build_financial_report_args,
            query_service="declaration_submission",
            query_tool="query_upload_financial_report_result_auto",
        ),
        "current_pdf": lambda step_cfg, cfg: _run_async_step(
            step_name="current_pdf",
            step_cfg=step_cfg,
            config=cfg,
            start_service="pdf_download",
            start_tool="load_pdf_task",
            payload_builder=_build_current_pdf_args,
            query_service="pdf_download",
            query_tool="query_pdf_task_result_auto",
        ),
        "history_pdf": lambda step_cfg, cfg: _run_async_step(
            step_name="history_pdf",
            step_cfg=step_cfg,
            config=cfg,
            start_service="pdf_download",
            start_tool="load_wq_pdf_task",
            payload_builder=_build_history_pdf_args,
            query_service="pdf_download",
            query_tool="query_pdf_task_result_auto",
        ),
        "declare_info": lambda step_cfg, cfg: _run_async_step(
            step_name="declare_info",
            step_cfg=step_cfg,
            config=cfg,
            start_service="declaration_query",
            start_tool="load_declare_info_task",
            payload_builder=_build_declare_info_args,
            query_service="declaration_query",
            query_tool="query_declare_info_task_result_auto",
        ),
        "missing_check": lambda step_cfg, cfg: _run_async_step(
            step_name="missing_check",
            step_cfg=step_cfg,
            config=cfg,
            start_service="missing_declaration_check",
            start_tool="initiate_missing_declaration_check_task_auto",
            payload_builder=_build_missing_check_args,
            query_service="missing_declaration_check",
            query_tool="query_missing_declaration_check_task_auto",
        ),
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

    parser = argparse.ArgumentParser(description="企享云申报闭环编排脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold-config", help="生成配置样例")
    scaffold_parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    run_parser = subparsers.add_parser("run", help="执行申报闭环")
    run_parser.add_argument("--config", required=True, help="工作流配置 JSON 文件")
    run_parser.add_argument(
        "--steps",
        help="只运行指定步骤，使用逗号分隔，例如 fetch_roster,init_data",
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
            _write_json(build_sample_config(), args.output)
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
    except (QXYMCPError, QXYWorkflowError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
