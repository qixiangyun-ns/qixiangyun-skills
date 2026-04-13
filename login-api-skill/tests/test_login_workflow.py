#!/usr/bin/env python3
"""Tax Login Skill 工作流单元测试。"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts import TaxLoginClient, TaxLoginError, TaxLoginWorkflow  # noqa: E402
from scripts import login_workflow as login_workflow_cli  # noqa: E402
from scripts.crypto import build_signature  # noqa: E402


class TaxLoginWorkflowTest(unittest.TestCase):
    """覆盖登录工作流核心编排。"""

    def setUp(self) -> None:
        self.client = mock.create_autospec(TaxLoginClient, instance=True)
        self.workflow = TaxLoginWorkflow(self.client)

    def test_create_natural_person_account_uses_dlfs17_without_agg_org_id(self) -> None:
        """自然人开户只走 dlfs=17，且不透传 aggOrgId。"""
        self.client.create_account_record.return_value = {
            "code": "2000",
            "success": True,
            "data": {"accountId": 1001, "dlfs": 17, "sflx": "BSY"},
        }

        result = self.workflow.create_natural_person_account("3100", "17633122441", "pwd")

        self.client.create_account_record.assert_called_once_with(
            agg_org_id=None,
            dq="31",
            username="17633122441",
            phone="17633122441",
            password="pwd",
            identity_type="BSY",
            login_mode=17,
        )
        self.assertEqual(result["account_id"], "1001")
        self.assertEqual(result["login_mode"], 17)

    def test_create_natural_person_account_reuses_existing_account(self) -> None:
        """账户已存在但返回 accountId 时，应按可继续成功处理。"""
        self.client.create_account_record.return_value = {
            "code": "PARAMETER_ERROR",
            "success": True,
            "message": "账户已经存在",
            "data": {"accountId": 1001, "aggOrgId": 0},
        }

        result = self.workflow.create_natural_person_account("3100", "17633122441", "pwd")

        self.assertTrue(result["success"])
        self.assertTrue(result["existing_account"])
        self.assertEqual(result["account_id"], "1001")
        self.assertNotIn("agg_org_id", result)
        self.assertNotIn("aggOrgId", json.dumps(result, ensure_ascii=False))

    def test_start_natural_person_login_only_uses_account_id(self) -> None:
        """自然人登录只允许使用 accountId。"""
        self.client.send_etax_login_sms.return_value = {
            "taskId": 2001,
            "smsCode": "123456",
            "aggOrgId": 0,
        }

        result = self.workflow.start_natural_person_login("1001")

        self.client.send_etax_login_sms.assert_called_once_with(
            account_id="1001",
            agg_org_id=None,
        )
        self.assertTrue(result["need_verify"])
        self.assertEqual(result["task_id"], "2001")
        self.assertNotIn("aggOrgId", json.dumps(result, ensure_ascii=False))

    def test_start_natural_person_login_handles_direct_success(self) -> None:
        """自然人发码接口若直接成功，也应按登录成功处理。"""
        self.client.send_etax_login_sms.return_value = {
            "code": "2000",
            "success": True,
            "message": "税局登录成功",
            "data": None,
        }

        result = self.workflow.start_natural_person_login("1001")

        self.assertTrue(result["login_success"])
        self.assertFalse(result["need_verify"])

    def test_verify_natural_person_login_routes_by_pending_task(self) -> None:
        """自然人验码应根据待处理任务来源路由底层接口。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            pending_task_path = Path(temp_dir) / "pending.json"
            self.client.login_flow_step1_send_sms.return_value = {
                "success": True,
                "need_verify": True,
                "task_id": "TASK-1",
            }
            self.client.verify_sms.return_value = {
                "code": "2000",
                "success": True,
                "message": "登录成功",
            }

            with mock.patch.dict(os.environ, {"QXY_LOGIN_PENDING_TASK_PATH": str(pending_task_path)}, clear=False):
                self.workflow.start_natural_person_login_by_phone("3100", "17633122441", "pwd")
                result = self.workflow.verify_natural_person_login("TASK-1", "654321")

        self.assertTrue(result["login_success"])
        self.client.verify_sms.assert_called_once_with(task_id="TASK-1", sms_code="654321")
        self.client.upload_etax_login_sms.assert_not_called()

    def test_list_enterprises_only_uses_account_id(self) -> None:
        """自然人企业列表只允许使用 accountId。"""
        self.client.query_nature_org_list.return_value = {
            "code": "2000",
            "success": True,
            "data": [{"xh": 0, "nsrsbh": "9131", "nsrmc": "企业A", "sflx": "BSY", "glzt": "00"}],
        }

        result = self.workflow.list_enterprises("1001")

        self.client.query_nature_org_list.assert_called_once_with(account_id="1001", agg_org_id=None)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["enterprises"][0]["identity_type"], "BSY")

    def test_choose_target_enterprise_supports_identity_type(self) -> None:
        """选企业应支持按税号和身份类型过滤。"""
        enterprises = [
            {"name": "测试企业", "nsrsbh": "9131", "identity_type": "BSY", "index": "0"},
            {"name": "测试企业", "nsrsbh": "9131", "identity_type": "KPY", "index": "1"},
        ]

        result = self.workflow.choose_target_enterprise(
            enterprises,
            nsrsbh="9131",
            identity_type="BSY",
        )

        self.assertEqual(result["identity_type"], "BSY")
        self.assertEqual(result["index"], "0")

    def test_subscribe_enterprise_service_returns_org_id(self) -> None:
        """企业订购成功后应返回 orgId/aggOrgId。"""
        self.client.order_product.return_value = {
            "code": "SUCCESS",
            "success": True,
            "data": {"aggOrgId": 3001},
        }

        result = self.workflow.subscribe_enterprise_service("3100", "企业A", "9131")

        self.assertEqual(result["org_id"], "3001")
        self.assertEqual(result["agg_org_id"], "3001")

    def test_create_multi_account_uses_enterprise_login_mode_14(self) -> None:
        """企业多账号默认应使用 dlfs=14。"""
        self.client.create_account_record.return_value = {
            "code": "2000",
            "success": True,
            "data": {"accountId": 4001, "aggOrgId": 3001, "dlfs": 14, "sflx": "BSY"},
        }

        result = self.workflow.create_multi_account("3001", "3100", "17633122441", "pwd")

        self.client.create_account_record.assert_called_once_with(
            agg_org_id="3001",
            dq="31",
            username="17633122441",
            phone="17633122441",
            password="pwd",
            identity_type="BSY",
            login_mode=14,
        )
        self.assertEqual(result["account_id"], "4001")

    def test_create_multi_account_reuses_existing_account(self) -> None:
        """企业多账号已存在且返回 accountId 时，应直接复用。"""
        self.client.create_account_record.return_value = {
            "code": "PARAMETER_ERROR",
            "success": True,
            "message": "账户已经存在",
            "data": {"accountId": 4001, "aggOrgId": 3001, "dlfs": 14},
        }

        result = self.workflow.create_multi_account("3001", "3100", "17633122441", "pwd")

        self.assertTrue(result["success"])
        self.assertTrue(result["existing_account"])
        self.assertEqual(result["account_id"], "4001")

    def test_start_enterprise_login_direct_success_saves_state(self) -> None:
        """企业登录若直接成功，应自动写共享登录态。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "login-state.json"
            self.client.send_etax_login_sms.return_value = {
                "code": "2000",
                "success": True,
                "message": "税局登录成功",
                "data": None,
            }

            with mock.patch.dict(os.environ, {"QXY_LOGIN_STATE_PATH": str(state_path)}, clear=False):
                result = self.workflow.start_enterprise_login(
                    "3001",
                    "4001",
                    enterprise_context={"orgId": "3001", "orgName": "企业A", "nsrsbh": "9131"},
                )

            self.assertTrue(result["login_success"])
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state_payload["source"], "enterprise_direct")
            self.assertEqual(state_payload["orgName"], "企业A")

    def test_verify_enterprise_login_saves_state(self) -> None:
        """企业验码成功后应写入共享登录态。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "login-state.json"
            self.client.upload_etax_login_sms.return_value = {
                "code": "2000",
                "success": True,
                "message": "登录成功",
            }

            with mock.patch.dict(os.environ, {"QXY_LOGIN_STATE_PATH": str(state_path)}, clear=False):
                result = self.workflow.verify_enterprise_login(
                    "TASK-2",
                    "123456",
                    "3001",
                    "4001",
                    enterprise_context={"orgId": "3001", "orgName": "企业A", "nsrsbh": "9131"},
                )

            self.assertTrue(result["login_success"])
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state_payload["taskId"], "TASK-2")

    def test_login_enterprise_account_preserves_context(self) -> None:
        """企业缓存命中后写登录态时应保留企业上下文。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "login-state.json"
            self.client.check_cache.return_value = {
                "code": "2000",
                "success": True,
                "data": True,
            }

            with mock.patch.dict(os.environ, {"QXY_LOGIN_STATE_PATH": str(state_path)}, clear=False):
                result = self.workflow.login_enterprise_account(
                    "3001",
                    "4001",
                    enterprise_context={"orgId": "3001", "orgName": "企业A", "nsrsbh": "9131"},
                )

            self.assertTrue(result["ready"])
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state_payload["source"], "cache")
            self.assertEqual(state_payload["nsrsbh"], "9131")


class TaxLoginCliTest(unittest.TestCase):
    """覆盖 CLI 的参数映射与结构化输出。"""

    def test_start_natural_login_uses_flow_state_account_id(self) -> None:
        """start-natural-login 未显式传参时，应从流程状态复用 accountId。"""
        fake_client = mock.create_autospec(TaxLoginClient, instance=True)
        fake_workflow = mock.create_autospec(TaxLoginWorkflow, instance=True)
        fake_workflow.start_natural_person_login.return_value = {
            "success": True,
            "task_id": "TASK-1",
            "account_id": "ACC-1",
            "message": "验证码已发送",
        }

        stdout_buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            flow_state_path = Path(temp_dir) / "flow.json"
            flow_state_path.write_text(
                json.dumps({"version": 1, "natural": {"accountId": "ACC-1"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "QXY_LOGIN_FLOW_STATE_PATH": str(flow_state_path),
                    "QXY_LOGIN_ENABLE_DEBUG_COMMANDS": "1",
                },
                clear=False,
            ):
                with mock.patch.object(login_workflow_cli.TaxLoginClient, "from_config", return_value=fake_client):
                    with mock.patch.object(login_workflow_cli, "TaxLoginWorkflow", return_value=fake_workflow):
                        with mock.patch.object(sys, "argv", ["login_workflow.py", "start-natural-login"]):
                            with mock.patch("sys.stdout", stdout_buffer):
                                exit_code = login_workflow_cli.main()

        self.assertEqual(exit_code, 0)
        fake_workflow.start_natural_person_login.assert_called_once_with(account_id="ACC-1")

    def test_list_enterprises_uses_flow_state_account_id(self) -> None:
        """list-enterprises 未显式传参时，应从流程状态复用 accountId。"""
        fake_client = mock.create_autospec(TaxLoginClient, instance=True)
        fake_workflow = mock.create_autospec(TaxLoginWorkflow, instance=True)
        fake_workflow.list_enterprises.return_value = {
            "success": True,
            "account_id": "ACC-1",
            "total": 0,
            "enterprises": [],
        }

        stdout_buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            flow_state_path = Path(temp_dir) / "flow.json"
            flow_state_path.write_text(
                json.dumps({"version": 1, "natural": {"accountId": "ACC-1"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "QXY_LOGIN_FLOW_STATE_PATH": str(flow_state_path),
                    "QXY_LOGIN_ENABLE_DEBUG_COMMANDS": "1",
                },
                clear=False,
            ):
                with mock.patch.object(login_workflow_cli.TaxLoginClient, "from_config", return_value=fake_client):
                    with mock.patch.object(login_workflow_cli, "TaxLoginWorkflow", return_value=fake_workflow):
                        with mock.patch.object(sys, "argv", ["login_workflow.py", "list-enterprises"]):
                            with mock.patch("sys.stdout", stdout_buffer):
                                exit_code = login_workflow_cli.main()

        self.assertEqual(exit_code, 0)
        fake_workflow.list_enterprises.assert_called_once_with(natural_account_id="ACC-1")

    def test_run_full_login_waits_for_natural_sms_instead_of_returning_final_success(self) -> None:
        """全链路命令在自然人验证码阶段必须返回未完成状态。"""
        fake_client = mock.create_autospec(TaxLoginClient, instance=True)
        fake_workflow = mock.create_autospec(TaxLoginWorkflow, instance=True)
        fake_workflow.create_natural_person_account.return_value = {
            "success": True,
            "account_id": "ACC-1",
            "login_mode": 17,
        }
        fake_workflow.start_natural_person_login.return_value = {
            "success": True,
            "need_verify": True,
            "task_id": "TASK-1",
            "login_success": False,
        }

        stdout_buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            flow_state_path = Path(temp_dir) / "flow.json"
            with mock.patch.dict(os.environ, {"QXY_LOGIN_FLOW_STATE_PATH": str(flow_state_path)}, clear=False):
                with mock.patch.object(login_workflow_cli.TaxLoginClient, "from_config", return_value=fake_client):
                    with mock.patch.object(login_workflow_cli, "TaxLoginWorkflow", return_value=fake_workflow):
                        with mock.patch.object(
                            sys,
                            "argv",
                            [
                                "login_workflow.py",
                                "run-full-login",
                                "--area-code",
                                "3100",
                                "--phone",
                                "17633122441",
                                "--password",
                                "pwd",
                            ],
                        ):
                            with mock.patch("sys.stdout", stdout_buffer):
                                exit_code = login_workflow_cli.main()

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertFalse(payload["success"])
        self.assertFalse(payload["final_success"])
        self.assertTrue(payload["waiting_for_user_input"])
        self.assertEqual(payload["user_input_kind"], "natural_sms_code")
        fake_workflow.list_enterprises.assert_not_called()

    def test_run_full_login_can_finish_full_chain(self) -> None:
        """全链路命令应能从头跑到企业登录完成。"""
        fake_client = mock.create_autospec(TaxLoginClient, instance=True)
        fake_workflow = mock.create_autospec(TaxLoginWorkflow, instance=True)
        fake_workflow.create_natural_person_account.return_value = {
            "success": True,
            "account_id": "NAT-1",
            "login_mode": 17,
        }
        fake_workflow.start_natural_person_login.return_value = {
            "success": True,
            "need_verify": False,
            "login_success": True,
            "account_id": "NAT-1",
        }
        fake_workflow.list_enterprises.return_value = {
            "success": True,
            "total": 1,
            "enterprises": [{"name": "企业A", "org_name": "企业A", "nsrsbh": "9131", "identity_type": "BSY", "index": "0"}],
        }
        fake_workflow.choose_target_enterprise.return_value = {
            "name": "企业A",
            "org_name": "企业A",
            "nsrsbh": "9131",
            "identity_type": "BSY",
            "index": "0",
        }
        fake_workflow.subscribe_enterprise_service.return_value = {
            "success": True,
            "agg_org_id": "ORG-1",
            "org_id": "ORG-1",
        }
        fake_workflow.create_multi_account.return_value = {
            "success": True,
            "account_id": "ENT-1",
            "agg_org_id": "ORG-1",
            "login_mode": 14,
        }
        fake_workflow.start_enterprise_login.return_value = {
            "success": True,
            "need_verify": False,
            "login_success": True,
            "login_state": {"ready": True, "aggOrgId": "ORG-1", "accountId": "ENT-1"},
        }

        stdout_buffer = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            flow_state_path = Path(temp_dir) / "flow.json"
            login_state_path = Path(temp_dir) / "login.json"
            with mock.patch.dict(
                os.environ,
                {
                    "QXY_LOGIN_FLOW_STATE_PATH": str(flow_state_path),
                    "QXY_LOGIN_STATE_PATH": str(login_state_path),
                },
                clear=False,
            ):
                with mock.patch.object(login_workflow_cli.TaxLoginClient, "from_config", return_value=fake_client):
                    with mock.patch.object(login_workflow_cli, "TaxLoginWorkflow", return_value=fake_workflow):
                        with mock.patch.object(
                            sys,
                            "argv",
                            [
                                "login_workflow.py",
                                "run-full-login",
                                "--area-code",
                                "3100",
                                "--phone",
                                "17633122441",
                                "--password",
                                "pwd",
                            ],
                        ):
                            with mock.patch("sys.stdout", stdout_buffer):
                                exit_code = login_workflow_cli.main()

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout_buffer.getvalue())
        self.assertTrue(payload["success"])
        self.assertTrue(payload["final_success"])
        self.assertEqual(payload["flow_status"], "ENTERPRISE_AUTHENTICATED")
        self.assertEqual(payload["agg_org_id"], "ORG-1")
        self.assertEqual(payload["account_id"], "ENT-1")


class TaxLoginClientApiTest(unittest.TestCase):
    """覆盖关键请求体拼装。"""

    def setUp(self) -> None:
        self.client = TaxLoginClient(app_key="10003110", app_secret="secret")

    @mock.patch.object(TaxLoginClient, "_send_request")
    @mock.patch.object(TaxLoginClient, "_encrypt_password", return_value="encrypted")
    def test_create_account_record_omits_agg_org_id_when_none(self, mock_encrypt: mock.Mock, mock_send_request: mock.Mock) -> None:
        """账号创建在 aggOrgId 为空时不应透传该字段。"""
        self.client.create_account_record(
            agg_org_id=None,
            dq="31",
            username="17633122441",
            phone="17633122441",
            password="pwd",
            identity_type="BSY",
            login_mode=17,
        )

        path, body = mock_send_request.call_args.args
        self.assertEqual(path, "/v2/public/account/create")
        self.assertNotIn("aggOrgId", body)
        self.assertEqual(body["dlfs"], 17)
        mock_encrypt.assert_called_once_with("pwd")

    @mock.patch.object(TaxLoginClient, "_send_request")
    def test_send_etax_login_sms_can_omit_agg_org_id(self, mock_send_request: mock.Mock) -> None:
        """发码接口在自然人链路应支持只传 accountId。"""
        self.client.send_etax_login_sms("1001")

        path, body = mock_send_request.call_args.args
        self.assertEqual(path, "/v2/public/login/remote/etaxcookie")
        self.assertEqual(body, {"accountId": "1001"})

    @mock.patch.object(TaxLoginClient, "_send_request")
    def test_query_nature_org_list_only_uses_account_id(self, mock_send_request: mock.Mock) -> None:
        """自然人企业列表接口只传 accountId。"""
        self.client.query_nature_org_list("1001")

        path, body = mock_send_request.call_args.args
        self.assertEqual(path, "/v2/public/login/queryOrglist")
        self.assertEqual(body, {"accountId": "1001"})

    def test_build_signature_uses_md5_base64_format(self) -> None:
        """签名算法应与服务端规则一致。"""
        signature = build_signature(
            method="POST",
            path="/v2/public/login/remote/etaxcookie",
            content_md5="content-md5",
            req_date="1712489752000",
            access_token="access-token",
            app_secret="secret",
            app_key="10003110",
        )
        self.assertEqual(signature, "API-SV1:10003110:NDIwZjQxOTVlMDRlODExZTIwYTNhMDAzMWVjMTE5MTI=")


if __name__ == "__main__":
    unittest.main()
