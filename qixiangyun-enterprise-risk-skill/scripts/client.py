"""企业风控和经营异常 Skill - API客户端

通过MCP HTTP Streamable协议调用企享云企业风控查询服务
"""

import json
import requests
from typing import Dict, Any, Optional

from config import Config, get_config, ConfigError
from exceptions import EnterpriseRiskError, NetworkError


class EnterpriseRiskClient:
    """
    企业风控和经营异常查询客户端

    通过MCP HTTP Streamable协议调用企享云企业风控查询服务。
    支持通过企业名称或统一社会信用代码查询企业征信与风险信息。
    """

    MCP_PATH = "/mcp/enterprise_blacklist_status_enhanced-http"

    def __init__(
        self,
        client_appkey: str,
        client_secret: str,
        mcp_base_url: str = "https://mcp.qixiangyun.com",
    ):
        self.client_appkey = client_appkey
        self.client_secret = client_secret
        self.mcp_base_url = mcp_base_url.rstrip("/")

    @classmethod
    def from_config(cls, config: Optional[Config] = None) -> "EnterpriseRiskClient":
        """从配置创建客户端"""
        if config is None:
            config = get_config()
        appkey, secret = config.validate()
        return cls(
            client_appkey=appkey,
            client_secret=secret,
            mcp_base_url=config.mcp_base_url,
        )

    def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用MCP工具"""
        url = f"{self.mcp_base_url}{self.MCP_PATH}"

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": {
                    "client_appkey": self.client_appkey,
                    "client_secret": self.client_secret,
                    **arguments,
                },
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                return self._parse_sse_response(response.text)
            else:
                result = response.json()
                return self._extract_mcp_result(result)

        except requests.RequestException as e:
            raise NetworkError(f"网络请求失败: {str(e)}")

    def _parse_sse_response(self, text: str) -> Dict[str, Any]:
        """解析SSE响应"""
        for line in text.split("\n"):
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str:
                    try:
                        data = json.loads(data_str)
                        return self._extract_mcp_result(data)
                    except json.JSONDecodeError:
                        continue
        raise EnterpriseRiskError("PARSE_ERROR", "无法解析SSE响应")

    def _extract_mcp_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """提取MCP工具调用结果"""
        if "error" in result:
            error = result["error"]
            raise EnterpriseRiskError(
                code=str(error.get("code", "UNKNOWN")),
                message=error.get("message", "MCP调用失败"),
            )

        result_data = result.get("result", {})
        content = result_data.get("content", [])

        for item in content:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except (json.JSONDecodeError, KeyError):
                    return {"raw_text": item.get("text", "")}

        return result_data

    def query_enterprise_risk(
        self,
        enterprise_name: str = "",
        credit_code: str = "",
    ) -> Dict[str, Any]:
        """
        查询企业风控和经营异常信息

        Args:
            enterprise_name: 企业全名
            credit_code: 统一社会信用代码（18位）

        Returns:
            企业风控信息，包含经营异常、严重违法、重大税收违法风险、非正常户等
        """
        if not enterprise_name and not credit_code:
            raise EnterpriseRiskError("PARAM_ERROR", "请提供企业名称或统一社会信用代码")

        arguments = {}
        if enterprise_name:
            arguments["enterpriseName"] = enterprise_name
        if credit_code:
            arguments["creditCode"] = credit_code

        return self._call_mcp_tool("query_enterprise_blacklist_status_enhanced_auto", arguments)
