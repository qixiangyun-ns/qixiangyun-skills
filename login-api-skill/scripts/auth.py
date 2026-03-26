"""
Tax Login Skill - 认证模块

提供OAuth2 Token获取和缓存功能
"""

import time
import requests
from .crypto import md5
from .exceptions import TaxLoginError, TokenExpiredError


class TokenManager:
    """
    Token管理器

    功能：
    - OAuth2 认证获取 access_token
    - Token缓存与自动刷新
    """

    # Token刷新缓冲时间（秒）- 提前5分钟刷新，避免请求途中过期
    TOKEN_REFRESH_BUFFER = 300

    def __init__(self, app_key: str, app_secret: str, api_host: str):
        """
        初始化Token管理器

        Args:
            app_key: 应用密钥
            app_secret: 应用密钥
            api_host: API服务地址
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.api_host = api_host

        # Token缓存
        self._token_cache = {
            "token": None,
            "expires_at": 0  # 过期时间戳（秒）
        }

    def get_token(self, force_refresh: bool = False) -> str:
        """
        获取有效的access_token

        Args:
            force_refresh: 是否强制刷新

        Returns:
            有效的access_token
        """
        current_time = time.time()

        # 检查缓存是否有效（提前刷新，避免请求途中过期）
        if not force_refresh:
            if (self._token_cache["token"] and
                self._token_cache["expires_at"] > current_time + self.TOKEN_REFRESH_BUFFER):
                return self._token_cache["token"]

        # 获取新Token
        token, expires_in = self._fetch_new_token()

        # 更新缓存
        self._token_cache["token"] = token
        self._token_cache["expires_at"] = current_time + expires_in

        return token

    def _fetch_new_token(self) -> tuple:
        """
        从OAuth2服务获取新Token

        Returns:
            (token, expires_in) 元组

        Raises:
            TaxLoginError: 获取失败时抛出
        """
        url = f"{self.api_host}/v2/public/oauth2/login"
        body = {
            "grant_type": "client_credentials",
            "client_appkey": self.app_key,
            "client_secret": md5(self.app_secret)
        }

        try:
            response = requests.post(url, json=body, timeout=30)
            response.raise_for_status()
            result = response.json()

            if result.get("code") != "SUCCESS" and result.get("code") != "2000":
                raise TaxLoginError(
                    result.get("code", "UNKNOWN"),
                    result.get("message", "获取Token失败"),
                    result.get("data")
                )

            data = result.get("data", {})
            token = data.get("access_token")
            expires_in = data.get("expires_in", 7200)  # 默认2小时

            if not token:
                raise TaxLoginError("TOKEN_ERROR", "Token响应格式错误")

            return token, expires_in

        except requests.RequestException as e:
            raise TaxLoginError("NETWORK_ERROR", f"网络请求失败: {str(e)}")

    def clear_cache(self):
        """清除Token缓存"""
        self._token_cache = {
            "token": None,
            "expires_at": 0
        }
