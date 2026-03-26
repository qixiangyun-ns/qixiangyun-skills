"""
Tax Login Skill - 配置管理

管理API密钥的加载、保存和验证
"""

import os
from pathlib import Path
from typing import Optional, Tuple

from .exceptions import ConfigError


class Config:
    """
    配置管理器

    配置优先级：
    1. 直接传入的参数
    2. 环境变量
    3. .env 文件
    """

    # 默认API地址
    DEFAULT_API_HOST = "https://api.qixiangyun.com"

    # 默认RSA公钥
    DEFAULT_RSA_PUBLIC_KEY = (
        "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC+vuYMGtTU+42wwbaFX+PkCuSeoREKe5V4EJMi553Gc03ficUdpLHIFdEjAMHAxepwm3RAGLwyxYFK/S93k8GYMuV35L2Nj/cVeHS8scsdqXzqLUKaI4wj438OI6HDh7rWsw1M5EgMsoZvQja53+SgD3mgIy3XyILbmA5jUp2IwIDAQAB"
    )

    def __init__(self):
        self.app_key: Optional[str] = None
        self.app_secret: Optional[str] = None
        self.api_host: str = self.DEFAULT_API_HOST
        self.rsa_public_key: Optional[str] = None
        self._env_paths = [
            Path(__file__).resolve().parents[1] / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]

    def load(self) -> "Config":
        """
        加载配置

        Returns:
            self，支持链式调用

        Raises:
            ConfigError: 配置加载失败
        """
        # 1. 尝试从 .env 文件加载
        self._load_from_env_file()

        # 2. 环境变量覆盖
        self._load_from_env()

        return self

    def _load_from_env_file(self):
        """从 .env 文件加载配置"""
        for env_path in self._env_paths:
            if not env_path.exists():
                continue

            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()

                        if key == "QXY_API_KEY":
                            self._parse_api_key(value)
                        elif key == "QXY_API_HOST":
                            self.api_host = value or self.DEFAULT_API_HOST
                        elif key == "QXY_RSA_PUBLIC_KEY":
                            self.rsa_public_key = value

    def _load_from_env(self):
        """从环境变量加载配置"""
        env_key = os.environ.get("QXY_API_KEY")
        if env_key:
            self._parse_api_key(env_key)

        env_host = os.environ.get("QXY_API_HOST")
        if env_host:
            self.api_host = env_host

        env_rsa = os.environ.get("QXY_RSA_PUBLIC_KEY")
        if env_rsa:
            self.rsa_public_key = env_rsa

    def _parse_api_key(self, api_key: str):
        """
        解析API密钥

        格式：{client_appkey}.{client_secret}

        Args:
            api_key: 完整的API密钥
        """
        if "." not in api_key:
            raise ConfigError(
                f"API密钥格式错误，应为 {{client_appkey}}.{{client_secret}} 格式\n"
                f"请访问 https://open.qixiangyun.com 申请密钥"
            )

        parts = api_key.split(".", 1)
        self.app_key = parts[0]
        self.app_secret = parts[1]

    def save(self, api_key: str, api_host: Optional[str] = None):
        """
        保存配置到 .env 文件

        Args:
            api_key: 完整的API密钥
            api_host: API地址（可选）
        """
        content = f"""# Tax Login Skill 配置文件
# 由配置向导自动生成
#
# API密钥申请：https://open.qixiangyun.com
# 密钥格式：{{client_appkey}}.{{client_secret}}

# 企享云 API 密钥
QXY_API_KEY={api_key}

# API 服务地址
QXY_API_HOST={api_host or self.DEFAULT_API_HOST}
"""

        env_path = self._env_paths[0]
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)

        # 重新加载
        self._parse_api_key(api_key)
        if api_host:
            self.api_host = api_host

    def validate(self) -> Tuple[str, str]:
        """
        验证配置完整性

        Returns:
            (app_key, app_secret)

        Raises:
            ConfigError: 配置不完整
        """
        if not self.app_key or not self.app_secret:
            raise ConfigError(
                "API密钥未配置\n"
                "请设置环境变量 QXY_API_KEY\n"
                "或在当前 skill 根目录创建 .env 文件\n"
                "API密钥申请：https://open.qixiangyun.com"
            )

        return self.app_key, self.app_secret

    @property
    def is_configured(self) -> bool:
        """检查是否已配置"""
        return bool(self.app_key and self.app_secret)


def get_config() -> Config:
    """
    获取配置实例（便捷函数）

    Returns:
        已加载的配置对象
    """
    return Config().load()
