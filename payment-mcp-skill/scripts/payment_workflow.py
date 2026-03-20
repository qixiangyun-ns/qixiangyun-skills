#!/usr/bin/env python3
"""缴款 Skill 的闭环编排脚本。"""

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
    validate_workflow_config,
)

LOGGER = logging.getLogger(__name__)

STEP_ORDER: tuple[str, ...] = (
    "payment",
    "certificate",
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
            "payment": {
                "enabled": True,
                "detail": [],
                "poll_result": True,
            },
            "certificate": {
                "enabled": False,
                "zsxmDtos": [],
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
    detail = step_cfg.get("detail")
    if not isinstance(detail, list) or not detail:
        raise QXYWorkflowError("`payment.detail` 不能为空。")
    return merge_non_null(
        build_common_args(config),
        {
            "detail": detail,
            "duration": step_cfg.get("duration"),
            "tdztswjgmc": step_cfg.get("tdztswjgmc"),
        },
    )


def _build_certificate_args(step_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    zsxm_dtos = step_cfg.get("zsxmDtos")
    if not isinstance(zsxm_dtos, list) or not zsxm_dtos:
        raise QXYWorkflowError("`certificate.zsxmDtos` 不能为空。")
    return merge_non_null(
        build_common_args(config),
        {
            "zsxmDtos": zsxm_dtos,
            "tdztswjgmc": step_cfg.get("tdztswjgmc"),
        },
    )


def run_workflow(config: dict[str, Any], only_steps: set[str] | None = None) -> dict[str, Any]:
    """按固定顺序执行缴款闭环。"""

    selected_steps = only_steps or set(STEP_ORDER)
    results: dict[str, Any] = {
        "aggOrgId": config["aggOrgId"],
        "year": config["year"],
        "period": config["period"],
        "steps": {},
    }
    step_configs = config.get("steps", {})

    handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
        "payment": lambda step_cfg, cfg: _run_async_step(
            step_name="payment",
            step_cfg=step_cfg,
            config=cfg,
            start_service="tax_payment",
            start_tool="load_payment_task",
            payload_builder=_build_payment_args,
            query_service="tax_payment",
            query_tool="query_tax_payment_task_result_auto",
        ),
        "certificate": lambda step_cfg, cfg: _run_async_step(
            step_name="certificate",
            step_cfg=step_cfg,
            config=cfg,
            start_service="tax_payment_certificate",
            start_tool="initiate_wszm_parse_task_auto",
            payload_builder=_build_certificate_args,
            query_service="tax_payment_certificate",
            query_tool="query_wszm_parse_task_result_auto",
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

    parser = argparse.ArgumentParser(description="企享云缴款闭环编排脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold-config", help="生成配置样例")
    scaffold_parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

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
