#!/usr/bin/env python3
"""登录 Skill 的命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts import ConfigError, TaxLoginClient, TaxLoginError, TaxLoginWorkflow
from scripts.login_state_support import (
    LoginStateError,
    clear_login_state,
    read_login_state,
    resolve_login_state_path,
)

LOGGER = logging.getLogger(__name__)


def build_sample_config() -> dict[str, Any]:
    """生成示例配置。"""

    return {
        "areaCode": "3100",
        "phone": "13800138000",
        "password": "请替换为登录密码",
        "natural": {
            "username": None,
        },
        "enterprise": {
            "orgName": "请替换为企业名称",
            "taxNumber": "请替换为企业税号",
            "multiAccountPhone": "13800138001",
            "multiAccountPassword": "请替换为办税小号密码",
            "multiAccountUsername": None,
        },
    }


def _write_json(payload: Any, output_path: str | None = None) -> None:
    """输出 JSON。"""

    if output_path:
        target_path = Path(output_path).expanduser().resolve()
        with target_path.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
            file_obj.write("\n")
        LOGGER.info("已写入 %s", target_path)
        return

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _resolve_secret(
    *,
    direct_value: str | None,
    env_var_name: str | None,
    field_name: str,
) -> str:
    """优先解析命令行密文，其次从环境变量读取敏感值。"""

    if direct_value:
        return direct_value
    if env_var_name:
        env_value = os.environ.get(env_var_name)
        if env_value:
            return env_value
        raise ValueError(f"环境变量 `{env_var_name}` 未设置，无法读取 {field_name}。")
    raise ValueError(f"`{field_name}` 未提供。")


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(description="企享云登录工作流脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold-config", help="生成配置样例")
    scaffold_parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    create_natural = subparsers.add_parser("create-natural-account", help="创建自然人账号")
    create_natural.add_argument("--area-code", required=True, help="地区代码，如 3100")
    create_natural.add_argument("--phone", required=True, help="手机号")
    create_natural.add_argument("--password", help="登录密码")
    create_natural.add_argument(
        "--password-env",
        help="从指定环境变量读取登录密码，优先于在 shell 中明文传参",
    )
    create_natural.add_argument("--username", help="用户名；默认与手机号相同")

    start_login = subparsers.add_parser("start-natural-login", help="发送自然人登录验证码")
    start_login.add_argument("--agg-org-id", required=True, help="自然人 aggOrgId")
    start_login.add_argument("--account-id", required=True, help="自然人 accountId")

    verify_login = subparsers.add_parser("verify-natural-login", help="上传自然人登录验证码")
    verify_login.add_argument("--task-id", required=True, help="验证码任务ID")
    verify_login.add_argument("--sms-code", required=True, help="短信验证码")

    list_enterprises = subparsers.add_parser("list-enterprises", help="获取自然人企业列表")
    list_enterprises.add_argument("--natural-agg-org-id", required=True, help="自然人 aggOrgId")
    list_enterprises.add_argument("--natural-account-id", required=True, help="自然人 accountId")

    subscribe = subparsers.add_parser("subscribe-enterprise-service", help="订购企业服务")
    subscribe.add_argument("--area-code", required=True, help="地区代码，如 3100")
    subscribe.add_argument("--org-name", required=True, help="企业名称")
    subscribe.add_argument("--tax-number", required=True, help="企业税号")

    create_multi = subparsers.add_parser("create-multi-account", help="创建企业多账号")
    create_multi.add_argument("--agg-org-id", required=True, help="企业 aggOrgId")
    create_multi.add_argument("--area-code", required=True, help="地区代码，如 3100")
    create_multi.add_argument("--phone", required=True, help="办税小号手机号")
    create_multi.add_argument("--password", help="办税小号密码")
    create_multi.add_argument(
        "--password-env",
        help="从指定环境变量读取办税小号密码，优先于在 shell 中明文传参",
    )
    create_multi.add_argument("--username", help="办税小号用户名；默认与手机号相同")

    enterprise_ready = subparsers.add_parser(
        "login-enterprise-account",
        help="校验企业账号是否可直接办税，并自动写入共享登录态",
    )
    enterprise_ready.add_argument("--agg-org-id", required=True, help="企业 aggOrgId")
    enterprise_ready.add_argument("--account-id", required=True, help="企业多账号 accountId")

    show_state = subparsers.add_parser("show-login-state", help="查看当前共享登录态")
    show_state.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    clear_state = subparsers.add_parser("clear-login-state", help="清理当前共享登录态")
    clear_state.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    return parser


def main() -> int:
    """CLI 入口。"""

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "scaffold-config":
            _write_json(build_sample_config(), args.output)
            return 0

        if args.command == "show-login-state":
            payload = {
                "stateFile": str(resolve_login_state_path(__file__)),
                "state": read_login_state(__file__),
            }
            _write_json(payload, args.output)
            return 0

        if args.command == "clear-login-state":
            cleared_path = clear_login_state(__file__)
            _write_json(
                {
                    "success": True,
                    "stateFile": str(cleared_path),
                    "message": "共享登录态已清理",
                },
                args.output,
            )
            return 0

        client = TaxLoginClient.from_config()
        workflow = TaxLoginWorkflow(client)

        if args.command == "create-natural-account":
            password = _resolve_secret(
                direct_value=args.password,
                env_var_name=args.password_env,
                field_name="自然人登录密码",
            )
            _write_json(
                workflow.create_natural_person_account(
                    area_code=args.area_code,
                    phone=args.phone,
                    password=password,
                    username=args.username,
                )
            )
            return 0

        if args.command == "start-natural-login":
            _write_json(
                workflow.start_natural_person_login(
                    agg_org_id=args.agg_org_id,
                    account_id=args.account_id,
                )
            )
            return 0

        if args.command == "verify-natural-login":
            _write_json(
                workflow.verify_natural_person_login(
                    task_id=args.task_id,
                    sms_code=args.sms_code,
                )
            )
            return 0

        if args.command == "list-enterprises":
            _write_json(
                workflow.list_enterprises(
                    natural_agg_org_id=args.natural_agg_org_id,
                    natural_account_id=args.natural_account_id,
                )
            )
            return 0

        if args.command == "subscribe-enterprise-service":
            _write_json(
                workflow.subscribe_enterprise_service(
                    area_code=args.area_code,
                    org_name=args.org_name,
                    tax_number=args.tax_number,
                )
            )
            return 0

        if args.command == "create-multi-account":
            password = _resolve_secret(
                direct_value=args.password,
                env_var_name=args.password_env,
                field_name="办税小号密码",
            )
            _write_json(
                workflow.create_multi_account(
                    agg_org_id=args.agg_org_id,
                    area_code=args.area_code,
                    phone=args.phone,
                    password=password,
                    username=args.username,
                )
            )
            return 0

        if args.command == "login-enterprise-account":
            _write_json(
                workflow.login_enterprise_account(
                    agg_org_id=args.agg_org_id,
                    account_id=args.account_id,
                )
            )
            return 0

        parser.print_help()
        return 1
    except (ConfigError, LoginStateError, TaxLoginError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
