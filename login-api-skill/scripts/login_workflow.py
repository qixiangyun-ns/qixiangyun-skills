#!/usr/bin/env python3
"""登录 Skill 的命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts import ConfigError, TaxLoginClient, TaxLoginError, TaxLoginWorkflow
from scripts.login_flow_state_support import (
    clear_login_flow_state,
    merge_login_flow_state,
    read_login_flow_state,
    resolve_login_flow_state_path,
)
from scripts.login_state_support import (
    LoginStateError,
    clear_login_state,
    read_login_state,
    resolve_login_state_path,
)

LOGGER = logging.getLogger(__name__)


def build_sample_config() -> dict[str, Any]:
    """生成配置样例。"""
    return {
        "areaCode": "3100",
        "phone": "13800138000",
        "password": "请替换为登录密码",
        "natural": {"username": None},
        "enterprise": {
            "orgName": "请替换为企业名称",
            "taxNumber": "请替换为企业税号",
            "multiAccountPhone": "13800138001",
            "multiAccountPassword": "请替换为办税小号密码",
            "multiAccountUsername": None,
            "loginMode": 14,
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


def _write_error_payload(exc: Exception) -> None:
    """输出结构化错误。"""
    payload: dict[str, Any] = {
        "success": False,
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
        },
    }
    if isinstance(exc, TaxLoginError):
        payload["error"]["code"] = exc.code
        payload["error"]["detail"] = exc.data
    _write_json(payload)


def _resolve_secret(*, direct_value: str | None, env_var_name: str | None, field_name: str) -> str:
    """解析敏感值。"""
    if direct_value:
        return direct_value
    if env_var_name:
        env_value = os.environ.get(env_var_name)
        if env_value:
            return env_value
        raise ValueError(f"环境变量 `{env_var_name}` 未设置，无法读取 {field_name}。")
    raise ValueError(f"`{field_name}` 未提供。")


def _resolve_value(direct_value: Any, fallback_value: Any, field_name: str) -> str:
    """优先使用显式值，其次使用流程状态。"""
    if direct_value not in (None, ""):
        return str(direct_value).strip()
    normalized_fallback = str(fallback_value or "").strip()
    if normalized_fallback:
        return normalized_fallback
    raise ValueError(f"`{field_name}` 未提供，且流程状态中也没有可复用值。")


def _build_full_login_payload(
    *,
    success: bool,
    message: str,
    flow_status: str,
    final_success: bool,
    next_step: str | None,
    waiting_for_user_input: bool = False,
    user_input_kind: str | None = None,
    **extra: Any,
) -> Dict[str, Any]:
    """构建全链路命令的统一输出。"""
    payload: Dict[str, Any] = {
        "success": success,
        "message": message,
        "flow_status": flow_status,
        "final_success": final_success,
        "next_step": next_step,
        "waiting_for_user_input": waiting_for_user_input,
    }
    if user_input_kind:
        payload["user_input_kind"] = user_input_kind
    payload.update(extra)
    return payload


def _build_enterprise_context(flow_state: Dict[str, Any]) -> Dict[str, Any]:
    """从流程状态中提取企业上下文。"""
    enterprise_subscription = flow_state.get("enterpriseSubscription", {})
    selected_enterprise = flow_state.get("selectedEnterprise", {})
    return {
        "orgId": enterprise_subscription.get("orgId") or enterprise_subscription.get("aggOrgId"),
        "orgName": enterprise_subscription.get("orgName")
        or selected_enterprise.get("org_name")
        or selected_enterprise.get("name"),
        "nsrsbh": enterprise_subscription.get("taxNumber") or selected_enterprise.get("nsrsbh"),
    }


def _debug_commands_enabled() -> bool:
    """是否启用分步调试命令。"""
    return os.environ.get("QXY_LOGIN_ENABLE_DEBUG_COMMANDS", "").strip() == "1"


def _run_full_login(args: argparse.Namespace, workflow: TaxLoginWorkflow) -> Dict[str, Any]:
    """执行从自然人到账户到企业登录完成的完整链路。"""
    flow_state = read_login_flow_state(__file__)
    shared_login_state = read_login_state(__file__)

    if flow_state.get("enterpriseLogin", {}).get("verified") and shared_login_state:
        return _build_full_login_payload(
            success=True,
            message="企业登录态已存在，完整登录链路已完成。",
            flow_status="ENTERPRISE_AUTHENTICATED",
            final_success=True,
            next_step=None,
            login_state=shared_login_state,
        )

    area_code = _resolve_value(args.area_code, flow_state.get("context", {}).get("areaCode"), "area_code")
    phone = _resolve_value(args.phone, flow_state.get("context", {}).get("phone"), "phone")
    natural_password = _resolve_secret(
        direct_value=args.password,
        env_var_name=args.password_env,
        field_name="自然人登录密码",
    )
    natural_username = args.username or flow_state.get("natural", {}).get("username") or phone
    enterprise_phone = args.enterprise_phone or flow_state.get("enterpriseAccount", {}).get("phone") or phone
    enterprise_password = _resolve_secret(
        direct_value=args.enterprise_password or args.password,
        env_var_name=args.enterprise_password_env or args.password_env,
        field_name="办税小号密码",
    )
    enterprise_username = (
        args.enterprise_username
        or flow_state.get("enterpriseAccount", {}).get("username")
        or enterprise_phone
    )

    natural_account_id = str(flow_state.get("natural", {}).get("accountId") or "").strip()
    if not natural_account_id:
        create_natural_result = workflow.create_natural_person_account(
            area_code=area_code,
            phone=phone,
            password=natural_password,
            username=natural_username,
        )
        natural_account_id = str(create_natural_result.get("account_id") or "").strip()
        flow_state = merge_login_flow_state(
            __file__,
            {
                "context": {"areaCode": area_code, "phone": phone},
                "natural": {
                    "accountId": natural_account_id,
                    "username": natural_username,
                    "loginMode": create_natural_result.get("login_mode"),
                },
            },
        )

    natural_login_state = flow_state.get("naturalLogin", {})
    if not natural_login_state.get("verified"):
        natural_task_id = str(natural_login_state.get("taskId") or "").strip()
        if not natural_task_id:
            start_natural_result = workflow.start_natural_person_login(account_id=natural_account_id)
            natural_task_id = str(start_natural_result.get("task_id") or "").strip()
            flow_state = merge_login_flow_state(
                __file__,
                {"naturalLogin": {"taskId": natural_task_id, "verified": bool(start_natural_result.get("login_success"))}},
            )
            if start_natural_result.get("need_verify"):
                if not args.natural_sms_code:
                    return _build_full_login_payload(
                        success=False,
                        message="自然人验证码已发送，但整条登录链路尚未完成。请提供自然人短信验证码后再次执行同一脚本。",
                        flow_status="WAIT_NATURAL_SMS",
                        final_success=False,
                        next_step="run-full-login",
                        waiting_for_user_input=True,
                        user_input_kind="natural_sms_code",
                        task_id=natural_task_id,
                        account_id=natural_account_id,
                    )
            else:
                flow_state = merge_login_flow_state(__file__, {"naturalLogin": {"verified": True}})

        if not flow_state.get("naturalLogin", {}).get("verified"):
            if not args.natural_sms_code:
                return _build_full_login_payload(
                    success=False,
                    message="自然人登录仍等待验证码，完整链路未完成。请提供自然人短信验证码后再次执行同一脚本。",
                    flow_status="WAIT_NATURAL_SMS",
                    final_success=False,
                    next_step="run-full-login",
                    waiting_for_user_input=True,
                    user_input_kind="natural_sms_code",
                    task_id=natural_task_id,
                    account_id=natural_account_id,
                )
            workflow.verify_natural_person_login(
                task_id=natural_task_id,
                sms_code=args.natural_sms_code,
            )
            flow_state = merge_login_flow_state(__file__, {"naturalLogin": {"verified": True}})

    enterprises = flow_state.get("enterpriseList", {}).get("items") or []
    if not enterprises:
        list_result = workflow.list_enterprises(natural_account_id=natural_account_id)
        enterprises = list_result.get("enterprises", [])
        flow_state = merge_login_flow_state(
            __file__,
            {"enterpriseList": {"items": enterprises, "total": list_result.get("total", len(enterprises))}},
        )

    selected_enterprise = flow_state.get("selectedEnterprise", {})
    if not selected_enterprise:
        try:
            selected_enterprise = workflow.choose_target_enterprise(
                enterprises,
                nsrsbh=args.nsrsbh,
                name=args.org_name,
                identity_type=args.identity_type or "BSY",
                index=args.index,
            )
        except TaxLoginError as exc:
            if str(exc.code) == "AMBIGUOUS_ENTERPRISE":
                return _build_full_login_payload(
                    success=False,
                    message="已获取企业列表，但目标企业不唯一。请补充企业税号、企业名称、身份类型或列表序号后再次执行同一脚本。",
                    flow_status="WAIT_ENTERPRISE_SELECTION",
                    final_success=False,
                    next_step="run-full-login",
                    waiting_for_user_input=True,
                    user_input_kind="enterprise_selector",
                    enterprises=enterprises,
                )
            raise
        flow_state = merge_login_flow_state(__file__, {"selectedEnterprise": selected_enterprise})

    enterprise_subscription = flow_state.get("enterpriseSubscription", {})
    agg_org_id = str(enterprise_subscription.get("aggOrgId") or "").strip()
    if not agg_org_id:
        subscribe_result = workflow.subscribe_enterprise_service(
            area_code=area_code,
            org_name=_resolve_value(args.org_name, selected_enterprise.get("org_name") or selected_enterprise.get("name"), "org_name"),
            tax_number=_resolve_value(args.nsrsbh, selected_enterprise.get("nsrsbh"), "tax_number"),
        )
        agg_org_id = str(subscribe_result.get("agg_org_id") or "").strip()
        flow_state = merge_login_flow_state(
            __file__,
            {
                "enterpriseSubscription": {
                    "aggOrgId": agg_org_id,
                    "orgId": subscribe_result.get("org_id") or agg_org_id,
                    "orgName": selected_enterprise.get("org_name") or selected_enterprise.get("name"),
                    "taxNumber": selected_enterprise.get("nsrsbh"),
                }
            },
        )

    enterprise_account = flow_state.get("enterpriseAccount", {})
    enterprise_account_id = str(enterprise_account.get("accountId") or "").strip()
    if not enterprise_account_id:
        create_multi_result = workflow.create_multi_account(
            agg_org_id=agg_org_id,
            area_code=area_code,
            phone=enterprise_phone,
            password=enterprise_password,
            username=enterprise_username,
            login_mode=args.login_mode,
        )
        enterprise_account_id = str(create_multi_result.get("account_id") or "").strip()
        flow_state = merge_login_flow_state(
            __file__,
            {
                "enterpriseAccount": {
                    "aggOrgId": create_multi_result.get("agg_org_id") or agg_org_id,
                    "accountId": enterprise_account_id,
                    "phone": enterprise_phone,
                    "username": enterprise_username,
                    "loginMode": create_multi_result.get("login_mode") or args.login_mode,
                }
            },
        )

    enterprise_context = _build_enterprise_context(flow_state)
    enterprise_login_state = flow_state.get("enterpriseLogin", {})
    if not enterprise_login_state.get("verified"):
        enterprise_task_id = str(enterprise_login_state.get("taskId") or "").strip()
        if not enterprise_task_id:
            start_enterprise_result = workflow.start_enterprise_login(
                agg_org_id=agg_org_id,
                account_id=enterprise_account_id,
                enterprise_context=enterprise_context,
            )
            enterprise_task_id = str(start_enterprise_result.get("task_id") or "").strip()
            flow_state = merge_login_flow_state(
                __file__,
                {"enterpriseLogin": {"taskId": enterprise_task_id, "verified": bool(start_enterprise_result.get("login_success"))}},
            )
            if start_enterprise_result.get("login_success"):
                return _build_full_login_payload(
                    success=True,
                    message="企业登录成功，完整登录链路已完成。",
                    flow_status="ENTERPRISE_AUTHENTICATED",
                    final_success=True,
                    next_step=None,
                    agg_org_id=agg_org_id,
                    account_id=enterprise_account_id,
                    login_state=start_enterprise_result.get("login_state"),
                )
            if start_enterprise_result.get("need_verify") and not args.enterprise_sms_code:
                return _build_full_login_payload(
                    success=False,
                    message="企业验证码已发送，但整条登录链路尚未完成。请提供企业短信验证码后再次执行同一脚本。",
                    flow_status="WAIT_ENTERPRISE_SMS",
                    final_success=False,
                    next_step="run-full-login",
                    waiting_for_user_input=True,
                    user_input_kind="enterprise_sms_code",
                    task_id=enterprise_task_id,
                    agg_org_id=agg_org_id,
                    account_id=enterprise_account_id,
                )

        if not flow_state.get("enterpriseLogin", {}).get("verified"):
            if not args.enterprise_sms_code:
                return _build_full_login_payload(
                    success=False,
                    message="企业登录仍等待验证码，完整链路未完成。请提供企业短信验证码后再次执行同一脚本。",
                    flow_status="WAIT_ENTERPRISE_SMS",
                    final_success=False,
                    next_step="run-full-login",
                    waiting_for_user_input=True,
                    user_input_kind="enterprise_sms_code",
                    task_id=enterprise_task_id,
                    agg_org_id=agg_org_id,
                    account_id=enterprise_account_id,
                )
            verify_enterprise_result = workflow.verify_enterprise_login(
                task_id=enterprise_task_id,
                sms_code=args.enterprise_sms_code,
                agg_org_id=agg_org_id,
                account_id=enterprise_account_id,
                enterprise_context=enterprise_context,
            )
            flow_state = merge_login_flow_state(__file__, {"enterpriseLogin": {"verified": True}})
            return _build_full_login_payload(
                success=True,
                message="企业登录成功，完整登录链路已完成。",
                flow_status="ENTERPRISE_AUTHENTICATED",
                final_success=True,
                next_step=None,
                agg_org_id=agg_org_id,
                account_id=enterprise_account_id,
                login_state=verify_enterprise_result.get("login_state"),
            )

    refreshed_login_state = read_login_state(__file__)
    return _build_full_login_payload(
        success=bool(refreshed_login_state),
        message="企业登录成功，完整登录链路已完成。" if refreshed_login_state else "流程状态显示已完成，但未读取到共享登录态。",
        flow_status="ENTERPRISE_AUTHENTICATED" if refreshed_login_state else "ENTERPRISE_LOGIN_STATE_MISSING",
        final_success=bool(refreshed_login_state),
        next_step=None if refreshed_login_state else "run-full-login",
        agg_org_id=agg_org_id,
        account_id=enterprise_account_id,
        login_state=refreshed_login_state,
    )


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(description="企享云登录工作流脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser("scaffold-config", help="生成配置样例")
    scaffold_parser.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    subparsers.add_parser("check-config", help="检查当前凭证与 API 连通性")

    run_full_login = subparsers.add_parser(
        "run-full-login",
        help="执行从自然人到企业登录完成的完整链路；仅在等待验证码时中断",
    )
    run_full_login.add_argument("--area-code", required=True, help="地区代码，如 3100")
    run_full_login.add_argument("--phone", required=True, help="自然人手机号")
    run_full_login.add_argument("--password", help="自然人登录密码")
    run_full_login.add_argument("--password-env", help="从指定环境变量读取自然人登录密码")
    run_full_login.add_argument("--username", help="自然人用户名；默认与手机号相同")
    run_full_login.add_argument("--enterprise-phone", help="办税小号手机号；默认复用自然人手机号")
    run_full_login.add_argument("--enterprise-password", help="办税小号密码；默认复用自然人密码")
    run_full_login.add_argument("--enterprise-password-env", help="从指定环境变量读取办税小号密码")
    run_full_login.add_argument("--enterprise-username", help="办税小号用户名；默认与办税小号手机号相同")
    run_full_login.add_argument("--login-mode", type=int, default=14, help="企业多账号登录方式，默认 14")
    run_full_login.add_argument("--identity-type", default="BSY", help="目标企业身份类型，默认 BSY")
    run_full_login.add_argument("--nsrsbh", help="目标企业税号")
    run_full_login.add_argument("--org-name", help="目标企业名称")
    run_full_login.add_argument("--index", help="目标企业序号")
    run_full_login.add_argument("--natural-sms-code", help="自然人短信验证码")
    run_full_login.add_argument("--enterprise-sms-code", help="企业短信验证码")

    show_state = subparsers.add_parser("show-login-state", help="查看当前共享登录态")
    show_state.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    show_flow_state = subparsers.add_parser("show-flow-state", help="查看当前登录流程状态")
    show_flow_state.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    clear_state = subparsers.add_parser("clear-login-state", help="清理当前共享登录态")
    clear_state.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    clear_flow_state = subparsers.add_parser("clear-flow-state", help="清理当前登录流程状态")
    clear_flow_state.add_argument("--output", help="输出文件路径；不传则打印到标准输出")

    if _debug_commands_enabled():
        create_natural = subparsers.add_parser("create-natural-account", help="创建自然人账号")
        create_natural.add_argument("--area-code", required=True, help="地区代码，如 3100")
        create_natural.add_argument("--phone", required=True, help="手机号")
        create_natural.add_argument("--password", help="登录密码")
        create_natural.add_argument("--password-env", help="从指定环境变量读取登录密码")
        create_natural.add_argument("--username", help="用户名；默认与手机号相同")

        start_login = subparsers.add_parser("start-natural-login", help="发送自然人登录验证码")
        start_login.add_argument("--account-id", help="自然人 accountId")

        start_login_by_phone = subparsers.add_parser(
            "start-natural-login-by-phone",
            help="仅用手机号和密码发送自然人登录验证码",
        )
        start_login_by_phone.add_argument("--area-code", required=True, help="地区代码，如 3100")
        start_login_by_phone.add_argument("--phone", required=True, help="手机号")
        start_login_by_phone.add_argument("--password", help="登录密码")
        start_login_by_phone.add_argument("--password-env", help="从指定环境变量读取登录密码")
        start_login_by_phone.add_argument("--username", help="用户名；默认与手机号相同")

        verify_login = subparsers.add_parser("verify-natural-login", help="上传自然人登录验证码")
        verify_login.add_argument("--task-id", help="验证码任务ID")
        verify_login.add_argument("--sms-code", required=True, help="短信验证码")

        list_enterprises = subparsers.add_parser("list-enterprises", help="获取自然人企业列表")
        list_enterprises.add_argument("--natural-account-id", help="自然人 accountId")

        choose_enterprise = subparsers.add_parser("choose-enterprise", help="从最近一次企业列表中选择目标企业")
        choose_enterprise.add_argument("--nsrsbh", help="企业税号")
        choose_enterprise.add_argument("--name", help="企业名称")
        choose_enterprise.add_argument("--identity-type", help="身份类型，如 BSY/KPY")
        choose_enterprise.add_argument("--index", help="企业列表序号")

        subscribe = subparsers.add_parser("subscribe-enterprise-service", help="订购企业服务")
        subscribe.add_argument("--area-code", help="地区代码，如 3100")
        subscribe.add_argument("--org-name", "--nsrmc", dest="org_name", help="企业名称")
        subscribe.add_argument("--tax-number", "--nsrsbh", dest="tax_number", help="企业税号")

        create_multi = subparsers.add_parser("create-multi-account", help="创建企业多账号")
        create_multi.add_argument("--agg-org-id", help="企业 aggOrgId")
        create_multi.add_argument("--area-code", help="地区代码，如 3100")
        create_multi.add_argument("--phone", required=True, help="办税小号手机号")
        create_multi.add_argument("--password", help="办税小号密码")
        create_multi.add_argument("--password-env", help="从指定环境变量读取办税小号密码")
        create_multi.add_argument("--username", help="办税小号用户名；默认与手机号相同")
        create_multi.add_argument("--login-mode", type=int, default=14, help="多账号登录方式，默认 14")

        start_enterprise_login = subparsers.add_parser("start-enterprise-login", help="发送企业登录验证码")
        start_enterprise_login.add_argument("--agg-org-id", help="企业 aggOrgId")
        start_enterprise_login.add_argument("--account-id", help="企业多账号 accountId")

        verify_enterprise_login = subparsers.add_parser(
            "verify-enterprise-login",
            help="上传企业登录验证码，并自动写入共享登录态",
        )
        verify_enterprise_login.add_argument("--task-id", help="验证码任务ID")
        verify_enterprise_login.add_argument("--sms-code", required=True, help="短信验证码")
        verify_enterprise_login.add_argument("--agg-org-id", help="企业 aggOrgId")
        verify_enterprise_login.add_argument("--account-id", help="企业多账号 accountId")

        enterprise_ready = subparsers.add_parser(
            "login-enterprise-account",
            help="兼容旧流程：仅校验企业账号是否可直接办税，并自动写入共享登录态",
        )
        enterprise_ready.add_argument("--agg-org-id", required=True, help="企业 aggOrgId")
        enterprise_ready.add_argument("--account-id", required=True, help="企业多账号 accountId")

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

        if args.command == "check-config":
            _write_json(TaxLoginClient.from_config().check_connection())
            return 0

        if args.command == "show-login-state":
            _write_json(
                {
                    "stateFile": str(resolve_login_state_path(__file__)),
                    "state": read_login_state(__file__),
                },
                args.output,
            )
            return 0

        if args.command == "show-flow-state":
            _write_json(
                {
                    "stateFile": str(resolve_login_flow_state_path(__file__)),
                    "state": read_login_flow_state(__file__),
                },
                args.output,
            )
            return 0

        if args.command == "clear-login-state":
            cleared_path = clear_login_state(__file__)
            _write_json({"success": True, "stateFile": str(cleared_path), "message": "共享登录态已清理"}, args.output)
            return 0

        if args.command == "clear-flow-state":
            cleared_path = clear_login_flow_state(__file__)
            _write_json({"success": True, "stateFile": str(cleared_path), "message": "登录流程状态已清理"}, args.output)
            return 0

        client = TaxLoginClient.from_config()
        workflow = TaxLoginWorkflow(client)
        flow_state = read_login_flow_state(__file__)
        enterprise_subscription = flow_state.get("enterpriseSubscription", {})

        if args.command == "run-full-login":
            _write_json(_run_full_login(args, workflow))
            return 0

        if args.command == "create-natural-account":
            password = _resolve_secret(
                direct_value=args.password,
                env_var_name=args.password_env,
                field_name="自然人登录密码",
            )
            result = workflow.create_natural_person_account(
                area_code=args.area_code,
                phone=args.phone,
                password=password,
                username=args.username,
            )
            merge_login_flow_state(
                __file__,
                {
                    "context": {"areaCode": args.area_code, "phone": args.phone},
                    "natural": {
                        "accountId": result.get("account_id"),
                        "username": args.username or args.phone,
                        "loginMode": result.get("login_mode"),
                    },
                },
            )
            _write_json(result)
            return 0

        if args.command == "start-natural-login":
            result = workflow.start_natural_person_login(
                account_id=_resolve_value(
                    args.account_id,
                    flow_state.get("natural", {}).get("accountId"),
                    "account_id",
                )
            )
            merge_login_flow_state(
                __file__,
                {"naturalLogin": {"taskId": result.get("task_id"), "verified": bool(result.get("login_success"))}},
            )
            _write_json(result)
            return 0

        if args.command == "start-natural-login-by-phone":
            password = _resolve_secret(
                direct_value=args.password,
                env_var_name=args.password_env,
                field_name="自然人登录密码",
            )
            result = workflow.start_natural_person_login_by_phone(
                area_code=_resolve_value(args.area_code, flow_state.get("context", {}).get("areaCode"), "area_code"),
                phone=_resolve_value(args.phone, flow_state.get("context", {}).get("phone"), "phone"),
                password=password,
                username=args.username,
            )
            merge_login_flow_state(
                __file__,
                {
                    "context": {
                        "areaCode": args.area_code or flow_state.get("context", {}).get("areaCode"),
                        "phone": args.phone or flow_state.get("context", {}).get("phone"),
                    },
                    "naturalLogin": {"taskId": result.get("task_id"), "verified": False},
                },
            )
            _write_json(result)
            return 0

        if args.command == "verify-natural-login":
            result = workflow.verify_natural_person_login(
                task_id=_resolve_value(args.task_id, flow_state.get("naturalLogin", {}).get("taskId"), "task_id"),
                sms_code=args.sms_code,
            )
            merge_login_flow_state(__file__, {"naturalLogin": {"verified": True}})
            _write_json(result)
            return 0

        if args.command == "list-enterprises":
            result = workflow.list_enterprises(
                natural_account_id=_resolve_value(
                    args.natural_account_id,
                    flow_state.get("natural", {}).get("accountId"),
                    "natural_account_id",
                )
            )
            merge_login_flow_state(
                __file__,
                {"enterpriseList": {"items": result.get("enterprises", []), "total": result.get("total")}},
            )
            _write_json(result)
            return 0

        if args.command == "choose-enterprise":
            enterprises = flow_state.get("enterpriseList", {}).get("items") or []
            result = workflow.choose_target_enterprise(
                enterprises,
                nsrsbh=args.nsrsbh,
                name=args.name,
                identity_type=args.identity_type,
                index=args.index,
            )
            merge_login_flow_state(__file__, {"selectedEnterprise": result})
            _write_json({"success": True, "selected_enterprise": result})
            return 0

        if args.command == "subscribe-enterprise-service":
            selected_enterprise = flow_state.get("selectedEnterprise", {})
            result = workflow.subscribe_enterprise_service(
                area_code=_resolve_value(args.area_code, flow_state.get("context", {}).get("areaCode"), "area_code"),
                org_name=_resolve_value(args.org_name, selected_enterprise.get("org_name") or selected_enterprise.get("name"), "org_name"),
                tax_number=_resolve_value(args.tax_number, selected_enterprise.get("nsrsbh"), "tax_number"),
            )
            merge_login_flow_state(
                __file__,
                {
                    "enterpriseSubscription": {
                        "aggOrgId": result.get("agg_org_id"),
                        "orgId": result.get("org_id"),
                        "orgName": selected_enterprise.get("org_name") or selected_enterprise.get("name") or args.org_name,
                        "taxNumber": selected_enterprise.get("nsrsbh") or args.tax_number,
                    }
                },
            )
            _write_json(result)
            return 0

        if args.command == "create-multi-account":
            password = _resolve_secret(
                direct_value=args.password,
                env_var_name=args.password_env,
                field_name="办税小号密码",
            )
            result = workflow.create_multi_account(
                agg_org_id=_resolve_value(args.agg_org_id, flow_state.get("enterpriseSubscription", {}).get("aggOrgId"), "agg_org_id"),
                area_code=_resolve_value(args.area_code, flow_state.get("context", {}).get("areaCode"), "area_code"),
                phone=args.phone,
                password=password,
                username=args.username,
                login_mode=args.login_mode,
            )
            merge_login_flow_state(
                __file__,
                {
                    "enterpriseAccount": {
                        "aggOrgId": result.get("agg_org_id"),
                        "accountId": result.get("account_id"),
                        "phone": args.phone,
                        "username": args.username or args.phone,
                        "loginMode": args.login_mode,
                    }
                },
            )
            _write_json(result)
            return 0

        if args.command == "start-enterprise-login":
            result = workflow.start_enterprise_login(
                agg_org_id=_resolve_value(args.agg_org_id, flow_state.get("enterpriseAccount", {}).get("aggOrgId"), "agg_org_id"),
                account_id=_resolve_value(args.account_id, flow_state.get("enterpriseAccount", {}).get("accountId"), "account_id"),
                enterprise_context={
                    "orgId": enterprise_subscription.get("orgId"),
                    "orgName": enterprise_subscription.get("orgName"),
                    "nsrsbh": enterprise_subscription.get("taxNumber"),
                },
            )
            merge_login_flow_state(
                __file__,
                {"enterpriseLogin": {"taskId": result.get("task_id"), "verified": bool(result.get("login_success"))}},
            )
            _write_json(result)
            return 0

        if args.command == "verify-enterprise-login":
            result = workflow.verify_enterprise_login(
                task_id=_resolve_value(args.task_id, flow_state.get("enterpriseLogin", {}).get("taskId"), "task_id"),
                sms_code=args.sms_code,
                agg_org_id=_resolve_value(args.agg_org_id, flow_state.get("enterpriseAccount", {}).get("aggOrgId"), "agg_org_id"),
                account_id=_resolve_value(args.account_id, flow_state.get("enterpriseAccount", {}).get("accountId"), "account_id"),
                enterprise_context={
                    "orgId": enterprise_subscription.get("orgId"),
                    "orgName": enterprise_subscription.get("orgName"),
                    "nsrsbh": enterprise_subscription.get("taxNumber"),
                },
            )
            merge_login_flow_state(__file__, {"enterpriseLogin": {"verified": True}})
            _write_json(result)
            return 0

        if args.command == "login-enterprise-account":
            _write_json(
                workflow.login_enterprise_account(
                    agg_org_id=args.agg_org_id,
                    account_id=args.account_id,
                    enterprise_context={
                        "orgId": enterprise_subscription.get("orgId"),
                        "orgName": enterprise_subscription.get("orgName"),
                        "nsrsbh": enterprise_subscription.get("taxNumber"),
                    },
                )
            )
            return 0

        parser.print_help()
        return 1
    except (ConfigError, LoginStateError, TaxLoginError, ValueError, json.JSONDecodeError) as exc:
        _write_error_payload(exc)
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
