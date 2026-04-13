#!/usr/bin/env python3
"""enterprise_filing_workflow 单元测试。"""

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

import enterprise_filing_workflow  # noqa: E402


def build_valid_config() -> dict[str, Any]:
    """构造可复用配置。"""

    config = enterprise_filing_workflow.build_sample_config(2026, 4)
    config["enterprises"][0]["aggOrgId"] = "4788840764917695"
    config["enterprises"][0]["accountId"] = None
    return config


class EnterpriseFilingWorkflowTest(unittest.TestCase):
    """覆盖企业级编排核心场景。"""

    @staticmethod
    def _write_login_state(state_path: Path) -> None:
        payload = {
            "version": 1,
            "ready": True,
            "aggOrgId": "4788840764917695",
            "accountId": "ACC-LOGIN-001",
            "source": "cache",
        }
        state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _with_login_state(self) -> tuple[tempfile.TemporaryDirectory[str], dict[str, str]]:
        temp_dir = tempfile.TemporaryDirectory()
        state_path = Path(temp_dir.name) / "login-state.json"
        self._write_login_state(state_path)
        return temp_dir, {"QXY_LOGIN_STATE_PATH": str(state_path)}

    @mock.patch.object(enterprise_filing_workflow, "ensure_current_filing_period")
    def test_excel_financial_input_reads_file_and_builds_payload(
        self,
        mock_current_period: mock.Mock,
    ) -> None:
        """Excel 财报应读取文件并转为 base64 报文。"""

        _ = mock_current_period
        config = build_valid_config()
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "report.xlsx"
            file_path.write_bytes(b"excel-bytes")
            financial_input = enterprise_filing_workflow._normalize_financial_report_input(
                {
                    "mode": "excel",
                    "file_path": str(file_path),
                    "yzpzzlDm": "CWBBSB",
                    "ssqQ": "2026-01-01",
                    "ssqZ": "2026-03-31",
                    "zlbsxlDm": "ZL1001003",
                },
                "financial_report_input",
            )

            temp_login_dir, env_dict = self._with_login_state()
            with temp_login_dir:
                with mock.patch.dict(os.environ, env_dict, clear=False):
                    runner = enterprise_filing_workflow.EnterpriseRunner(
                        config["enterprises"][0],
                        poll_interval_seconds=10,
                        max_poll_attempts=30,
                        checkpoint_path=Path(temp_dir) / "checkpoint.json",
                    )
                    with mock.patch.object(enterprise_filing_workflow, "call_tool", return_value={"taskId": "TASK-FI-001"}) as mock_call_tool:
                        with mock.patch.object(
                            enterprise_filing_workflow,
                            "poll_tool",
                            return_value={"state": "success", "attempts": 1, "result": {"code": "2000", "message": "申报成功"}},
                        ):
                            step = runner._run_financial_report(financial_input)

        payload = mock_call_tool.call_args.args[2]
        self.assertEqual(step["normalized_status"], "success")
        self.assertEqual(payload["zsxmList"][0]["attachName"], "report.xlsx")
        self.assertEqual(payload["zsxmList"][0]["templateCode"], "0")
        self.assertTrue(payload["zsxmList"][0]["attachEncode"])

    def test_json_financial_input_requires_payload(self) -> None:
        """JSON 财报至少应包含一个报文对象。"""

        with self.assertRaisesRegex(Exception, "至少需要 `cbData` 或 `cbnbData`"):
            enterprise_filing_workflow._normalize_financial_report_input(
                {"mode": "json"},
                "financial_report_input",
            )

    @mock.patch.object(enterprise_filing_workflow, "ensure_current_filing_period")
    @mock.patch.object(enterprise_filing_workflow, "poll_tool")
    @mock.patch.object(enterprise_filing_workflow, "call_tool")
    def test_run_returns_awaiting_financial_report_when_roster_contains_financial_code(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """清册含财报但未提供财报输入时应挂起等待用户上传。"""

        _ = mock_current_period
        config = build_valid_config()
        config["enterprises"][0]["financial_report_input"] = None
        mock_call_tool.return_value = {"taskId": "TASK-ROSTER-001"}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {
                "code": "2000",
                "data": {"detail": [{"yzpzzlDm": "CWBBSB"}, {"yzpzzlDm": "BDA0610606"}]},
            },
        }

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = enterprise_filing_workflow.run_workflow(config)

        enterprise_result = result["enterprises"][0]["result"]
        self.assertEqual(enterprise_result["status"], "awaiting_financial_report")
        self.assertIn("清册包含财务报表", enterprise_result["operator_advice"][0])

    @mock.patch.object(enterprise_filing_workflow, "ensure_current_filing_period")
    @mock.patch.object(enterprise_filing_workflow, "poll_tool")
    @mock.patch.object(enterprise_filing_workflow, "call_tool")
    def test_income_tax_zero_declare_uses_empty_tax_data_submission(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """企业所得税 A 类应通过空 tax_data 触发 MCP 兜底骨架。"""

        _ = mock_current_period
        config = build_valid_config()
        config["enterprises"][0]["financial_report_input"] = None
        mock_call_tool.side_effect = [
            {"taskId": "TASK-ROSTER-001"},
            {"taskId": "TASK-INIT-CIT-001"},
            {"taskId": "TASK-DECL-CIT-001"},
            {"taskId": "TASK-INFO-001"},
            {"taskId": "TASK-PDF-001"},
        ]
        mock_poll_tool.side_effect = [
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"detail": [{"yzpzzlDm": "BDA0611159"}]}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "SUCCESS", "message": "初始化成功", "data": {"initData": {"foo": "bar"}}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "message": "申报成功", "data": {"taxAmount": "0.00"}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"detail": [{"yzpzzlDm": "BDA0611159", "state": 1, "payState": -1, "fromDate": "2026-01-01", "toDate": "2026-03-31"}]}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"pdfFileUrl": "https://example.com/cit.pdf"}},
            },
        ]

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = enterprise_filing_workflow.run_workflow(config)

        enterprise_result = result["enterprises"][0]["result"]
        self.assertEqual(enterprise_result["status"], "success")
        tax_payload = mock_call_tool.call_args_list[2].args[2]
        self.assertEqual(tax_payload["tax_type"], "sdsData")
        self.assertEqual(tax_payload["tax_data"], {})

    @mock.patch.object(enterprise_filing_workflow, "ensure_current_filing_period")
    @mock.patch.object(enterprise_filing_workflow, "poll_tool")
    @mock.patch.object(enterprise_filing_workflow, "call_tool")
    def test_vat_non_zero_no_ticket_income_requires_manual_review(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """无票收入大于 0 时应挂起人工确认。"""

        _ = mock_current_period
        config = build_valid_config()
        config["enterprises"][0]["financial_report_input"] = None
        config["enterprises"][0]["vat_adjustment"]["no_ticket_income_amount"] = 188.5
        mock_call_tool.side_effect = [
            {"taskId": "TASK-ROSTER-001"},
            {"taskId": "TASK-INIT-VAT-001"},
        ]
        mock_poll_tool.side_effect = [
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"detail": [{"yzpzzlDm": "BDA0610606"}]}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "SUCCESS", "message": "初始化成功", "data": {"initData": {"foo": "bar"}}},
            },
        ]

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = enterprise_filing_workflow.run_workflow(config)

        enterprise_result = result["enterprises"][0]["result"]
        self.assertEqual(enterprise_result["status"], "manual_review_required")
        self.assertEqual(mock_call_tool.call_count, 2)
        self.assertIn("无票收入", enterprise_result["operator_advice"][0])

    @mock.patch.object(enterprise_filing_workflow, "ensure_current_filing_period")
    @mock.patch.object(enterprise_filing_workflow, "poll_tool")
    @mock.patch.object(enterprise_filing_workflow, "call_tool")
    def test_vat_success_generates_payment_preparation_and_pdf(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """增值税成功后应汇总 PDF 与缴款准备数据。"""

        _ = mock_current_period
        config = build_valid_config()
        config["enterprises"][0]["financial_report_input"] = None
        mock_call_tool.side_effect = [
            {"taskId": "TASK-ROSTER-001"},
            {"taskId": "TASK-INIT-VAT-001"},
            {"taskId": "TASK-DECL-VAT-001"},
            {"taskId": "TASK-INFO-001"},
            {"taskId": "TASK-PDF-001"},
        ]
        mock_poll_tool.side_effect = [
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"detail": [{"yzpzzlDm": "BDA0610606"}]}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "SUCCESS", "message": "初始化成功", "data": {"initData": {"foo": "bar"}}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "message": "申报成功", "data": {"taxAmount": "12.30"}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {
                    "code": "2000",
                    "data": {
                        "detail": [
                            {
                                "yzpzzlDm": "BDA0610606",
                                "state": 1,
                                "payState": 0,
                                "fromDate": "2026-03-01",
                                "toDate": "2026-03-31",
                                "taxAmount": "12.30",
                            }
                        ]
                    },
                },
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"pdfFileUrl": "https://example.com/vat.pdf"}},
            },
        ]

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = enterprise_filing_workflow.run_workflow(config)

        enterprise_result = result["enterprises"][0]["result"]
        self.assertEqual(enterprise_result["status"], "success")
        self.assertEqual(len(enterprise_result["payment_preparation"]["detail"]), 1)
        self.assertEqual(
            enterprise_result["payment_preparation"]["detail"][0]["taxAmount"],
            12.3,
        )
        self.assertEqual(
            enterprise_result["steps"]["current_pdf"]["request_payload"]["zsxmList"][0]["yzpzzlDm"],
            "BDA0610606",
        )

    @mock.patch.object(enterprise_filing_workflow, "ensure_current_filing_period")
    @mock.patch.object(enterprise_filing_workflow, "poll_tool")
    @mock.patch.object(enterprise_filing_workflow, "call_tool")
    def test_batch_serial_continues_after_awaiting_financial_report(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """批量串行下，前一个企业等待财报不应阻塞下一个企业。"""

        _ = mock_current_period
        config = build_valid_config()
        config["enterprises"] = [
            {
                "aggOrgId": "ENT-001",
                "accountId": None,
                "display_name": "企业一",
                "year": 2026,
                "period": 4,
                "financial_report_input": None,
                "vat_adjustment": {"no_ticket_income_amount": 0},
            },
            {
                "aggOrgId": "ENT-002",
                "accountId": None,
                "display_name": "企业二",
                "year": 2026,
                "period": 4,
                "financial_report_input": None,
                "vat_adjustment": {"no_ticket_income_amount": 0},
            },
        ]
        mock_call_tool.side_effect = [
            {"taskId": "TASK-ROSTER-001"},
            {"taskId": "TASK-ROSTER-002"},
            {"taskId": "TASK-INIT-VAT-002"},
            {"taskId": "TASK-DECL-VAT-002"},
            {"taskId": "TASK-INFO-002"},
            {"taskId": "TASK-PDF-002"},
        ]
        mock_poll_tool.side_effect = [
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"detail": [{"yzpzzlDm": "CWBBSB"}]}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"detail": [{"yzpzzlDm": "BDA0610606"}]}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "SUCCESS", "message": "初始化成功", "data": {"initData": {"foo": "bar"}}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "message": "申报成功", "data": {"taxAmount": "0.00"}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"detail": [{"yzpzzlDm": "BDA0610606", "state": 1, "payState": -1, "fromDate": "2026-03-01", "toDate": "2026-03-31"}]}},
            },
            {
                "state": "success",
                "attempts": 1,
                "result": {"code": "2000", "data": {"pdfFileUrl": "https://example.com/vat.pdf"}},
            },
        ]

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = enterprise_filing_workflow.run_workflow(config)

        self.assertEqual(result["summary"]["awaiting_financial_report"], 1)
        self.assertEqual(result["summary"]["success"], 1)
        second_result = [item["result"] for item in result["enterprises"] if item["aggOrgId"] == "ENT-002"][0]
        self.assertEqual(second_result["status"], "success")


if __name__ == "__main__":
    unittest.main()
