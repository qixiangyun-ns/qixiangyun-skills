#!/usr/bin/env python3
"""payment_workflow 单元测试。"""

from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import payment_workflow  # noqa: E402
import mcp_client as payment_mcp_client  # noqa: E402
import qxy_mcp_lib  # noqa: E402
from login_state_support import LoginStateError  # noqa: E402
from qxy_mcp_lib import QXYMCPError, QXYWorkflowError  # noqa: E402


def build_valid_config() -> dict[str, Any]:
    """构建可复用的有效配置。"""

    config = payment_workflow.build_sample_config()
    config["aggOrgId"] = "4788840764917695"
    config["year"] = 2026
    config["period"] = 4
    config["steps"]["payment"]["detail"] = [
        {
            "yzpzzlDm": "BDA0610606",
            "fromDate": "2026-03-01",
            "toDate": "2026-03-31",
            "taxAmount": 128.5,
            "jkfs": "1",
            "yhzh": "6222020202020202",
            "agreementAccount": None,
            "zspmDm": None,
            "zsxmDm": None,
            "bsswjg": None,
            "kqyswjgmc": None,
            "sebyz": "N",
        }
    ]
    config["steps"]["certificate"]["zsxmDtos"] = [
        {
            "ssqQ": "2026-03-01",
            "ssqZ": "2026-03-31",
            "yzpzzlDm": "BDA0610606",
            "zspmDm": None,
        }
    ]
    return config


class PaymentWorkflowValidationTest(unittest.TestCase):
    """覆盖参数校验与基础流程行为。"""

    @staticmethod
    def _write_login_state(state_path: Path) -> None:
        payload = {
            "version": 1,
            "ready": True,
            "aggOrgId": "4788840764917695",
            "accountId": "ACC-PAY-001",
            "source": "cache",
        }
        state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_build_payment_args_keeps_common_fields(self) -> None:
        """缴款参数构建应保留顶层公共字段和明细内容。"""

        config = build_valid_config()
        result = payment_workflow._build_payment_args(config["steps"]["payment"], config)

        self.assertEqual(result["aggOrgId"], "4788840764917695")
        self.assertEqual(result["year"], 2026)
        self.assertEqual(result["period"], 4)
        self.assertEqual(result["detail"][0]["yzpzzlDm"], "BDA0610606")
        self.assertEqual(result["detail"][0]["fromDate"], "2026-03-01")
        self.assertEqual(result["detail"][0]["toDate"], "2026-03-31")
        self.assertEqual(result["detail"][0]["taxAmount"], 128.5)

    def test_build_sample_config_accepts_year_and_period(self) -> None:
        """脚手架应允许显式指定申报月份，并回推出上月所属期。"""

        config = payment_workflow.build_sample_config(2025, 12)

        self.assertEqual(config["year"], 2025)
        self.assertEqual(config["period"], 12)
        self.assertEqual(config["max_poll_attempts"], 30)
        self.assertEqual(
            config["steps"]["payment"]["detail"][0]["fromDate"],
            "2025-11-01",
        )

    def test_build_sample_config_handles_january_filing_month(self) -> None:
        """1 月申报应自动回推到上一年 12 月所属期。"""

        config = payment_workflow.build_sample_config(2026, 1)

        self.assertEqual(
            config["steps"]["payment"]["detail"][0]["fromDate"],
            "2025-12-01",
        )
        self.assertEqual(
            config["steps"]["certificate"]["zsxmDtos"][0]["ssqZ"],
            "2025-12-31",
        )

    def test_build_payment_args_rejects_invalid_date_range(self) -> None:
        """缴款明细日期倒挂时应直接报错。"""

        config = build_valid_config()
        config["steps"]["payment"]["detail"][0]["fromDate"] = "2026-04-01"

        with self.assertRaisesRegex(QXYWorkflowError, "fromDate 不能晚于 toDate"):
            payment_workflow._build_payment_args(config["steps"]["payment"], config)

    def test_build_certificate_args_rejects_cross_year_range(self) -> None:
        """完税证明请求跨自然年时应在本地提前拦截。"""

        config = build_valid_config()
        config["steps"]["certificate"]["zsxmDtos"] = [
            {
                "ssqQ": "2025-12-01",
                "ssqZ": "2025-12-31",
                "yzpzzlDm": "BDA0610606",
            },
            {
                "ssqQ": "2026-01-01",
                "ssqZ": "2026-01-31",
                "yzpzzlDm": "BDA0610606",
            },
        ]

        with self.assertRaisesRegex(QXYWorkflowError, "不可跨自然年"):
            payment_workflow._build_certificate_args(
                config["steps"]["certificate"],
                config,
            )

    @mock.patch.object(payment_workflow, "poll_tool")
    @mock.patch.object(payment_workflow, "call_tool")
    def test_run_workflow_payment_returns_start_and_query(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """缴款步骤启用轮询时应返回统一的 start/taskId/query 结构。"""

        config = build_valid_config()
        config["steps"]["certificate"]["enabled"] = False
        mock_call_tool.return_value = {"taskId": "TASK-001", "businessStatus": 1}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {"businessStatus": 3, "resultMessage": "成功"},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "login-state.json"
            self._write_login_state(state_path)

            with mock.patch.dict(
                os.environ,
                {"QXY_LOGIN_STATE_PATH": str(state_path)},
                clear=False,
            ):
                result = payment_workflow.run_workflow(config, only_steps={"payment"})

        self.assertEqual(result["steps"]["payment"]["taskId"], "TASK-001")
        self.assertEqual(result["steps"]["payment"]["query"]["state"], "success")
        self.assertEqual(result["accountId"], "ACC-PAY-001")
        mock_call_tool.assert_called_once_with(
            "tax_payment",
            "load_payment_task",
            mock.ANY,
        )
        mock_poll_tool.assert_called_once()
        self.assertEqual(mock_call_tool.call_args.args[2]["accountId"], "ACC-PAY-001")

    def test_run_workflow_requires_shared_login_state(self) -> None:
        """未登录时，缴款 workflow 应明确提示先完成登录。"""

        config = build_valid_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "missing-login-state.json"
            with mock.patch.dict(
                os.environ,
                {"QXY_LOGIN_STATE_PATH": str(state_path)},
                clear=False,
            ):
                with self.assertRaisesRegex(LoginStateError, "未检测到共享登录态"):
                    payment_workflow.run_workflow(config, only_steps={"payment"})


class PaymentCliOutputTest(unittest.TestCase):
    """覆盖 CLI 的结构化错误输出。"""

    def test_workflow_cli_outputs_structured_error_json(self) -> None:
        """缴款 workflow CLI 失败时应输出 JSON 错误。"""

        stdout_buffer = io.StringIO()
        with mock.patch.object(payment_workflow, "run_workflow", side_effect=LoginStateError("未检测到共享登录态")):
            with mock.patch.object(payment_workflow, "load_workflow_config", return_value=build_valid_config()):
                with mock.patch.object(
                    sys,
                    "argv",
                    ["payment_workflow.py", "run", "--config", "/tmp/mock.json"],
                ):
                    with mock.patch("sys.stdout", stdout_buffer):
                        exit_code = payment_workflow.main()

        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["type"], "LoginStateError")

    def test_mcp_client_outputs_structured_error_json(self) -> None:
        """缴款 MCP 客户端失败时应输出 JSON 错误。"""

        stdout_buffer = io.StringIO()
        with mock.patch.object(
            payment_mcp_client,
            "call_tool",
            side_effect=QXYMCPError("服务调用失败"),
        ):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "mcp_client.py",
                    "--service",
                    "tax_payment",
                    "--tool",
                    "load_payment_task",
                ],
            ):
                with mock.patch("sys.stdout", stdout_buffer):
                    exit_code = payment_mcp_client.main()

        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["type"], "QXYMCPError")


class PaymentTransportSslTest(unittest.TestCase):
    """覆盖 SSL 与传输层容错。"""

    def test_send_jsonrpc_reports_certificate_error_clearly(self) -> None:
        """证书校验失败时应输出明确错误。"""

        cert_error = qxy_mcp_lib.ssl.SSLCertVerificationError("certificate verify failed")
        with mock.patch.object(qxy_mcp_lib, "urlopen", side_effect=cert_error):
            with self.assertRaisesRegex(QXYMCPError, "SSL 证书校验失败"):
                qxy_mcp_lib._send_jsonrpc(
                    "https://example.com/mcp",
                    "initialize",
                    {},
                    1,
                )

    def test_send_jsonrpc_retries_retryable_ssl_error(self) -> None:
        """瞬时 SSL EOF 应触发短重试。"""

        response = mock.MagicMock()
        response.headers.get.return_value = "SESSION-1"
        response.read.return_value = b'{"jsonrpc":"2.0","result":{"ok":true},"id":1}'
        context_manager = mock.MagicMock()
        context_manager.__enter__.return_value = response
        context_manager.__exit__.return_value = False

        with mock.patch.object(
            qxy_mcp_lib,
            "urlopen",
            side_effect=[qxy_mcp_lib.ssl.SSLEOFError("EOF occurred"), context_manager],
        ) as mock_urlopen:
            with mock.patch.object(qxy_mcp_lib.time, "sleep") as mock_sleep:
                result, session_id = qxy_mcp_lib._send_jsonrpc(
                    "https://example.com/mcp",
                    "initialize",
                    {},
                    1,
                )

        self.assertEqual(session_id, "SESSION-1")
        self.assertEqual(result["result"]["ok"], True)
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
