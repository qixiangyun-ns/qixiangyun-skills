"""企业风控和经营异常 Skill - 异常定义"""


class EnterpriseRiskError(Exception):
    """企业风控模块异常基类"""
    def __init__(self, code: str, message: str, data: dict = None):
        self.code = code
        self.message = message
        self.data = data or {}
        super().__init__(f"[{code}] {message}")

    def __str__(self):
        return f"[{self.code}] {self.message}"


class NetworkError(EnterpriseRiskError):
    """网络请求异常"""
    def __init__(self, message: str = "网络请求失败"):
        super().__init__("NETWORK_ERROR", message)
