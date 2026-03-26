"""
Tax Login Skill - 异常定义
"""


class TaxLoginError(Exception):
    """税务登录模块异常基类"""

    def __init__(self, code: str, message: str, data: dict = None):
        self.code = code
        self.message = message
        self.data = data or {}
        super().__init__(f"[{code}] {message}")

    def __str__(self):
        return f"[{self.code}] {self.message}"


class TokenExpiredError(TaxLoginError):
    """Token过期异常"""

    def __init__(self, message: str = "Token已过期，请重新获取"):
        super().__init__("4001", message)


class SignatureError(TaxLoginError):
    """签名错误异常"""

    def __init__(self, message: str = "签名验证失败"):
        super().__init__("4003", message)


class NetworkError(TaxLoginError):
    """网络请求异常"""

    def __init__(self, message: str = "网络请求失败"):
        super().__init__("NETWORK_ERROR", message)


class TaskTimeoutError(TaxLoginError):
    """任务超时异常"""

    def __init__(self, task_id: str, attempts: int):
        super().__init__(
            "TASK_TIMEOUT",
            f"任务 {task_id} 超时，已尝试 {attempts} 次",
            {"taskId": task_id, "attempts": attempts}
        )


class ConfigError(Exception):
    """配置错误异常"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

    def __str__(self):
        return self.message
