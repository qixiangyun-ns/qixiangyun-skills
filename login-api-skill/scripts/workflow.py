"""
Tax Login Skill - 7 步登录工作流

目标：
1. 严格按照“17 自然人 -> 企业列表 -> 订购 -> 14 企业账号 -> 企业登录”编排；
2. 自然人链路只允许使用 `accountId`；
3. 企业链路必须使用 `aggOrgId + accountId`；
4. 对上游不稳定响应做兼容归一化，避免把业务可继续场景误判成失败。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .client import TaxLoginClient
from .exceptions import TaxLoginError
from .login_state_support import save_login_state
from .login_task_support import (
    clear_pending_login_task,
    read_pending_login_task,
    save_pending_login_task,
)


class TaxLoginWorkflow:
    """税务登录工作流编排器。"""

    DEFAULT_NATURAL_LOGIN_MODE = 17
    DEFAULT_ENTERPRISE_LOGIN_MODE = 14
    DEFAULT_IDENTITY_TYPE = "BSY"

    def __init__(self, client: TaxLoginClient):
        self.client = client

    @staticmethod
    def _flow_payload(
        *,
        flow_status: str,
        final_success: bool,
        next_step: Optional[str],
        waiting_for_user_input: bool = False,
        user_input_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建统一状态机字段。"""
        payload: Dict[str, Any] = {
            "flow_status": flow_status,
            "final_success": final_success,
            "next_step": next_step,
            "waiting_for_user_input": waiting_for_user_input,
        }
        if user_input_kind:
            payload["user_input_kind"] = user_input_kind
        return payload

    @staticmethod
    def _normalize_string(value: Any, field_name: str) -> str:
        """标准化必填字符串。"""
        normalized = str(value or "").strip()
        if not normalized:
            raise TaxLoginError("INVALID_PARAM", f"{field_name} 不能为空")
        return normalized

    @staticmethod
    def _normalize_optional_string(value: Any) -> str:
        """标准化可选字符串。"""
        return str(value or "").strip()

    @staticmethod
    def _normalize_area_code(area_code: str) -> str:
        """将地区代码标准化为 2 位。"""
        normalized = str(area_code or "").strip()
        if not normalized.isdigit():
            raise TaxLoginError("INVALID_AREA_CODE", "地区代码必须为数字字符串")
        if len(normalized) == 2:
            return normalized
        if len(normalized) == 4 and normalized.endswith("00"):
            return normalized[:2]
        raise TaxLoginError("INVALID_AREA_CODE", "地区代码只支持 2 位或以 00 结尾的 4 位编码")

    @staticmethod
    def _normalize_app_area_code(area_code: str) -> str:
        """将自然人 APP 登录使用的地区代码标准化为 4 位。"""
        normalized = str(area_code or "").strip()
        if not normalized.isdigit():
            raise TaxLoginError("INVALID_AREA_CODE", "地区代码必须为数字字符串")
        if len(normalized) == 2:
            return f"{normalized}00"
        if len(normalized) == 4:
            return normalized
        raise TaxLoginError("INVALID_AREA_CODE", "地区代码只支持 2 位或 4 位编码")

    @staticmethod
    def _strip_org_fields(value: Any) -> Any:
        """递归移除自然人链路中不应出现的组织字段。"""
        forbidden_keys = {
            "aggOrgId",
            "agg_org_id",
            "orgId",
            "org_id",
            "aggOrgName",
            "agg_org_name",
        }
        if isinstance(value, dict):
            return {
                key: TaxLoginWorkflow._strip_org_fields(item)
                for key, item in value.items()
                if key not in forbidden_keys
            }
        if isinstance(value, list):
            return [TaxLoginWorkflow._strip_org_fields(item) for item in value]
        return value

    @staticmethod
    def _is_success_code(result: Dict[str, Any]) -> bool:
        """判断响应是否可视为成功。"""
        code = str(result.get("code", "")).strip().upper()
        success = result.get("success")
        if any(key in result for key in ("taskId", "task_id", "smsCode", "sms_code")):
            return True
        if code in {"2000", "SUCCESS"}:
            return True
        if success is True and not code:
            return True
        return success is True and code not in {"PARAMETER_ERROR", "ERROR", "FAIL"}

    @staticmethod
    def _extract_response_data(result: Dict[str, Any]) -> Dict[str, Any]:
        """统一提取响应数据。"""
        data = result.get("data")
        if isinstance(data, dict):
            return data
        if data is None:
            return {}
        if isinstance(result, dict):
            top_level_payload = {
                key: value
                for key, value in result.items()
                if key not in {"code", "success", "message", "timestamp", "reqId", "data"}
            }
            if top_level_payload:
                return top_level_payload
        return {}

    @staticmethod
    def _extract_task_id(result: Dict[str, Any]) -> str:
        """从标准或裸响应中提取 taskId。"""
        data = TaxLoginWorkflow._extract_response_data(result)
        task_id = data.get("taskId") or data.get("task_id") or result.get("taskId")
        return str(task_id).strip() if task_id not in (None, "") else ""

    @staticmethod
    def _extract_message(result: Dict[str, Any], default: str) -> str:
        """提取更贴近业务的提示文案。"""
        data = TaxLoginWorkflow._extract_response_data(result)
        message = (
            data.get("msg")
            or data.get("message")
            or result.get("message")
            or default
        )
        return str(message or default)

    def _ensure_success(self, result: Dict[str, Any], action: str) -> Dict[str, Any]:
        """校验通用成功响应。"""
        if self._is_success_code(result):
            return result
        raise TaxLoginError(
            str(result.get("code", "UNKNOWN")),
            self._extract_message(result, f"{action}失败"),
            self._extract_response_data(result),
        )

    @staticmethod
    def _build_login_mode_mismatch_error(
        exc: "TaxLoginError", raw: Dict[str, Any]
    ) -> Dict[str, Any]:
        """构建登录方式不匹配的标准错误响应。"""
        return {
            "success": False,
            "ready": False,
            "source": "none",
            "message": "当前企业账号的登录方式与税局要求不匹配。",
            "error": {"code": exc.code, "message": exc.message},
            "nextAction": {
                "command": "create-multi-account",
                "message": (
                    "请重新创建企业多账号，并将 login_mode 设置为 14 或 15；"
                    "如果走企业自传验证码流程，请优先使用 14；"
                    "只有代理业务场景才建议使用 15。"
                ),
                "suggestedArgs": {"login_mode": 14},
            },
            "raw": raw,
        }

    def _build_enterprise_login_state_extra(
        self,
        *,
        enterprise_context: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建企业共享登录态扩展字段。"""
        context = enterprise_context or {}
        payload = {
            "loginMode": self.DEFAULT_ENTERPRISE_LOGIN_MODE,
            "orgId": self._normalize_optional_string(
                context.get("orgId") or context.get("aggOrgId")
            ),
            "orgName": self._normalize_optional_string(context.get("orgName")),
            "nsrsbh": self._normalize_optional_string(context.get("nsrsbh")),
        }
        if task_id:
            payload["taskId"] = self._normalize_optional_string(task_id)
        return payload

    def _save_enterprise_login_state(
        self,
        *,
        agg_org_id: str,
        account_id: str,
        source: str,
        enterprise_context: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """统一写入企业共享登录态。"""
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
        normalized_account_id = self._normalize_string(account_id, "accountId")
        extra = self._build_enterprise_login_state_extra(
            enterprise_context=enterprise_context,
            task_id=task_id,
        )
        if not extra.get("orgId"):
            extra["orgId"] = normalized_agg_org_id
        return save_login_state(
            __file__,
            agg_org_id=normalized_agg_org_id,
            account_id=normalized_account_id,
            source=source,
            extra=extra,
        )

    def create_natural_person_account(
        self,
        area_code: str,
        phone: str,
        password: str,
        username: Optional[str] = None,
        identity_type: str = DEFAULT_IDENTITY_TYPE,
        login_mode: int = DEFAULT_NATURAL_LOGIN_MODE,
    ) -> Dict[str, Any]:
        """第 1 步：创建自然人账号。"""
        dq = self._normalize_area_code(area_code)
        normalized_phone = self._normalize_string(phone, "手机号")
        normalized_password = self._normalize_string(password, "密码")
        normalized_username = self._normalize_string(username or phone, "用户名")

        result = self.client.create_account_record(
            agg_org_id=None,
            dq=dq,
            username=normalized_username,
            phone=normalized_phone,
            password=normalized_password,
            identity_type=identity_type,
            login_mode=login_mode,
        )

        data = self._extract_response_data(result)
        message = self._extract_message(result, "自然人账号创建成功")
        account_id = self._normalize_optional_string(data.get("accountId"))
        if account_id and any(keyword in message for keyword in ("已存在", "已经存在")):
            return {
                "success": True,
                "existing_account": True,
                "account_id": account_id,
                "identity_type": self._normalize_optional_string(data.get("sflx")),
                "login_mode": data.get("dlfs", login_mode),
                "message": "自然人账号已存在，已复用 accountId",
                "raw": self._strip_org_fields(result),
                **self._flow_payload(
                    flow_status="NATURAL_ACCOUNT_READY",
                    final_success=False,
                    next_step="start-natural-login",
                ),
            }

        success_result = self._ensure_success(result, "自然人创建账号")
        normalized_data = self._extract_response_data(success_result)
        created_account_id = self._normalize_optional_string(normalized_data.get("accountId"))
        if not created_account_id:
            raise TaxLoginError("INVALID_RESPONSE", "自然人创建账号未返回 accountId", normalized_data)
        return {
            "success": True,
            "existing_account": False,
            "account_id": created_account_id,
            "identity_type": self._normalize_optional_string(normalized_data.get("sflx")),
            "login_mode": normalized_data.get("dlfs", login_mode),
            "message": "自然人账号创建成功",
            "raw": self._strip_org_fields(success_result),
            **self._flow_payload(
                flow_status="NATURAL_ACCOUNT_READY",
                final_success=False,
                next_step="start-natural-login",
            ),
        }

    def start_natural_person_login(self, account_id: str) -> Dict[str, Any]:
        """第 2 步-1：自然人登录发验证码，只允许传 accountId。"""
        normalized_account_id = self._normalize_string(account_id, "accountId")
        result = self.client.send_etax_login_sms(
            account_id=normalized_account_id,
            agg_org_id=None,
        )
        success_result = self._ensure_success(result, "发送自然人登录验证码")
        task_id = self._extract_task_id(success_result)
        if not task_id:
            return {
                "success": True,
                "need_verify": False,
                "login_success": True,
                "task_id": "",
                "account_id": normalized_account_id,
                "message": self._extract_message(success_result, "自然人登录成功"),
                "raw": self._strip_org_fields(success_result),
                **self._flow_payload(
                    flow_status="NATURAL_AUTHENTICATED",
                    final_success=False,
                    next_step="list-enterprises",
                ),
            }

        save_pending_login_task(
            __file__,
            task_id=task_id,
            flow="natural_remote",
            extra={"accountId": normalized_account_id},
        )
        return {
            "success": True,
            "need_verify": True,
            "task_id": task_id,
            "account_id": normalized_account_id,
            "message": self._extract_message(success_result, "验证码已发送"),
            "raw": self._strip_org_fields(success_result),
            **self._flow_payload(
                flow_status="WAIT_NATURAL_SMS",
                final_success=False,
                next_step="verify-natural-login",
                waiting_for_user_input=True,
                user_input_kind="natural_sms_code",
            ),
        }

    def start_natural_person_login_by_phone(
        self,
        area_code: str,
        phone: str,
        password: str,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """自然人手机号直登发验证码。"""
        normalized_area_code = self._normalize_app_area_code(area_code)
        normalized_phone = self._normalize_string(phone, "手机号")
        normalized_password = self._normalize_string(password, "密码")
        normalized_username = self._normalize_string(username or phone, "用户名")

        result = self.client.login_flow_step1_send_sms(
            area_code=normalized_area_code,
            phone=normalized_phone,
            password=normalized_password,
        )
        if not result.get("success"):
            raise TaxLoginError(
                str(result.get("code", "UNKNOWN")),
                str(result.get("message", "自然人登录验证码发送失败")),
            )

        task_id = self._normalize_optional_string(result.get("task_id"))
        if task_id:
            save_pending_login_task(
                __file__,
                task_id=task_id,
                flow="natural_phone",
                extra={
                    "areaCode": normalized_area_code,
                    "phone": normalized_phone,
                    "username": normalized_username,
                },
            )
        return {
            "success": True,
            "need_verify": bool(result.get("need_verify", False)),
            "task_id": task_id,
            "phone": normalized_phone,
            "username": normalized_username,
            "message": str(result.get("message") or "验证码已发送"),
            "raw": self._strip_org_fields(result),
            **self._flow_payload(
                flow_status="WAIT_NATURAL_SMS" if task_id else "NATURAL_AUTHENTICATED",
                final_success=False,
                next_step="verify-natural-login" if task_id else "list-enterprises",
                waiting_for_user_input=bool(task_id),
                user_input_kind="natural_sms_code" if task_id else None,
            ),
        }

    def verify_natural_person_login(self, task_id: str, sms_code: str) -> Dict[str, Any]:
        """第 2 步-2：自然人验码。"""
        normalized_task_id = self._normalize_string(task_id, "taskId")
        normalized_sms_code = self._normalize_string(sms_code, "验证码")
        pending_task = read_pending_login_task(__file__, task_id=normalized_task_id)

        if pending_task and pending_task.get("flow") == "enterprise_remote":
            raise TaxLoginError(
                "INVALID_TASK_FLOW",
                "当前 taskId 对应企业登录流程，请改用 `verify-enterprise-login`。",
            )

        if pending_task and pending_task.get("flow") == "natural_phone":
            result = self.client.verify_sms(
                task_id=normalized_task_id,
                sms_code=normalized_sms_code,
            )
        else:
            result = self.client.upload_etax_login_sms(
                task_id=normalized_task_id,
                sms_code=normalized_sms_code,
            )

        success_result = self._ensure_success(result, "自然人登录验码")
        clear_pending_login_task(__file__)
        return {
            "success": True,
            "login_success": True,
            "message": self._extract_message(success_result, "自然人登录成功"),
            "raw": self._strip_org_fields(success_result),
            **self._flow_payload(
                flow_status="NATURAL_AUTHENTICATED",
                final_success=False,
                next_step="list-enterprises",
            ),
        }

    def list_enterprises(self, natural_account_id: str) -> Dict[str, Any]:
        """第 3 步：只用自然人 accountId 获取企业列表。"""
        normalized_account_id = self._normalize_string(natural_account_id, "accountId")
        result = self.client.query_nature_org_list(
            account_id=normalized_account_id,
            agg_org_id=None,
        )
        success_result = self._ensure_success(result, "获取企业列表")
        data = success_result.get("data", [])
        items = data if isinstance(data, list) else (data.get("list") or data.get("details") or [])

        enterprises: List[Dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            enterprises.append(
                {
                    "name": self._normalize_optional_string(
                        item.get("nsrmc") or item.get("name") or item.get("aggOrgName")
                    ),
                    "org_name": self._normalize_optional_string(
                        item.get("nsrmc") or item.get("name") or item.get("aggOrgName")
                    ),
                    "nsrsbh": self._normalize_optional_string(item.get("nsrsbh")),
                    "identity_type": self._normalize_optional_string(item.get("sflx")),
                    "status": self._normalize_optional_string(item.get("glzt")),
                    "index": self._normalize_optional_string(item.get("xh")),
                }
            )

        return {
            "success": True,
            "account_id": normalized_account_id,
            "total": len(enterprises),
            "enterprises": enterprises,
            "raw": self._strip_org_fields(success_result),
            **self._flow_payload(
                flow_status="ENTERPRISES_DISCOVERED",
                final_success=False,
                next_step="choose-enterprise",
            ),
        }

    def choose_target_enterprise(
        self,
        enterprises: Sequence[Dict[str, str]],
        *,
        nsrsbh: Optional[str] = None,
        name: Optional[str] = None,
        identity_type: Optional[str] = None,
        index: Optional[str] = None,
    ) -> Dict[str, str]:
        """第 4 步：从企业列表中选择目标企业。"""
        normalized_nsrsbh = self._normalize_optional_string(nsrsbh)
        normalized_name = self._normalize_optional_string(name)
        normalized_identity_type = self._normalize_optional_string(identity_type).upper()
        normalized_index = self._normalize_optional_string(index)
        if not any([normalized_nsrsbh, normalized_name, normalized_identity_type, normalized_index]):
            raise TaxLoginError("INVALID_PARAM", "必须提供企业税号、企业名称、身份类型或列表序号")

        matches: List[Dict[str, str]] = []
        for item in enterprises:
            item_nsrsbh = self._normalize_optional_string(item.get("nsrsbh"))
            item_name = self._normalize_optional_string(item.get("name"))
            item_identity = self._normalize_optional_string(item.get("identity_type")).upper()
            item_index = self._normalize_optional_string(item.get("index"))
            if normalized_nsrsbh and item_nsrsbh != normalized_nsrsbh:
                continue
            if normalized_name and item_name != normalized_name:
                continue
            if normalized_identity_type and item_identity != normalized_identity_type:
                continue
            if normalized_index and item_index != normalized_index:
                continue
            matches.append(item)

        if not matches:
            raise TaxLoginError("NOT_FOUND", "未找到匹配的目标企业")
        if len(matches) > 1:
            raise TaxLoginError("AMBIGUOUS_ENTERPRISE", "匹配到多个企业，请补充身份类型或列表序号")
        return matches[0]

    def subscribe_enterprise_service(
        self,
        area_code: str,
        org_name: str,
        tax_number: str,
        product_codes: str = "0020",
    ) -> Dict[str, Any]:
        """第 5 步：企业产品订购，返回企业 orgId。"""
        dq = self._normalize_area_code(area_code)
        normalized_name = self._normalize_string(org_name, "企业名称")
        normalized_tax_number = self._normalize_string(tax_number, "企业税号")

        result = self.client.order_product(
            nsrsbh=normalized_tax_number,
            org_name=normalized_name,
            dq=dq,
            product_codes=product_codes,
        )
        success_result = self._ensure_success(result, "企业服务订购")
        data = self._extract_response_data(success_result)
        agg_org_id = self._normalize_optional_string(data.get("aggOrgId"))
        if not agg_org_id:
            raise TaxLoginError("INVALID_RESPONSE", "企业服务订购未返回 aggOrgId", data)
        return {
            "success": True,
            "agg_org_id": agg_org_id,
            "org_id": agg_org_id,
            "message": self._extract_message(success_result, "企业订购成功"),
            "raw": success_result,
            **self._flow_payload(
                flow_status="ENTERPRISE_SUBSCRIBED",
                final_success=False,
                next_step="create-multi-account",
            ),
        }

    def create_multi_account(
        self,
        agg_org_id: str,
        area_code: str,
        phone: str,
        password: str,
        username: Optional[str] = None,
        identity_type: str = DEFAULT_IDENTITY_TYPE,
        login_mode: int = DEFAULT_ENTERPRISE_LOGIN_MODE,
    ) -> Dict[str, Any]:
        """第 6 步：用企业 orgId 创建 dlfs=14 多账号。"""
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
        dq = self._normalize_area_code(area_code)
        normalized_phone = self._normalize_string(phone, "手机号")
        normalized_password = self._normalize_string(password, "密码")
        normalized_username = self._normalize_string(username or phone, "用户名")

        result = self.client.create_account_record(
            agg_org_id=normalized_agg_org_id,
            dq=dq,
            username=normalized_username,
            phone=normalized_phone,
            password=normalized_password,
            identity_type=identity_type,
            login_mode=login_mode,
        )
        data = self._extract_response_data(result)
        message = self._extract_message(result, "多账号创建成功")
        existing_account_id = self._normalize_optional_string(data.get("accountId"))
        if existing_account_id and any(keyword in message for keyword in ("已存在", "已经存在")):
            payload = {
                "success": True,
                "existing_account": True,
                "account_id": existing_account_id,
                "agg_org_id": self._normalize_optional_string(data.get("aggOrgId")) or normalized_agg_org_id,
                "identity_type": self._normalize_optional_string(data.get("sflx")),
                "login_mode": data.get("dlfs", login_mode),
                "message": "企业多账号已存在，已复用 accountId",
                "is_multi_account": True,
                "raw": result,
            }
            payload.update(
                self._flow_payload(
                    flow_status="ENTERPRISE_ACCOUNT_READY",
                    final_success=False,
                    next_step="start-enterprise-login",
                )
            )
            return payload

        success_result = self._ensure_success(result, "多账号创建")
        data = self._extract_response_data(success_result)
        account_id = self._normalize_optional_string(data.get("accountId"))
        if not account_id:
            raise TaxLoginError("INVALID_RESPONSE", "多账号创建未返回 accountId", data)
        payload = {
            "success": True,
            "account_id": account_id,
            "agg_org_id": self._normalize_optional_string(data.get("aggOrgId")) or normalized_agg_org_id,
            "identity_type": self._normalize_optional_string(data.get("sflx")),
            "login_mode": data.get("dlfs", login_mode),
            "message": "多账号创建成功",
            "is_multi_account": True,
            "raw": success_result,
        }
        if login_mode not in (14, 15):
            payload["nextAction"] = {
                "message": (
                    "当前多账号使用的 login_mode 不是 14 或 15。"
                    "如果后续企业登录发送验证码时报“登录方式必须是14或者15”，"
                    "请重新创建多账号并将 login_mode 改为 14；"
                    "只有代理业务场景才建议使用 15。"
                ),
                "suggested_login_mode": 14,
            }
        payload.update(
            self._flow_payload(
                flow_status="ENTERPRISE_ACCOUNT_READY",
                final_success=False,
                next_step="start-enterprise-login",
            )
        )
        return payload

    def start_enterprise_login(
        self,
        agg_org_id: str,
        account_id: str,
        enterprise_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """第 7 步-1：企业登录发码；若上游直接成功则立即落登录态。"""
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
        normalized_account_id = self._normalize_string(account_id, "accountId")
        result = self.client.send_etax_login_sms(
            account_id=normalized_account_id,
            agg_org_id=normalized_agg_org_id,
        )
        success_result = self._ensure_success(result, "发送企业登录验证码")
        task_id = self._extract_task_id(success_result)
        if not task_id:
            clear_pending_login_task(__file__)
            saved_state = self._save_enterprise_login_state(
                agg_org_id=normalized_agg_org_id,
                account_id=normalized_account_id,
                source="enterprise_direct",
                enterprise_context=enterprise_context,
            )
            return {
                "success": True,
                "need_verify": False,
                "login_success": True,
                "task_id": "",
                "agg_org_id": normalized_agg_org_id,
                "account_id": normalized_account_id,
                "message": self._extract_message(success_result, "企业登录成功"),
                "state_file": saved_state["state_file"],
                "login_state": saved_state["state"],
                "raw": success_result,
                **self._flow_payload(
                    flow_status="ENTERPRISE_AUTHENTICATED",
                    final_success=True,
                    next_step=None,
                ),
            }

        save_pending_login_task(
            __file__,
            task_id=task_id,
            flow="enterprise_remote",
            extra={
                "aggOrgId": normalized_agg_org_id,
                "accountId": normalized_account_id,
            },
        )
        return {
            "success": True,
            "need_verify": True,
            "task_id": task_id,
            "agg_org_id": normalized_agg_org_id,
            "account_id": normalized_account_id,
            "message": self._extract_message(success_result, "企业登录验证码已发送"),
            "raw": success_result,
            **self._flow_payload(
                flow_status="WAIT_ENTERPRISE_SMS",
                final_success=False,
                next_step="verify-enterprise-login",
                waiting_for_user_input=True,
                user_input_kind="enterprise_sms_code",
            ),
        }

    def verify_enterprise_login(
        self,
        task_id: str,
        sms_code: str,
        agg_org_id: str,
        account_id: str,
        enterprise_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """第 7 步-2：企业验码并写共享登录态。"""
        normalized_task_id = self._normalize_string(task_id, "taskId")
        normalized_sms_code = self._normalize_string(sms_code, "验证码")
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
        normalized_account_id = self._normalize_string(account_id, "accountId")

        result = self.client.upload_etax_login_sms(
            task_id=normalized_task_id,
            sms_code=normalized_sms_code,
        )
        success_result = self._ensure_success(result, "企业登录验码")
        clear_pending_login_task(__file__)
        saved_state = self._save_enterprise_login_state(
            agg_org_id=normalized_agg_org_id,
            account_id=normalized_account_id,
            source="enterprise_sms",
            enterprise_context=enterprise_context,
            task_id=normalized_task_id,
        )
        return {
            "success": True,
            "login_success": True,
            "message": self._extract_message(success_result, "企业登录成功"),
            "state_file": saved_state["state_file"],
            "login_state": saved_state["state"],
            "raw": success_result,
            **self._flow_payload(
                flow_status="ENTERPRISE_AUTHENTICATED",
                final_success=True,
                next_step=None,
            ),
        }

    def login_enterprise_account(
        self,
        agg_org_id: str,
        account_id: str,
        enterprise_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """兼容能力：检查企业账号是否已有可复用登录态。"""
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
        normalized_account_id = self._normalize_string(account_id, "accountId")

        cache_result = self.client.check_cache(
            agg_org_id=normalized_agg_org_id,
            account_id=normalized_account_id,
        )
        try:
            self._ensure_success(cache_result, "校验税局缓存")
        except TaxLoginError as exc:
            if exc.code == "4000" and "14或者15" in exc.message:
                return self._build_login_mode_mismatch_error(exc, cache_result)
            raise

        if bool(cache_result.get("data")):
            saved_state = self._save_enterprise_login_state(
                agg_org_id=normalized_agg_org_id,
                account_id=normalized_account_id,
                source="cache",
                enterprise_context=enterprise_context,
            )
            return {
                "success": True,
                "ready": True,
                "source": "cache",
                "message": "企业账号缓存有效，可直接开展办税业务",
                "state_file": saved_state["state_file"],
                "login_state": saved_state["state"],
                "raw": cache_result,
                **self._flow_payload(
                    flow_status="ENTERPRISE_AUTHENTICATED",
                    final_success=True,
                    next_step=None,
                ),
            }

        quick_login_result = self.client.check_app_login(
            agg_org_id=normalized_agg_org_id,
            account_id=normalized_account_id,
        )
        try:
            self._ensure_success(quick_login_result, "校验税局快速登录")
        except TaxLoginError as exc:
            if exc.code == "4000" and "14或者15" in exc.message:
                return self._build_login_mode_mismatch_error(exc, quick_login_result)
            raise

        if bool(quick_login_result.get("data")):
            saved_state = self._save_enterprise_login_state(
                agg_org_id=normalized_agg_org_id,
                account_id=normalized_account_id,
                source="quick_login",
                enterprise_context=enterprise_context,
            )
            return {
                "success": True,
                "ready": True,
                "source": "quick_login",
                "message": "企业账号支持快速登录，可直接开展办税业务",
                "state_file": saved_state["state_file"],
                "login_state": saved_state["state"],
                "raw": quick_login_result,
                **self._flow_payload(
                    flow_status="ENTERPRISE_AUTHENTICATED",
                    final_success=True,
                    next_step=None,
                ),
            }

        return {
            "success": True,
            "ready": False,
            "source": "none",
            "message": "当前企业账号尚未形成可直接复用的登录态，请继续走企业短信登录。",
            "raw": {
                "check_cache": cache_result,
                "check_app_login": quick_login_result,
            },
            **self._flow_payload(
                flow_status="ENTERPRISE_LOGIN_REQUIRED",
                final_success=False,
                next_step="start-enterprise-login",
            ),
        }
