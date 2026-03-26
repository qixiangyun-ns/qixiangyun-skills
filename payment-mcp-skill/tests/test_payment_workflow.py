#!/usr/bin/env python3
"""payment_workflow 单元测试。"""

from __future__ import annotations

import json
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
from login_state import LoginStateError  # noqa: E402
from qxy_mcp_lib import QXYWorkflowError  # noqa: E402


def build_valid_config() -> dict[str, Any]:
    """构建可复用的有效配置。"""

    config = payment_workflow.build_sample_config()
    config["aggOrgId"] = "4788840764917695"
    config["year"] = 2026
    config["period"] = 3
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
        self.assertEqual(result["period"], 3)
        self.assertEqual(result["detail"][0]["yzpzzlDm"], "BDA0610606")
        self.assertEqual(result["detail"][0]["fromDate"], "2026-03-01")
        self.assertEqual(result["detail"][0]["toDate"], "2026-03-31")
        self.assertEqual(result["detail"][0]["taxAmount"], 128.5)

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


if __name__ == "__main__":
    unittest.main()
