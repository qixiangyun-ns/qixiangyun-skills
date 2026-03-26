"""
Tax Login Skill - 7步登录工作流

将“自然人创建账号 -> 自然人登录 -> 获取企业列表 -> 企业订购
-> 多账号创建 -> 企业账号就绪校验”封装为可复用的高层流程。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

from login_state import save_login_state

from .client import TaxLoginClient
from .exceptions import TaxLoginError


class TaxLoginWorkflow:
    """
    税务登录工作流编排器。

    目标：
    1. 将低层 API 拼成用户可理解的 7 步流程；
    2. 对输入参数做轻量校验和标准化；
    3. 返回稳定的结构化结果，便于 Skill 或业务层继续消费。
    """

    DEFAULT_NATURAL_LOGIN_MODE = 15
    DEFAULT_ENTERPRISE_LOGIN_MODE = 9
    DEFAULT_IDENTITY_TYPE = "BSY"

    def __init__(self, client: TaxLoginClient):
        self.client = client

    @staticmethod
    def _normalize_area_code(area_code: str) -> str:
        """
        将地区代码标准化为 2 位地区码。

        支持输入 `33` 或 `3300` 两种形式。
        """
        normalized = str(area_code).strip()
        if not normalized.isdigit():
            raise TaxLoginError("INVALID_AREA_CODE", "地区代码必须为数字字符串")
        if len(normalized) == 2:
            return normalized
        if len(normalized) == 4 and normalized.endswith("00"):
            return normalized[:2]
        raise TaxLoginError(
            "INVALID_AREA_CODE",
            "地区代码只支持 2 位或以 00 结尾的 4 位编码",
        )

    @staticmethod
    def _normalize_string(value: Any, field_name: str) -> str:
        """标准化非空字符串。"""
        if value is None:
            raise TaxLoginError("INVALID_PARAM", f"{field_name} 不能为空")
        normalized = str(value).strip()
        if not normalized:
            raise TaxLoginError("INVALID_PARAM", f"{field_name} 不能为空")
        return normalized

    @staticmethod
    def _ensure_success(result: Dict[str, Any], action: str) -> Dict[str, Any]:
        """校验 API 是否执行成功。"""
        if result.get("code") in ("SUCCESS", "2000") and result.get("success", True) is not False:
            return result
        raise TaxLoginError(
            str(result.get("code", "UNKNOWN")),
            result.get("message", f"{action}失败"),
            result.get("data") if isinstance(result.get("data"), dict) else {},
        )

    @staticmethod
    def _extract_account_data(result: Dict[str, Any], action: str) -> Dict[str, Any]:
        """提取账号类接口的核心返回字段。"""
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise TaxLoginError("INVALID_RESPONSE", f"{action}返回格式错误")
        account_id = data.get("accountId")
        agg_org_id = data.get("aggOrgId")
        if account_id in (None, ""):
            raise TaxLoginError("INVALID_RESPONSE", f"{action}未返回 accountId")
        return {
            "account_id": str(account_id),
            "agg_org_id": "" if agg_org_id in (None, "") else str(agg_org_id),
            "identity_type": str(data.get("sflx", "")),
            "login_mode": data.get("dlfs"),
            "raw": result,
        }

    def create_natural_person_account(
        self,
        area_code: str,
        phone: str,
        password: str,
        username: Optional[str] = None,
        identity_type: str = "BSY",
        login_mode: int = 15,
    ) -> Dict[str, Any]:
        """
        第1步：自然人创建账号。

        说明：
        - 使用“登录业务（新）/账号创建”
        - 默认按代理业务登录 `dlfs=15` 创建自然人账号
        - `aggOrgId=0` 表示先创建自然人侧平台账号，再进入后续代理流程
        """
        dq = self._normalize_area_code(area_code)
        normalized_phone = self._normalize_string(phone, "手机号")
        normalized_password = self._normalize_string(password, "密码")
        normalized_username = self._normalize_string(username or phone, "用户名")

        result = self.client.create_account_record(
            agg_org_id=0,
            dq=dq,
            username=normalized_username,
            phone=normalized_phone,
            password=normalized_password,
            identity_type=identity_type,
            login_mode=login_mode,
        )
        success_result = self._ensure_success(result, "自然人创建账号")
        account_data = self._extract_account_data(success_result, "自然人创建账号")
        account_data["message"] = "自然人账号创建成功"
        return account_data

    def start_natural_person_login(
        self,
        agg_org_id: str,
        account_id: str,
    ) -> Dict[str, Any]:
        """
        第2步-1：发送自然人登录验证码。
        """
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
        normalized_account_id = self._normalize_string(account_id, "accountId")

        result = self.client.send_etax_login_sms(
            agg_org_id=normalized_agg_org_id,
            account_id=normalized_account_id,
        )
        success_result = self._ensure_success(result, "发送自然人登录验证码")
        data = success_result.get("data", {})
        if not isinstance(data, dict):
            raise TaxLoginError("INVALID_RESPONSE", "发送验证码返回格式错误")

        task_id = data.get("taskId") or data.get("task_id")
        if task_id in (None, ""):
            raise TaxLoginError("INVALID_RESPONSE", "发送验证码未返回 taskId")

        return {
            "success": True,
            "task_id": str(task_id),
            "agg_org_id": normalized_agg_org_id,
            "account_id": normalized_account_id,
            "message": data.get("msg") or data.get("message") or "验证码已发送",
            "raw": success_result,
        }

    def verify_natural_person_login(
        self,
        task_id: str,
        sms_code: str,
    ) -> Dict[str, Any]:
        """
        第2步-2：上传验证码，完成自然人登录。
        """
        normalized_task_id = self._normalize_string(task_id, "taskId")
        normalized_sms_code = self._normalize_string(sms_code, "验证码")

        result = self.client.upload_etax_login_sms(
            task_id=normalized_task_id,
            sms_code=normalized_sms_code,
        )
        success_result = self._ensure_success(result, "自然人登录验码")
        return {
            "success": True,
            "login_success": True,
            "message": success_result.get("message") or "自然人登录成功",
            "raw": success_result,
        }

    def list_enterprises(
        self,
        natural_agg_org_id: str,
        natural_account_id: str,
    ) -> Dict[str, Any]:
        """
        第3步：获取自然人托管企业列表。
        """
        normalized_agg_org_id = self._normalize_string(natural_agg_org_id, "aggOrgId")
        normalized_account_id = self._normalize_string(natural_account_id, "accountId")

        result = self.client.query_nature_org_list(
            account_id=normalized_account_id,
            agg_org_id=normalized_agg_org_id,
        )
        success_result = self._ensure_success(result, "获取企业列表")
        data = success_result.get("data", [])

        details: Iterable[Any]
        if isinstance(data, dict):
            details = data.get("details") or data.get("list") or []
        elif isinstance(data, list):
            details = data
        else:
            details = []

        enterprises: List[Dict[str, str]] = []
        for item in details:
            if not isinstance(item, dict):
                continue
            enterprises.append(
                {
                    "name": str(item.get("name") or item.get("aggOrgName") or ""),
                    "nsrsbh": str(item.get("nsrsbh") or ""),
                    "identity_type": str(item.get("sflx") or ""),
                }
            )

        return {
            "success": True,
            "total": len(enterprises),
            "enterprises": enterprises,
            "raw": success_result,
        }

    def choose_target_enterprise(
        self,
        enterprises: Sequence[Dict[str, str]],
        *,
        nsrsbh: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        第4步：从企业列表中选择目标企业。
        """
        normalized_nsrsbh = str(nsrsbh or "").strip()
        normalized_name = str(name or "").strip()
        if not normalized_nsrsbh and not normalized_name:
            raise TaxLoginError("INVALID_PARAM", "必须提供企业税号或企业名称")

        matches: List[Dict[str, str]] = []
        for item in enterprises:
            if normalized_nsrsbh and item.get("nsrsbh") == normalized_nsrsbh:
                matches.append(item)
                continue
            if normalized_name and item.get("name") == normalized_name:
                matches.append(item)

        if not matches:
            raise TaxLoginError("NOT_FOUND", "未找到匹配的目标企业")
        if len(matches) > 1:
            raise TaxLoginError("AMBIGUOUS_ENTERPRISE", "匹配到多个企业，请优先传入企业税号")
        return matches[0]

    def subscribe_enterprise_service(
        self,
        area_code: str,
        org_name: str,
        tax_number: str,
        product_codes: str = "0020",
    ) -> Dict[str, Any]:
        """
        第5步：企业服务订购。
        """
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
        data = success_result.get("data", {})
        if not isinstance(data, dict):
            raise TaxLoginError("INVALID_RESPONSE", "企业服务订购返回格式错误")

        agg_org_id = data.get("aggOrgId")
        if agg_org_id in (None, ""):
            raise TaxLoginError("INVALID_RESPONSE", "企业服务订购未返回 aggOrgId")

        return {
            "success": True,
            "agg_org_id": str(agg_org_id),
            "org_id": str(agg_org_id),
            "message": success_result.get("message") or "企业订购成功",
            "raw": success_result,
        }

    def create_multi_account(
        self,
        agg_org_id: str,
        area_code: str,
        phone: str,
        password: str,
        username: Optional[str] = None,
        identity_type: str = "BSY",
        login_mode: int = 9,
    ) -> Dict[str, Any]:
        """
        第6步：创建企业多账号。

        默认 `dlfs=9`，适用于办税小号企业业务登录。
        """
        dq = self._normalize_area_code(area_code)
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
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
        success_result = self._ensure_success(result, "多账号创建")
        account_data = self._extract_account_data(success_result, "多账号创建")
        account_data["message"] = "多账号创建成功"
        account_data["is_multi_account"] = True
        return account_data

    def login_enterprise_account(
        self,
        agg_org_id: str,
        account_id: str,
    ) -> Dict[str, Any]:
        """
        第7步：企业账号登录就绪校验。

        说明：
        - 先校验缓存是否有效；
        - 如果缓存未命中，再校验是否能快速登录；
        - 该步骤不主动发验证码，符合“企业账号登录无需再次补输验证码”的目标场景。
        """
        normalized_agg_org_id = self._normalize_string(agg_org_id, "aggOrgId")
        normalized_account_id = self._normalize_string(account_id, "accountId")

        cache_result = self.client.check_cache(
            agg_org_id=normalized_agg_org_id,
            account_id=normalized_account_id,
        )
        self._ensure_success(cache_result, "校验税局缓存")
        if bool(cache_result.get("data")):
            saved_state = save_login_state(
                __file__,
                agg_org_id=normalized_agg_org_id,
                account_id=normalized_account_id,
                source="cache",
            )
            return {
                "success": True,
                "ready": True,
                "source": "cache",
                "message": "企业账号缓存有效，可直接开展办税业务",
                "state_file": saved_state["state_file"],
                "login_state": saved_state["state"],
                "raw": cache_result,
            }

        quick_login_result = self.client.check_app_login(
            agg_org_id=normalized_agg_org_id,
            account_id=normalized_account_id,
        )
        self._ensure_success(quick_login_result, "校验税局快速登录")
        if bool(quick_login_result.get("data")):
            saved_state = save_login_state(
                __file__,
                agg_org_id=normalized_agg_org_id,
                account_id=normalized_account_id,
                source="quick_login",
            )
            return {
                "success": True,
                "ready": True,
                "source": "quick_login",
                "message": "企业账号支持快速登录，可直接开展办税业务",
                "state_file": saved_state["state_file"],
                "login_state": saved_state["state"],
                "raw": quick_login_result,
            }

        return {
            "success": True,
            "ready": False,
            "source": "none",
            "message": (
                "当前企业账号尚未形成可直接复用的登录态，请确认办税小号已在税局侧完成绑定，"
                "或先人工完成首次激活。"
            ),
            "raw": {
                "check_cache": cache_result,
                "check_app_login": quick_login_result,
            },
        }
