"""
登录 API Skill - 企享云税务登录能力封装

提供自然人APP登录、账号管理、企业信息查询等功能的封装。
"""

from .client import TaxLoginClient
from .config import Config, get_config
from .exceptions import (
    TaxLoginError,
    NetworkError,
    TaskTimeoutError,
    ConfigError
)
from .workflow import TaxLoginWorkflow

__version__ = "1.1.0"
__all__ = [
    "TaxLoginClient",
    "Config",
    "ConfigError",
    "TaxLoginError",
    "NetworkError",
    "TaskTimeoutError",
    "TaxLoginWorkflow",
    "get_config"
]
