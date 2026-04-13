"""票据查验 Skill - API客户端

通过MCP HTTP Streamable协议调用企享云发票查验服务
"""

import json
import requests
from typing import Dict, Any, Optional, List

from config import Config, get_config, ConfigError
from exceptions import BillVerificationError, NetworkError


class BillVerificationClient:
    """
    票据查验客户端

    通过MCP HTTP Streamable协议调用企享云发票查验服务。
    支持税控发票查验、数电票查验、数电纸质发票查验、批量查验等。
    """

    MCP_PATH = "/mcp/invoice_verification-http"

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
    def from_config(cls, config: Optional[Config] = None) -> "BillVerificationClient":
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
        """
        调用MCP工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具调用结果
        """
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
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=120,
            )
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
        raise BillVerificationError("PARSE_ERROR", "无法解析SSE响应")

    def _extract_mcp_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """提取MCP工具调用结果"""
        if "error" in result:
            error = result["error"]
            raise BillVerificationError(
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

    def verify_tax_control_invoice(self, cy_list: List[Dict]) -> Dict[str, Any]:
        """
        税控发票查验

        支持增值税专用/普通发票、机动车销售统一发票等类型。
        cyList 支持多张。

        Args:
            cy_list: 发票查验列表，每项包含 fpdm/fphm/kprq/je/fj 等字段

        Returns:
            查验结果
        """
        if not cy_list:
            raise BillVerificationError("PARAM_ERROR", "cy_list 不能为空")
        return self._call_mcp_tool("verify_tax_control_invoice", {"cyList": cy_list})

    def verify_digital_invoice(self, cy_list: List[Dict]) -> Dict[str, Any]:
        """
        数电票查验（全电发票）

        cyList 支持多张。

        Args:
            cy_list: 发票查验列表

        Returns:
            查验结果
        """
        if not cy_list:
            raise BillVerificationError("PARAM_ERROR", "cy_list 不能为空")
        return self._call_mcp_tool("verify_digital_invoice", {"cyList": cy_list})

    def verify_digital_paper_invoice(self, cy_list: List[Dict]) -> Dict[str, Any]:
        """
        数电纸质发票查验（全电纸质）

        cyList 支持多张。

        Args:
            cy_list: 发票查验列表

        Returns:
            查验结果
        """
        if not cy_list:
            raise BillVerificationError("PARAM_ERROR", "cy_list 不能为空")
        return self._call_mcp_tool("verify_digital_paper_invoice", {"cyList": cy_list})

    def verify_invoice(
        self,
        invoice_type_code: str,
        invoice_number: str,
        billing_date: str,
        amount: str,
        check_code: str = "",
    ) -> Dict[str, Any]:
        """
        发票查验（单张）

        支持增值税专用发票、普通发票等类型。

        Args:
            invoice_type_code: 发票代码
            invoice_number: 发票号码
            billing_date: 开票日期
            amount: 金额
            check_code: 校验码

        Returns:
            查验结果
        """
        return self._call_mcp_tool("verify_invoice", {
            "invoiceTypeCode": invoice_type_code,
            "invoiceNumber": invoice_number,
            "billingDate": billing_date,
            "amount": amount,
            "checkCode": check_code,
        })

    def batch_verify_invoices(self, cy_list: List[Dict]) -> Dict[str, Any]:
        """
        批量查验税控发票

        每次最多50张发票。

        Args:
            cy_list: 发票查验列表

        Returns:
            批量查验结果
        """
        if not cy_list:
            raise BillVerificationError("PARAM_ERROR", "cy_list 不能为空")
        if len(cy_list) > 50:
            raise BillVerificationError("PARAM_ERROR", "批量查验每次最多50张发票")
        return self._call_mcp_tool("batch_verify_invoices", {"cyList": cy_list})

    def validate_invoice_info(
        self,
        invoice_type_code: str = "",
        invoice_number: str = "",
        billing_date: str = "",
        amount: str = "",
        check_code: str = "",
    ) -> Dict[str, Any]:
        """
        验证发票信息格式

        在正式查验前进行预检查。

        Args:
            invoice_type_code: 发票代码
            invoice_number: 发票号码
            billing_date: 开票日期
            amount: 金额
            check_code: 校验码

        Returns:
            验证结果
        """
        return self._call_mcp_tool("validate_invoice_info", {
            "invoiceTypeCode": invoice_type_code,
            "invoiceNumber": invoice_number,
            "billingDate": billing_date,
            "amount": amount,
            "checkCode": check_code,
        })
