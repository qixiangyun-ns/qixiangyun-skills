"""
Tax Login Skill - API客户端

提供自然人APP登录、账号管理、企业信息查询等功能的完整封装
"""

import json
import time
import requests
from datetime import datetime
from typing import Optional, Dict, Any, Callable, Union

from .auth import TokenManager
from .config import Config, get_config, ConfigError
from .crypto import md5, base64_encode, rsa_encrypt, build_signature
from .exceptions import (
    TaxLoginError,
    NetworkError,
    TaskTimeoutError
)


class TaxLoginClient:
    """
    税务登录认证客户端

    功能模块：
    - F01-01: 自然人APP登录（发送验证码）
    - F01-02: 上传短信验证码
    - F01-03: 产品订购（创建企业）
    - F01-04: 账号创建/更新
    - F01-05: 企业信息查询（异步）
    - F01-06: 校验税局缓存
    - F01-07: 校验APP快速登录
    - F01-08: 登录税局发送短信
    - F01-09: 登录税局验证短信
    """

    # 默认RSA公钥
    DEFAULT_RSA_PUBLIC_KEY = (
        "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC+vuYMGtTU+42wwbaFX+PkCuSeoREKe5V4EJMi553Gc03ficUdpLHIFdEjAMHAxepwm3RAGLwyxYFK/S93k8GYMuV35L2Nj/cVeHS8scsdqXzqLUKaI4wj438OI6HDh7rWsw1M5EgMsoZvQqja53+SgD3mgIy3XyILbmA5jUp2IwIDAQAB"
    )

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        api_host: str = "https://api.qixiangyun.com",
        rsa_public_key: str = None
    ):
        """
        初始化客户端

        Args:
            app_key: 应用密钥
            app_secret: 应用密钥
            api_host: API服务地址
            rsa_public_key: RSA公钥（用于密码加密）
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.api_host = api_host
        self.rsa_public_key = rsa_public_key or self.DEFAULT_RSA_PUBLIC_KEY

        # 初始化Token管理器
        self._token_manager = TokenManager(app_key, app_secret, api_host)

    @classmethod
    def from_config(cls, config: Optional[Config] = None) -> "TaxLoginClient":
        """
        从配置文件创建客户端（推荐方式）

        Args:
            config: 配置对象，为None时自动加载

        Returns:
            TaxLoginClient 实例

        Raises:
            ConfigError: 配置未设置

        使用示例：
            # 首次使用请先运行: python -m tax_login_skill
            client = TaxLoginClient.from_config()
        """
        if config is None:
            config = get_config()

        app_key, app_secret = config.validate()

        return cls(
            app_key=app_key,
            app_secret=app_secret,
            api_host=config.api_host,
            rsa_public_key=config.rsa_public_key
        )

    def check_connection(self) -> Dict[str, Any]:
        """
        测试API连接

        尝试获取Token来验证配置是否正确

        Returns:
            {
                "success": True/False,
                "message": "连接成功" 或 错误信息,
                "app_key": "已脱敏的app_key"
            }
        """
        try:
            # 尝试获取token来验证连接
            token = self._token_manager.get_token()
            return {
                "success": True,
                "message": "连接成功，API密钥有效",
                "app_key": f"{self.app_key[:4]}...{self.app_key[-4:]}"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "app_key": f"{self.app_key[:4]}...{self.app_key[-4:]}"
            }

    # ============================================================
    # 内部方法
    # ============================================================

    def _send_request(
        self,
        path: str,
        body: dict,
        need_token: bool = True
    ) -> Dict[str, Any]:
        """
        发送API请求

        Args:
            path: API路径
            body: 请求体（dict）
            need_token: 是否需要Token

        Returns:
            API响应数据
        """
        # 获取Token
        access_token = None
        if need_token:
            access_token = self._token_manager.get_token()

        # 构建请求
        full_url = f"{self.api_host}{path}"
        body_str = json.dumps(body, ensure_ascii=False)
        req_date = str(int(datetime.now().timestamp() * 1000))
        content_md5 = md5(body_str)

        # 构建签名
        if access_token:
            req_sign = build_signature(
                method="POST",
                content_md5=content_md5,
                req_date=req_date,
                access_token=access_token,
                app_secret=self.app_secret,
                app_key=self.app_key
            )
        else:
            # 无Token端点的签名格式（用于OAuth2等不需要Token的接口）
            req_sign = f"API-SV1:{self.app_key}:{base64_encode(md5(body_str))}"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "access_token": access_token or "",
            "req_date": req_date,
            "req_sign": req_sign
        }

        # 发送请求
        try:
            response = requests.post(
                full_url,
                headers=headers,
                data=body_str.encode('utf-8'),
                timeout=30
            )
            response.raise_for_status()
            result = response.json()

            # 检查业务错误
            code = result.get("code")
            if code == "4001":
                # Token过期，刷新后重试
                self._token_manager.clear_cache()
                access_token = self._token_manager.get_token()
                # 重新生成时间戳和签名（避免时间戳过期）
                req_date = str(int(datetime.now().timestamp() * 1000))
                req_sign = build_signature(
                    method="POST",
                    content_md5=content_md5,
                    req_date=req_date,
                    access_token=access_token,
                    app_secret=self.app_secret,
                    app_key=self.app_key
                )
                headers["access_token"] = access_token
                headers["req_date"] = req_date
                headers["req_sign"] = req_sign
                response = requests.post(
                    full_url,
                    headers=headers,
                    data=body_str.encode('utf-8'),
                    timeout=30
                )
                result = response.json()

            return result

        except requests.RequestException as e:
            raise NetworkError(f"网络请求失败: {str(e)}")

    def _encrypt_password(self, password: str) -> str:
        """
        加密密码

        Args:
            password: 明文密码

        Returns:
            加密后的密码
        """
        return rsa_encrypt(password, self.rsa_public_key)

    def _build_account_request_body(
        self,
        agg_org_id: Union[str, int],
        dq: str,
        username: str,
        phone: str,
        password: str,
        identity_type: str = "BSY",
        login_mode: int = 9,
        account_id: Optional[Union[str, int]] = None,
        proxy_nsrsbh: Optional[str] = None,
        login_type: Optional[int] = None,
        spec_type: Optional[int] = None,
        login_username: Optional[str] = None,
        login_password: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        构建账号创建/修改请求体。

        说明：
        - 兼容登录业务（新）的单账号/多账号模式
        - 默认 `login_mode=9`，适用于企业业务登录（办税小号）
        - 代理业务登录场景可传 `login_mode=15`
        """
        body: Dict[str, Any] = {
            "aggOrgId": agg_org_id,
            "dq": dq,
            "dlfs": login_mode,
            "gryhm": username,
            "gryhmm": self._encrypt_password(password),
            "sflx": identity_type,
            "sjhm": phone,
        }

        if account_id is not None:
            body["accountId"] = account_id
        if proxy_nsrsbh:
            body["proxyNsrsbh"] = proxy_nsrsbh
        if login_type is not None:
            body["loginType"] = login_type
        if spec_type is not None:
            body["specType"] = spec_type
        if login_username:
            body["dlzh"] = login_username
        if login_password:
            body["dlmm"] = login_password

        return body

    def create_account_record(
        self,
        agg_org_id: Union[str, int],
        dq: str,
        username: str,
        phone: str,
        password: str,
        identity_type: str = "BSY",
        login_mode: int = 9,
        proxy_nsrsbh: Optional[str] = None,
        login_type: Optional[int] = None,
        spec_type: Optional[int] = None,
        login_username: Optional[str] = None,
        login_password: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建平台登录账号记录。

        该方法对应“登录业务（新）/账号创建”。
        """
        path = "/v2/public/account/create"
        body = self._build_account_request_body(
            agg_org_id=agg_org_id,
            dq=dq,
            username=username,
            phone=phone,
            password=password,
            identity_type=identity_type,
            login_mode=login_mode,
            proxy_nsrsbh=proxy_nsrsbh,
            login_type=login_type,
            spec_type=spec_type,
            login_username=login_username,
            login_password=login_password,
        )
        return self._send_request(path, body)

    def update_account_record(
        self,
        agg_org_id: Union[str, int],
        account_id: Union[str, int],
        dq: str,
        username: str,
        phone: str,
        password: str,
        identity_type: str = "BSY",
        login_mode: int = 9,
        proxy_nsrsbh: Optional[str] = None,
        login_type: Optional[int] = None,
        spec_type: Optional[int] = None,
        login_username: Optional[str] = None,
        login_password: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        修改平台登录账号记录。

        该方法对应“登录业务（新）/账号修改”。
        """
        path = "/v2/public/account/update"
        body = self._build_account_request_body(
            agg_org_id=agg_org_id,
            dq=dq,
            username=username,
            phone=phone,
            password=password,
            identity_type=identity_type,
            login_mode=login_mode,
            account_id=account_id,
            proxy_nsrsbh=proxy_nsrsbh,
            login_type=login_type,
            spec_type=spec_type,
            login_username=login_username,
            login_password=login_password,
        )
        return self._send_request(path, body)

    # ============================================================
    # F01-01: 自然人APP登录（发送验证码）
    # ============================================================

    def app_login(
        self,
        area_code: str,
        username: str,
        phone: str,
        password: str
    ) -> Dict[str, Any]:
        """
        自然人APP登录 - 发送验证码

        对应文档：F01-01

        Args:
            area_code: 地区代码（4位，如 1100）
            username: 个人用户名
            phone: 手机号码
            password: 密码（明文，自动RSA加密）

        Returns:
            {
                "code": "SUCCESS",
                "data": {
                    "taskId": "xxx"  # 用于后续验证码上传
                }
            }
        """
        path = "/v2/public/zrr/login/getNatureTpass"
        body = {
            "areaCode": area_code,
            "gryhm": username,
            "gryhmm": self._encrypt_password(password),
            "sjhm": phone
        }
        return self._send_request(path, body)

    # ============================================================
    # F01-02: 上传短信验证码
    # ============================================================

    def verify_sms(self, task_id: str, sms_code: str) -> Dict[str, Any]:
        """
        上传短信验证码

        对应文档：F01-02

        Args:
            task_id: 登录任务ID（app_login返回）
            sms_code: 短信验证码

        Returns:
            验证结果
        """
        path = "/v2/public/zrr/login/tpasspushsms"
        body = {
            "taskId": task_id,
            "smsCode": sms_code
        }
        return self._send_request(path, body)

    # ============================================================
    # F01-03: 产品订购（创建企业）
    # ============================================================

    def order_product(
        self,
        nsrsbh: str,
        org_name: str,
        dq: str,
        product_codes: str = "0020"
    ) -> Dict[str, Any]:
        """
        产品订购（创建企业）

        对应文档：F01-03

        Args:
            nsrsbh: 纳税人识别号
            org_name: 企业名称
            dq: 地区代码（2位）
            product_codes: 产品代码（默认 0020）

        Returns:
            {
                "code": "SUCCESS",
                "data": {
                    "aggOrgId": "xxx"  # 企业ID
                }
            }
        """
        path = "/v2/public/org/productPurchase"
        body = {
            "nsrsbh": nsrsbh,
            "aggOrgName": org_name,
            "dq": dq,
            "productCodeList": product_codes
        }
        return self._send_request(path, body)

    # ============================================================
    # F01-04: 账号创建/更新
    # ============================================================

    def query_account(self, agg_org_id: str, account_id: str) -> Dict[str, Any]:
        """
        查询账号是否存在

        Args:
            agg_org_id: 企业ID
            account_id: 账号ID

        Returns:
            账号信息
        """
        path = "/v2/public/account/queryAccount"
        body = {
            "aggOrgId": agg_org_id,
            "accountId": account_id
        }
        return self._send_request(path, body)

    def _create_account(
        self,
        agg_org_id: str,
        dq: str,
        username: str,
        phone: str,
        password: str
    ) -> Dict[str, Any]:
        """创建账号（内部方法）"""
        return self.create_account_record(
            agg_org_id=agg_org_id,
            dq=dq,
            username=username,
            phone=phone,
            password=password,
            identity_type="BSY",
            login_mode=9,
        )

    def _update_account(
        self,
        agg_org_id: str,
        account_id: str,
        dq: str,
        username: str,
        phone: str,
        password: str
    ) -> Dict[str, Any]:
        """更新账号（内部方法）"""
        return self.update_account_record(
            agg_org_id=agg_org_id,
            account_id=account_id,
            dq=dq,
            username=username,
            phone=phone,
            password=password,
            identity_type="BSY",
            login_mode=9,
        )

    def create_or_update_account(
        self,
        agg_org_id: str,
        dq: str,
        username: str,
        phone: str,
        password: str,
        account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        账号创建或更新（智能判断）

        对应文档：F01-04

        业务逻辑：
        1. 如果传入account_id，先查询账号是否存在
        2. 存在则更新，不存在则创建
        3. 如果未传account_id，直接创建

        Args:
            agg_org_id: 企业ID
            dq: 地区代码
            username: 用户名
            phone: 手机号
            password: 密码
            account_id: 账号ID（可选，更新时必填）

        Returns:
            创建/更新结果
        """
        if account_id:
            # 查询账号是否存在
            query_result = self.query_account(agg_org_id, account_id)
            # 明确检查成功代码列表（API可能返回不同格式）
            if query_result.get("code") in ("0000", "SUCCESS") and query_result.get("data"):
                # 账号存在，执行更新
                return self._update_account(
                    agg_org_id, account_id, dq, username, phone, password
                )

        # 账号不存在或无account_id，执行创建
        return self._create_account(agg_org_id, dq, username, phone, password)

    # ============================================================
    # F01-05: 企业信息查询（异步）
    # ============================================================

    def query_org_info(self, agg_org_id: str) -> Dict[str, Any]:
        """
        发起企业信息查询任务

        对应文档：F01-05

        Args:
            agg_org_id: 企业ID

        Returns:
            {
                "code": "SUCCESS",
                "data": {
                    "taskId": "xxx"
                }
            }
        """
        path = "/v2/public/org/loadOrgTaxInfo"
        body = {"aggOrgId": agg_org_id}
        return self._send_request(path, body)

    def query_org_info_result(
        self,
        agg_org_id: str,
        task_id: str
    ) -> Dict[str, Any]:
        """
        查询企业信息结果

        Args:
            agg_org_id: 企业ID
            task_id: 任务ID

        Returns:
            企业税务信息
        """
        path = "/v2/public/org/queryOrgInfo"
        body = {
            "aggOrgId": agg_org_id,
            "taskId": task_id
        }
        return self._send_request(path, body)

    def poll_org_info_result(
        self,
        agg_org_id: str,
        task_id: str,
        max_attempts: int = 30,
        interval: int = 10,
        on_progress: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        轮询企业信息查询结果

        Args:
            agg_org_id: 企业ID
            task_id: 任务ID
            max_attempts: 最大尝试次数
            interval: 轮询间隔（秒）
            on_progress: 进度回调函数

        Returns:
            查询完成的完整数据

        Raises:
            TaskTimeoutError: 超时未完成
        """
        for attempt in range(max_attempts):
            result = self.query_org_info_result(agg_org_id, task_id)

            # 检查业务状态
            business_status = result.get("data", {}).get("businessStatus")

            if business_status == 3:  # 完成
                return result
            elif business_status == 2:  # 失败
                return result

            # 处理中，等待重试
            if on_progress:
                on_progress(attempt + 1, max_attempts, result)

            time.sleep(interval)

        raise TaskTimeoutError(task_id, max_attempts)

    # ============================================================
    # F01-06 ~ F01-09: 登录状态校验与税局登录
    # ============================================================

    def check_cache(
        self,
        agg_org_id: str,
        account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        校验税局缓存是否有效

        对应文档：F01-06

        Args:
            agg_org_id: 企业ID
            account_id: 平台账号ID

        Returns:
            缓存状态
        """
        path = "/v2/public/login/remote/checkCache"
        body: Dict[str, Any] = {"aggOrgId": agg_org_id}
        if account_id:
            body["accountId"] = account_id
        return self._send_request(path, body)

    def check_app_login(
        self,
        agg_org_id: str,
        account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        校验税务APP是否可快速登录

        对应文档：F01-07

        Args:
            agg_org_id: 企业ID
            account_id: 平台账号ID

        Returns:
            是否可快速登录
        """
        path = "/v2/public/login/remote/checkRomoteAppCache"
        body: Dict[str, Any] = {"aggOrgId": agg_org_id}
        if account_id:
            body["accountId"] = account_id
        return self._send_request(path, body)

    def send_etax_login_sms(
        self,
        agg_org_id: Union[str, int],
        account_id: Union[str, int]
    ) -> Dict[str, Any]:
        """
        发送税局登录验证码。

        该方法对应“登录业务（新）/发送短信验证码”。
        """
        path = "/v2/public/login/remote/etaxcookie"
        body = {
            "aggOrgId": agg_org_id,
            "accountId": account_id,
        }
        return self._send_request(path, body)

    def upload_etax_login_sms(
        self,
        task_id: Union[str, int],
        sms_code: str
    ) -> Dict[str, Any]:
        """
        上传税局登录验证码。

        该方法对应“登录业务（新）/上传短信验证码”。
        """
        path = "/v2/public/login/remote/pushsms"
        body = {
            "taskId": task_id,
            "smsCode": sms_code,
        }
        return self._send_request(path, body)

    def login_tax_send_sms(self, agg_org_id: str) -> Dict[str, Any]:
        """
        登录税局 - 发送短信验证码

        对应文档：F01-08

        Args:
            agg_org_id: 企业ID

        Returns:
            发送结果
        """
        path = "/v2/public/login/remote/sendSms"
        body = {"aggOrgId": agg_org_id}
        return self._send_request(path, body)

    def login_tax_verify_sms(
        self,
        agg_org_id: str,
        sms_code: str
    ) -> Dict[str, Any]:
        """
        登录税局 - 验证短信验证码

        对应文档：F01-09

        Args:
            agg_org_id: 企业ID
            sms_code: 短信验证码

        Returns:
            登录结果
        """
        path = "/v2/public/login/remote/verifySms"
        body = {
            "aggOrgId": agg_org_id,
            "smsCode": sms_code
        }
        return self._send_request(path, body)

    # ============================================================
    # F01-10: 自然人企业列表查询
    # ============================================================

    def query_nature_org_list(
        self,
        account_id: str,
        agg_org_id: str
    ) -> Dict[str, Any]:
        """
        查询自然人托管的企业列表

        Args:
            account_id: 自然人账号ID
            agg_org_id: 企业ID

        Returns:
            企业列表
        """
        path = "/v2/public/login/queryOrglist"
        body = {
            "accountId": account_id,
            "aggOrgId": agg_org_id
        }
        return self._send_request(path, body)

    # ============================================================
    # 完整登录流程
    # ============================================================

    def login_flow_step1_send_sms(
        self,
        area_code: str,
        phone: str,
        password: str
    ) -> Dict[str, Any]:
        """
        登录流程步骤1：发送短信验证码

        Args:
            area_code: 地区代码（4位，如 1100）
            phone: 手机号码
            password: 密码

        Returns:
            - 需要验证码: {"success": True, "need_verify": True, "task_id": "xxx"}
            - 已登录: {"success": True, "need_verify": False, "tpass": {...}}
            - 失败: {"success": False, "message": "错误信息"}
        """
        result = self.app_login(
            area_code=area_code,
            username=phone,
            phone=phone,
            password=password
        )

        if result.get("code") in ("SUCCESS", "2000"):
            data = result.get("data", {})

            # 检查是否返回了 tpass（已登录状态）
            if data.get("tpass"):
                return {
                    "success": True,
                    "need_verify": False,
                    "tpass": data.get("tpass"),
                    "message": "登录成功"
                }

            # 检查是否需要验证码
            task_id = data.get("taskId") or data.get("task_id")
            if task_id:
                return {
                    "success": True,
                    "need_verify": True,
                    "task_id": str(task_id),
                    "message": data.get("msg") or "验证码已发送",
                    "mobile": data.get("mobile")  # 脱敏后的手机号
                }

            # 其他情况
            return {
                "success": True,
                "need_verify": False,
                "message": data.get("msg") or "操作成功",
                "data": data
            }
        else:
            return {
                "success": False,
                "message": result.get("message", "发送验证码失败"),
                "code": result.get("code")
            }

    def login_flow_step2_verify_sms(
        self,
        task_id: str,
        sms_code: str
    ) -> Dict[str, Any]:
        """
        登录流程步骤2：验证短信验证码

        Args:
            task_id: 任务ID
            sms_code: 短信验证码

        Returns:
            {"success": True, "tpass": {...}} 或 {"success": False, "message": "错误信息"}
        """
        result = self.verify_sms(task_id, sms_code)

        if result.get("code") in ("SUCCESS", "2000"):
            return {
                "success": True,
                "tpass": result.get("data", {}).get("tpass"),
                "message": "登录成功"
            }
        else:
            return {
                "success": False,
                "message": result.get("message", "验证码错误"),
                "code": result.get("code")
            }

    def login_flow_step3_order_enterprise(
        self,
        nsrsbh: str,
        org_name: str,
        dq: str,
        phone: str,
        password: str,
        product_codes: str = "0020"
    ) -> Dict[str, Any]:
        """
        登录流程步骤3：企业订购（产品订购 + 账号创建）

        Args:
            nsrsbh: 纳税人识别号
            org_name: 企业名称
            dq: 地区代码（2位）
            phone: 手机号
            password: 密码
            product_codes: 产品代码

        Returns:
            {"success": True, "agg_org_id": "xxx", "account_id": "xxx"} 或 {"success": False, "message": "错误信息"}
        """
        # 3.1 产品订购
        result = self.order_product(
            nsrsbh=nsrsbh,
            org_name=org_name,
            dq=dq,
            product_codes=product_codes
        )

        if result.get("code") not in ("SUCCESS", "2000"):
            return {
                "success": False,
                "message": result.get("message", "产品订购失败"),
                "code": result.get("code")
            }

        agg_org_id = result.get("data", {}).get("aggOrgId")

        # 3.2 创建账号
        result = self.create_or_update_account(
            agg_org_id=agg_org_id,
            dq=dq,
            username=phone,
            phone=phone,
            password=password
        )

        if result.get("code") not in ("SUCCESS", "2000"):
            return {
                "success": False,
                "message": result.get("message", "创建账号失败"),
                "code": result.get("code")
            }

        account_id = result.get("data", {}).get("accountId")

        return {
            "success": True,
            "agg_org_id": agg_org_id,
            "account_id": account_id,
            "message": "企业订购成功"
        }

    def login_flow_full(
        self,
        area_code: str,
        phone: str,
        password: str,
        sms_code: str,
        target_enterprises: list = None
    ) -> Dict[str, Any]:
        """
        完整登录流程（推荐使用）

        流程：
        1. 发送验证码
        2. 验证短信验证码
        3. 查询自然人托管的企业列表
        4. 对每个企业进行产品订购和账号创建

        Args:
            area_code: 地区代码（4位，如 1100）
            phone: 手机号码
            password: 密码
            sms_code: 短信验证码
            target_enterprises: 目标企业列表，格式 [{"nsrsbh": "xxx", "org_name": "xxx", "dq": "11"}, ...]
                               如果为 None，则只登录不创建企业

        Returns:
            {
                "success": True/False,
                "message": "xxx",
                "tpass": {...},  # 登录凭证
                "enterprises": [  # 企业列表
                    {"agg_org_id": "xxx", "account_id": "xxx", "nsrsbh": "xxx", "org_name": "xxx"}
                ]
            }
        """
        # 步骤1：发送验证码（如果还没发送）
        # 这里假设验证码已经发送，直接验证

        # 步骤2：验证短信验证码
        verify_result = self.login_flow_step2_verify_sms(
            task_id=None,  # 需要从外部传入
            sms_code=sms_code
        )

        # 由于 task_id 需要从步骤1获取，这里提供一个简化版本
        # 实际使用时应该分步调用

        return {
            "success": False,
            "message": "请使用分步方法: step1_send_sms -> step2_verify_sms -> step3_query_and_create"
        }

    def login_flow_step3_query_orgs(
        self,
        account_id: str,
        agg_org_id: str
    ) -> Dict[str, Any]:
        """
        登录流程步骤3：查询自然人托管的企业列表

        Args:
            account_id: 自然人账号ID
            agg_org_id: 任意一个企业ID（用于查询）

        Returns:
            {"success": True, "org_list": [...]} 或 {"success": False, "message": "错误信息"}
        """
        result = self.query_nature_org_list(account_id, agg_org_id)

        if result.get("code") in ("SUCCESS", "2000"):
            return {
                "success": True,
                "org_list": result.get("data", []),
                "message": "查询成功"
            }
        else:
            return {
                "success": False,
                "message": result.get("message", "查询企业列表失败"),
                "code": result.get("code")
            }

    def login_flow_step4_order_enterprise(
        self,
        nsrsbh: str,
        org_name: str,
        dq: str,
        phone: str,
        password: str,
        product_codes: str = "0020"
    ) -> Dict[str, Any]:
        """
        登录流程步骤4：企业订购（产品订购 + 账号创建）

        Args:
            nsrsbh: 纳税人识别号
            org_name: 企业名称
            dq: 地区代码（2位）
            phone: 手机号
            password: 密码
            product_codes: 产品代码

        Returns:
            {"success": True, "agg_org_id": "xxx", "account_id": "xxx"} 或 {"success": False, "message": "错误信息"}
        """
        return self.login_flow_step3_order_enterprise(
            nsrsbh=nsrsbh,
            org_name=org_name,
            dq=dq,
            phone=phone,
            password=password,
            product_codes=product_codes
        )
