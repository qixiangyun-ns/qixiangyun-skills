#!/usr/bin/env python3
"""declaration_workflow 单元测试。"""

from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import declaration_workflow  # noqa: E402
import mcp_client as declaration_mcp_client  # noqa: E402
import qxy_mcp_lib  # noqa: E402
import rules_engine  # noqa: E402
from login_state_support import LoginStateError  # noqa: E402
from qxy_mcp_lib import QXYMCPError  # noqa: E402


def build_valid_config() -> dict[str, Any]:
    """构建可复用的有效配置。"""

    today = date.today()
    config = declaration_workflow.build_sample_config()
    config["aggOrgId"] = "4788840764917695"
    config["year"] = today.year
    config["period"] = today.month
    config["accountId"] = None
    config["steps"]["fetch_roster"]["enabled"] = True
    for step_name, step_cfg in config["steps"].items():
        if step_name != "fetch_roster":
            step_cfg["enabled"] = False
    return config


class DeclarationWorkflowLoginGuardTest(unittest.TestCase):
    """覆盖申报与登录 skill 的联动行为。"""

    @staticmethod
    def _write_login_state(state_path: Path) -> None:
        payload = {
            "version": 1,
            "ready": True,
            "aggOrgId": "4788840764917695",
            "accountId": "ACC-LOGIN-001",
            "source": "cache",
        }
        state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_run_workflow_uses_shared_login_state(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """申报 workflow 应自动读取共享登录态并复用 accountId。"""

        config = build_valid_config()
        mock_call_tool.return_value = {"taskId": "TASK-DECL-001", "businessStatus": 1}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {"businessStatus": 3},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "login-state.json"
            self._write_login_state(state_path)

            with mock.patch.dict(
                os.environ,
                {"QXY_LOGIN_STATE_PATH": str(state_path)},
                clear=False,
            ):
                result = declaration_workflow.run_workflow(
                    config,
                    only_steps={"fetch_roster"},
                )

        self.assertEqual(result["accountId"], "ACC-LOGIN-001")
        self.assertEqual(result["login"]["source"], "cache")
        _, _, payload = mock_call_tool.call_args.args
        self.assertEqual(payload["accountId"], "ACC-LOGIN-001")

    def test_run_workflow_requires_shared_login_state(self) -> None:
        """未登录时，申报 workflow 应明确提示先完成登录。"""

        config = build_valid_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "missing-login-state.json"
            with mock.patch.dict(
                os.environ,
                {"QXY_LOGIN_STATE_PATH": str(state_path)},
                clear=False,
            ):
                with self.assertRaisesRegex(LoginStateError, "未检测到共享登录态"):
                    declaration_workflow.run_workflow(config, only_steps={"fetch_roster"})


class DeclarationWorkflowConfigTest(unittest.TestCase):
    """覆盖样例配置和初始化前置校验。"""

    def test_build_sample_config_accepts_year_and_period(self) -> None:
        """脚手架应允许显式指定申报月份，并回推出上月所属期。"""

        config = declaration_workflow.build_sample_config(2025, 12)

        self.assertEqual(config["year"], 2025)
        self.assertEqual(config["period"], 12)
        self.assertEqual(config["max_poll_attempts"], 30)
        self.assertEqual(
            config["steps"]["init_data"]["zsxmList"][0]["ssqQ"],
            "2025-11-01",
        )
        self.assertEqual(
            config["steps"]["history_pdf"]["skssqq"],
            "2025-11-01",
        )

    def test_build_sample_config_handles_january_filing_month(self) -> None:
        """1 月申报应自动回推到上一年 12 月所属期。"""

        config = declaration_workflow.build_sample_config(2026, 1)

        self.assertEqual(
            config["steps"]["init_data"]["zsxmList"][0]["ssqQ"],
            "2025-12-01",
        )
        self.assertEqual(
            config["steps"]["init_data"]["zsxmList"][0]["ssqZ"],
            "2025-12-31",
        )

    def test_run_init_data_rejects_known_unsupported_tax_code(self) -> None:
        """已知不支持的税种应在本地直接拦截。"""

        config = build_valid_config()
        step_cfg = {
            "enabled": True,
            "query_after_start": True,
            "zsxmList": [
                {
                    "yzpzzlDm": "BDA0610135",
                    "ssqQ": "2026-03-01",
                    "ssqZ": "2026-03-31",
                }
            ],
        }

        with self.assertRaisesRegex(Exception, "个人所得税当前不支持初始化"):
            declaration_workflow.run_init_data(step_cfg, config)

    def test_run_init_data_rejects_catalog_unsupported_tax_code(self) -> None:
        """附录中声明不支持初始化的税种也应统一拦截。"""

        config = build_valid_config()
        step_cfg = {
            "enabled": True,
            "query_after_start": True,
            "zsxmList": [
                {
                    "yzpzzlDm": "BDA0610857",
                    "ssqQ": "2026-03-01",
                    "ssqZ": "2026-03-31",
                }
            ],
        }

        with self.assertRaisesRegex(Exception, "残保金"):
            declaration_workflow.run_init_data(step_cfg, config)

    @mock.patch.object(declaration_workflow, "ensure_current_filing_period")
    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_run_init_data_auto_fills_monthly_range_from_filing_month(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """月报税种在未传所属期起止时应按申报月份回推上月。"""

        _ = mock_current_period
        config = build_valid_config()
        config["year"] = 2026
        config["period"] = 4
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["init_data"]["enabled"] = True
        config["steps"]["init_data"]["zsxmList"] = [
            {
                "yzpzzlDm": "BDA0610606",
                "period_cycle": "monthly",
            }
        ]
        mock_call_tool.return_value = {"taskId": "TASK-INIT-001"}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {"code": "SUCCESS", "message": "成功", "data": {}},
        }

        temp_dir = tempfile.TemporaryDirectory()
        state_path = Path(temp_dir.name) / "login-state.json"
        DeclarationWorkflowLoginGuardTest._write_login_state(state_path)
        with temp_dir:
            with mock.patch.dict(os.environ, {"QXY_LOGIN_STATE_PATH": str(state_path)}, clear=False):
                result = declaration_workflow.run_workflow(config, only_steps={"init_data"})

        self.assertEqual(result["steps"]["init_data"]["normalized_status"], "success")
        start_payload = mock_call_tool.call_args_list[0].args[2]
        self.assertEqual(start_payload["zsxmList"][0]["ssqQ"], "2026-03-01")
        self.assertEqual(start_payload["zsxmList"][0]["ssqZ"], "2026-03-31")

    @mock.patch.object(declaration_workflow, "ensure_current_filing_period")
    def test_run_init_data_requires_explicit_cycle_for_ambiguous_tax_code(
        self,
        mock_current_period: mock.Mock,
    ) -> None:
        """月季不明确的税种未指定周期时应直接报错。"""

        _ = mock_current_period
        config = build_valid_config()
        config["year"] = 2026
        config["period"] = 4
        step_cfg = {
            "enabled": True,
            "query_after_start": True,
            "zsxmList": [{"yzpzzlDm": "BDA0611159"}],
        }

        with self.assertRaisesRegex(Exception, "必须显式指定 `period_cycle`"):
            declaration_workflow.run_init_data(step_cfg, config)

    @mock.patch.object(declaration_workflow, "ensure_current_filing_period")
    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_run_init_data_auto_fills_quarterly_range_when_cycle_explicit(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """显式季度周期时应换算为上一自然季度。"""

        _ = mock_current_period
        config = build_valid_config()
        config["year"] = 2026
        config["period"] = 4
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["init_data"]["enabled"] = True
        config["steps"]["init_data"]["zsxmList"] = [
            {
                "yzpzzlDm": "BDA0611159",
                "period_cycle": "quarterly",
            }
        ]
        mock_call_tool.return_value = {"taskId": "TASK-INIT-QUARTER-001"}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {"code": "SUCCESS", "message": "成功", "data": {}},
        }

        temp_dir = tempfile.TemporaryDirectory()
        state_path = Path(temp_dir.name) / "login-state.json"
        DeclarationWorkflowLoginGuardTest._write_login_state(state_path)
        with temp_dir:
            with mock.patch.dict(os.environ, {"QXY_LOGIN_STATE_PATH": str(state_path)}, clear=False):
                result = declaration_workflow.run_workflow(config, only_steps={"init_data"})

        self.assertEqual(result["steps"]["init_data"]["normalized_status"], "success")
        start_payload = mock_call_tool.call_args_list[0].args[2]
        self.assertEqual(start_payload["zsxmList"][0]["ssqQ"], "2026-01-01")
        self.assertEqual(start_payload["zsxmList"][0]["ssqZ"], "2026-03-31")


class DeclarationCliOutputTest(unittest.TestCase):
    """覆盖 CLI 的结构化错误输出。"""

    def test_workflow_cli_outputs_structured_error_json(self) -> None:
        """申报 workflow CLI 失败时应输出 JSON 错误。"""

        stdout_buffer = io.StringIO()
        with mock.patch.object(declaration_workflow, "run_workflow", side_effect=LoginStateError("未检测到共享登录态")):
            with mock.patch.object(declaration_workflow, "load_workflow_config", return_value=build_valid_config()):
                with mock.patch.object(
                    sys,
                    "argv",
                    ["declaration_workflow.py", "run", "--config", "/tmp/mock.json"],
                ):
                    with mock.patch("sys.stdout", stdout_buffer):
                        exit_code = declaration_workflow.main()

        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["type"], "LoginStateError")

    def test_mcp_client_outputs_structured_error_json(self) -> None:
        """申报 MCP 客户端失败时应输出 JSON 错误。"""

        stdout_buffer = io.StringIO()
        with mock.patch.object(
            declaration_mcp_client,
            "call_tool",
            side_effect=QXYMCPError("服务调用失败"),
        ):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "mcp_client.py",
                    "--service",
                    "roster_entry",
                    "--tool",
                    "initiate_declaration_entry_task_auto",
                ],
            ):
                with mock.patch("sys.stdout", stdout_buffer):
                    exit_code = declaration_mcp_client.main()

        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["type"], "QXYMCPError")

    def test_mcp_client_rejects_non_current_filing_period_for_roster_entry(self) -> None:
        """原子调用应在本地拦截把所属期月份误传为申报月份的场景。"""

        stdout_buffer = io.StringIO()
        with mock.patch.object(
            declaration_mcp_client,
            "ensure_current_filing_period",
            side_effect=ValueError("`period` 表示申报月份，不是税款所属期月份。"),
        ):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "mcp_client.py",
                    "--service",
                    "roster_entry",
                    "--tool",
                    "initiate_declaration_entry_task_auto",
                    "--args",
                    '{"aggOrgId":"4788840764917695","year":2026,"period":3}',
                ],
            ):
                with mock.patch("sys.stdout", stdout_buffer):
                    exit_code = declaration_mcp_client.main()

        self.assertEqual(exit_code, 2)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertIn("申报月份", payload["error"]["message"])


class DeclarationWorkflowEnhancedTest(unittest.TestCase):
    """覆盖增强后的轮询、规则和恢复能力。"""

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

    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_tax_report_success_sets_pdf_action_when_no_payment(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """申报成功且税额较小时应给出 PDF 动作。"""

        config = build_valid_config()
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["tax_report"]["enabled"] = True
        config["steps"]["tax_report"]["tax_data"] = {"foo": "bar"}

        mock_call_tool.return_value = {"taskId": "TASK-TAX-001", "businessStatus": 1}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {
                "code": "2000",
                "message": "申报成功",
                "data": {"taxAmount": "0.50"},
            },
        }

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = declaration_workflow.run_workflow(config, only_steps={"tax_report"})

        tax_step = result["steps"]["tax_report"]
        self.assertEqual(result["workflow_state"], "success")
        self.assertEqual(tax_step["normalized_status"], "success")
        self.assertEqual(tax_step["pdf_action"], "download_pdf")
        self.assertIsNone(tax_step["payment_action"])
        self.assertTrue(tax_step["customer_visible"])

    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_pending_poll_sets_resume_and_retry_time(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """短轮询未完成时应进入待恢复状态。"""

        config = build_valid_config()
        mock_call_tool.return_value = {"taskId": "TASK-ROSTER-001", "businessStatus": 1}
        mock_poll_tool.return_value = {
            "state": "pending",
            "attempts": 30,
            "result": {
                "code": "4999",
                "message": "税局繁忙",
                "data": {"taskId": "TASK-ROSTER-001"},
            },
            "next_retry_after_minutes": 30,
        }

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = declaration_workflow.run_workflow(config, only_steps={"fetch_roster"})

        step = result["steps"]["fetch_roster"]
        self.assertEqual(result["workflow_state"], "pending")
        self.assertEqual(result["next_action"], "resume")
        self.assertEqual(step["normalized_status"], "pending")
        self.assertIsNotNone(step["next_retry_at"])

    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_init_data_blocks_when_tax_burden_out_of_range(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """行业税负率超阈值时应阻断自动申报。"""

        config = build_valid_config()
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["init_data"]["enabled"] = True
        config["rules"]["tax_burden_enabled"] = True
        config["rules"]["tax_burden_blocking"] = True
        config["rules"]["industry_name"] = "批发和零售业"

        init_query_result = {
            "code": "SUCCESS",
            "message": "",
            "data": {
                "initData": {
                    "zbGrid": {
                        "rows": [
                            {
                                "ewblxh": "1",
                                "asysljsxse": "100.00",
                                "ajybfjsxse": "0.00",
                                "mdtbfckxse": "0.00",
                                "msxse": "0.00",
                                "bqybtse": "10.00",
                                "bqybtsecjs": "1.00",
                                "bqybtsejyfj": "0.50",
                                "bqybtsedfjyfj": "0.50"
                            },
                            {
                                "ewblxh": "3",
                                "asysljsxse": "0.00",
                                "ajybfjsxse": "0.00",
                                "mdtbfckxse": "0.00",
                                "msxse": "0.00",
                                "bqybtse": "0.00"
                            }
                        ]
                    }
                }
            }
        }
        mock_call_tool.return_value = {"taskId": "TASK-INIT-001"}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": init_query_result,
        }

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = declaration_workflow.run_workflow(config, only_steps={"init_data"})

        step = result["steps"]["init_data"]
        self.assertEqual(step["normalized_status"], "manual_review_required")
        self.assertIn("行业税负率超阈值", step["operator_advice"])

    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_init_data_pending_query_uses_short_poll_and_sets_resume(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """初始化查询命中进行中提示时应保持 pending 并生成恢复信息。"""

        config = build_valid_config()
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["init_data"]["enabled"] = True
        config["steps"]["init_data"]["zsxmList"] = [
            {"yzpzzlDm": "BDA0610606", "period_cycle": "monthly"},
            {"yzpzzlDm": "BDA0611159", "period_cycle": "quarterly"},
        ]
        mock_call_tool.return_value = {
            "code": "SUCCESS",
            "data": {
                "taskIds": [
                    {"yzpzzlDm": "BDA0610606", "taskId": "TASK-INIT-001"},
                    {"yzpzzlDm": "BDA0611159", "taskId": "TASK-INIT-002"},
                ]
            },
        }
        mock_poll_tool.side_effect = [
            {
                "state": "pending",
                "attempts": 30,
                "result": {"message": "初始化任务还在执行中，请稍后获取！"},
                "next_retry_after_minutes": 30,
                "history": [],
            },
            {
                "state": "pending",
                "attempts": 30,
                "result": {"message": "初始化任务还在执行中，请稍后获取！"},
                "next_retry_after_minutes": 30,
                "history": [],
            },
        ]

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = declaration_workflow.run_workflow(config, only_steps={"init_data"})

        step = result["steps"]["init_data"]
        self.assertEqual(result["workflow_state"], "pending")
        self.assertEqual(step["normalized_status"], "pending")
        self.assertEqual(step["attempt_count"], 60)
        self.assertEqual(mock_poll_tool.call_count, 2)
        self.assertIsNotNone(step["next_retry_at"])

    @mock.patch.object(declaration_workflow, "call_tool")
    def test_tax_report_auto_patch_payload_uses_accrual_analysis(self, mock_call_tool: mock.Mock) -> None:
        """自动改写模式应把规则计算结果写入待申报报文。"""

        config = build_valid_config()
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["tax_report"]["enabled"] = True
        config["steps"]["tax_report"]["tax_data"] = {"computed": {}}
        config["rules"]["accrual_mode"] = "auto_patch_payload"

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                runner = declaration_workflow.WorkflowRunner(config)
                runner.context["artifacts"]["init_queries"] = [
                    {
                        "yzpzzlDm": "BDA0610606",
                        "result": {
                            "code": "SUCCESS",
                            "message": "",
                            "data": {
                                "initData": {
                                    "zbGrid": {
                                        "rows": [
                                            {
                                                "ewblxh": "1",
                                                "asysljsxse": "100.00",
                                                "ajybfjsxse": "50.00",
                                                "mdtbfckxse": "0.00",
                                                "msxse": "0.00",
                                                "bqybtse": "5.00",
                                                "bqybtsecjs": "0.30",
                                                "bqybtsejyfj": "0.10",
                                                "bqybtsedfjyfj": "0.10"
                                            },
                                            {
                                                "ewblxh": "3",
                                                "asysljsxse": "20.00",
                                                "ajybfjsxse": "0.00",
                                                "mdtbfckxse": "0.00",
                                                "msxse": "0.00",
                                                "bqybtse": "1.00"
                                            }
                                        ]
                                    }
                                }
                            }
                        },
                    }
                ]
                mock_call_tool.return_value = {"taskId": "TASK-TAX-001", "businessStatus": 1}
                step_result = runner.execute_step("tax_report", phase="start")

        computed = step_result["request_payload"]["tax_data"]["computed"]
        self.assertEqual(round(computed["sales_amount"], 2), 170.00)
        self.assertEqual(round(computed["total_tax_amount"], 2), 6.50)
        self.assertEqual(step_result["normalized_status"], "pending")

    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_resume_runner_queries_existing_task_from_checkpoint(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """checkpoint 恢复后应直接使用已有 taskId 查询。"""

        config = build_valid_config()
        mock_call_tool.return_value = {"taskId": "TASK-ROSTER-002", "businessStatus": 1}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {"businessStatus": 3, "code": "2000", "message": "成功"},
        }

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            checkpoint_path = Path(temp_dir.name) / "resume-checkpoint.json"
            with mock.patch.dict(os.environ, env_dict, clear=False):
                runner = declaration_workflow.WorkflowRunner(config, checkpoint_path=checkpoint_path)
                started = runner.execute_step("fetch_roster", phase="start")
                self.assertEqual(started["normalized_status"], "pending")

                restored_runner = declaration_workflow.WorkflowRunner.from_checkpoint(checkpoint_path)
                result = restored_runner.execute_step("fetch_roster", phase="query")

        self.assertEqual(result["taskId"], "TASK-ROSTER-002")
        self.assertEqual(result["normalized_status"], "success")

    @mock.patch.object(declaration_workflow, "ensure_current_filing_period")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_init_data_blocks_platform_unsupported_annual_settlement(
        self,
        mock_call_tool: mock.Mock,
        mock_current_period: mock.Mock,
    ) -> None:
        """平台声明汇算清缴未上线时应直接失败并终止后续步骤。"""

        _ = mock_current_period
        config = build_valid_config()
        config["year"] = 2026
        config["period"] = 4
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["init_data"]["enabled"] = True
        config["steps"]["init_data"]["zsxmList"] = [
            {"yzpzzlDm": "BDA0610606", "period_cycle": "monthly"}
        ]
        config["steps"]["tax_report"]["enabled"] = True
        config["steps"]["tax_report"]["tax_data"] = {"foo": "bar"}
        mock_call_tool.return_value = {
            "code": "BUSINESS_ERROR",
            "message": "汇算清缴当前不支持操作，敬请期待上线！",
        }

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                result = declaration_workflow.run_workflow(config, only_steps={"init_data", "tax_report"})

        self.assertEqual(result["workflow_state"], "failed")
        self.assertNotIn("tax_report", result["steps"])
        step = result["steps"]["init_data"]
        self.assertEqual(step["normalized_status"], "failed")
        self.assertIn("平台侧暂未支持", step["operator_advice"])
        mock_call_tool.assert_called_once()

    @mock.patch.object(declaration_workflow, "poll_tool")
    @mock.patch.object(declaration_workflow, "call_tool")
    def test_resume_skips_failed_finalized_step_and_continues(
        self,
        mock_call_tool: mock.Mock,
        mock_poll_tool: mock.Mock,
    ) -> None:
        """resume 默认应跳过已失败终态步骤，继续执行后续目标步骤。"""

        config = build_valid_config()
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["init_data"]["enabled"] = True
        config["steps"]["tax_report"]["enabled"] = True
        config["steps"]["tax_report"]["tax_data"] = {"foo": "bar"}
        mock_call_tool.return_value = {"taskId": "TASK-TAX-RESUME-001", "businessStatus": 1}
        mock_poll_tool.return_value = {
            "state": "success",
            "attempts": 1,
            "result": {"code": "2000", "message": "申报成功", "data": {"taxAmount": "0.00"}},
        }

        temp_dir, env_dict = self._with_login_state()
        with temp_dir:
            with mock.patch.dict(os.environ, env_dict, clear=False):
                runner = declaration_workflow.WorkflowRunner(config)
                runner.context["workflow_state"] = "failed"
                runner.context["steps"]["init_data"] = {
                    "normalized_status": "failed",
                    "taskId": None,
                }
                result = runner.run(only_steps={"init_data", "tax_report"}, resume=True)

        self.assertEqual(result["workflow_state"], "success")
        self.assertEqual(result["steps"]["tax_report"]["normalized_status"], "success")
        self.assertEqual(mock_call_tool.call_count, 1)


class DeclarationRulesEngineTest(unittest.TestCase):
    """覆盖规则引擎的所属期展示。"""

    def test_match_response_rule_prefers_actual_period_range(self) -> None:
        """对客话术应优先显示实际税款所属期范围。"""

        rule_sets = rules_engine.load_rule_sets()
        config = declaration_workflow.build_sample_config(2026, 4)
        config["steps"]["init_data"]["zsxmList"] = [
            {
                "yzpzzlDm": "BDA0610606",
                "ssqQ": "2026-03-01",
                "ssqZ": "2026-03-31",
            }
        ]

        matched = rules_engine.match_response_rule(
            payload={
                "code": "2000",
                "message": "申报成功",
                "data": {"taxAmount": "0.50"},
            },
            step_name="tax_report",
            step_cfg=config["steps"]["tax_report"],
            config=config,
            rule_sets=rule_sets,
            tax_label="增值税",
        )

        self.assertIn("2026-03-01~2026-03-31", matched["customer_message"])
        self.assertNotIn("申报月份 2026-04", matched["customer_message"])


class DeclarationTransportSslTest(unittest.TestCase):
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


class DeclarationWorkflowCompatibilityTest(unittest.TestCase):
    """覆盖兼容性输入与本地状态推断。"""

    def test_current_pdf_accepts_string_tax_code_list(self) -> None:
        """当期 PDF 步骤应兼容字符串税种数组。"""

        config = build_valid_config()
        step_cfg = {
            "zsxmList": ["BDA0610606", "BDA0611159"],
            "analysisPdf": "Y",
        }

        payload = declaration_workflow._build_current_pdf_args(step_cfg, config)

        self.assertEqual(
            payload["zsxmList"],
            [{"yzpzzlDm": "BDA0610606"}, {"yzpzzlDm": "BDA0611159"}],
        )

    def test_fixed_catalog_period_cycle_conflict_raises(self) -> None:
        """税种目录为固定周期时，本地配置冲突应直接报错。"""

        config = build_valid_config()
        config["year"] = 2026
        config["period"] = 4
        step_cfg = {
            "zsxmList": [{"yzpzzlDm": "BDA0610994", "period_cycle": "monthly"}],
        }

        with self.assertRaisesRegex(Exception, "目录周期为 `annual`"):
            declaration_workflow._resolve_init_data_zsxm_list(
                step_cfg,
                config,
                declaration_workflow.load_rule_sets(),
            )

    @mock.patch.object(declaration_workflow, "call_tool")
    def test_financial_report_empty_payload_is_skipped(self, mock_call_tool: mock.Mock) -> None:
        """财报数据为空时应输出明确 skipped，而不是静默跳过。"""

        config = build_valid_config()
        config["steps"]["fetch_roster"]["enabled"] = False
        config["steps"]["financial_report"]["enabled"] = True
        config["steps"]["financial_report"]["cbData"] = {}
        config["steps"]["financial_report"]["cbnbData"] = None

        temp_dir = tempfile.TemporaryDirectory()
        state_path = Path(temp_dir.name) / "login-state.json"
        DeclarationWorkflowLoginGuardTest._write_login_state(state_path)
        with temp_dir:
            with mock.patch.dict(os.environ, {"QXY_LOGIN_STATE_PATH": str(state_path)}, clear=False):
                runner = declaration_workflow.WorkflowRunner(config)
                step = runner.execute_step("financial_report", phase="run")

        self.assertEqual(step["normalized_status"], "skipped")
        self.assertIn("cbData/cbnbData 为空", step["business_message"])
        mock_call_tool.assert_not_called()

    def test_infer_task_state_treats_init_in_progress_message_as_pending(self) -> None:
        """初始化进行中的提示语应识别为 pending。"""

        state = qxy_mcp_lib.infer_task_state({"message": "初始化任务还在执行中，请稍后获取！"})

        self.assertEqual(state, "pending")
