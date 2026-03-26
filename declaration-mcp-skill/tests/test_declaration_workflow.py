#!/usr/bin/env python3
"""declaration_workflow 单元测试。"""

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

import declaration_workflow  # noqa: E402
from login_state_support import LoginStateError  # noqa: E402


def build_valid_config() -> dict[str, Any]:
    """构建可复用的有效配置。"""

    config = declaration_workflow.build_sample_config()
    config["aggOrgId"] = "4788840764917695"
    config["year"] = 2026
    config["period"] = 3
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
