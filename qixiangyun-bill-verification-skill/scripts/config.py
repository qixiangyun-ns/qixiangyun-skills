"""票据查验 Skill - 配置管理"""

import os
from pathlib import Path
from typing import Optional, Tuple


class ConfigError(Exception):
    """配置错误异常"""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class Config:
    """
    配置管理器

    配置优先级：
    1. 环境变量
    2. .env 文件
    """

    MCP_BASE_URL = "https://mcp.qixiangyun.com"

    def __init__(self):
        self.client_appkey: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.mcp_base_url: str = self.MCP_BASE_URL
        self._env_paths = [
            Path(__file__).resolve().parents[1] / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]

    def load(self) -> "Config":
        """加载配置"""
        self._load_from_env_file()
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
                        if key == "QXY_CLIENT_APPKEY":
                            self.client_appkey = value or None
                        elif key == "QXY_CLIENT_SECRET":
                            self.client_secret = value or None
                        elif key == "QXY_MCP_BASE_URL":
                            self.mcp_base_url = value or self.MCP_BASE_URL

    def _load_from_env(self):
        """从环境变量加载配置"""
        env_appkey = os.environ.get("QXY_CLIENT_APPKEY")
        if env_appkey:
            self.client_appkey = env_appkey.strip() or None
        env_secret = os.environ.get("QXY_CLIENT_SECRET")
        if env_secret:
            self.client_secret = env_secret.strip() or None
        env_base_url = os.environ.get("QXY_MCP_BASE_URL")
        if env_base_url:
            self.mcp_base_url = env_base_url

    def validate(self) -> Tuple[str, str]:
        """验证配置完整性"""
        if not self.client_appkey or not self.client_secret:
            raise ConfigError(
                "API密钥未配置\n"
                "请设置环境变量 QXY_CLIENT_APPKEY 和 QXY_CLIENT_SECRET\n"
                "或在当前 skill 根目录创建 .env 文件\n"
                "API密钥申请：https://open.qixiangyun.com"
            )
        return self.client_appkey, self.client_secret

    @property
    def is_configured(self) -> bool:
        """检查是否已配置"""
        return bool(self.client_appkey and self.client_secret)


def get_config() -> Config:
    """获取配置实例"""
    return Config().load()
