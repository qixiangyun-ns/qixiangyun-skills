#!/usr/bin/env python3
"""Tax Login Skill 工作流单元测试。"""

from __future__ import annotations

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

from scripts import Config, TaxLoginClient, TaxLoginError, TaxLoginWorkflow  # noqa: E402


class TaxLoginWorkflowTest(unittest.TestCase):
    """覆盖 7 步工作流的核心编排逻辑。"""

    def setUp(self) -> None:
        self.client = mock.create_autospec(TaxLoginClient, instance=True)
        self.workflow = TaxLoginWorkflow(self.client)

    def test_create_natural_person_account_uses_proxy_login_mode(self) -> None:
        """自然人账号创建应默认使用代理业务登录模式。"""

        self.client.create_account_record.return_value = {
            "code": "2000",
            "success": True,
            "data": {
                "accountId": 5203935360402240,
                "aggOrgId": 7583454730897015,
                "sflx": "BSY",
                "dlfs": 15,
            },
        }

        result = self.workflow.create_natural_person_account(
            area_code="3300",
            phone="13800138000",
            password="password",
        )

        self.client.create_account_record.assert_called_once_with(
            agg_org_id=0,
            dq="33",
            username="13800138000",
            phone="13800138000",
            password="password",
            identity_type="BSY",
            login_mode=15,
        )
        self.assertEqual(result["account_id"], "5203935360402240")
        self.assertEqual(result["agg_org_id"], "7583454730897015")

    def test_list_enterprises_normalizes_response(self) -> None:
        """企业列表应被标准化成固定字段。"""

        self.client.query_nature_org_list.return_value = {
            "code": "2000",
            "success": True,
            "data": [
                {"name": "企业A", "nsrsbh": "913300001", "sflx": "BSY"},
                {"name": "企业B", "nsrsbh": "913300002", "sflx": "CWFZR"},
            ],
        }

        result = self.workflow.list_enterprises(
            natural_agg_org_id="7583454730897015",
            natural_account_id="5203935360402240",
        )

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["enterprises"][0]["name"], "企业A")
        self.assertEqual(result["enterprises"][1]["identity_type"], "CWFZR")

    def test_start_natural_person_login_by_phone_uses_app_login_flow(self) -> None:
        """手机号直登应允许在未知 accountId 时先发送验证码。"""

        self.client.login_flow_step1_send_sms.return_value = {
            "success": True,
            "need_verify": True,
            "task_id": "TASK-001",
            "message": "验证码已发送",
        }

        result = self.workflow.start_natural_person_login_by_phone(
            area_code="31",
            phone="13800138000",
            password="password",
        )

        self.client.login_flow_step1_send_sms.assert_called_once_with(
            area_code="3100",
            phone="13800138000",
            password="password",
        )
        self.assertTrue(result["need_verify"])
        self.assertEqual(result["task_id"], "TASK-001")

    def test_choose_target_enterprise_requires_unique_match(self) -> None:
        """企业选择遇到多条同名记录时应要求改用税号。"""

        enterprises = [
            {"name": "同名企业", "nsrsbh": "913300001", "identity_type": "BSY"},
            {"name": "同名企业", "nsrsbh": "913300002", "identity_type": "BSY"},
        ]

        with self.assertRaisesRegex(TaxLoginError, "匹配到多个企业"):
            self.workflow.choose_target_enterprise(enterprises, name="同名企业")

    def test_login_enterprise_account_prefers_cache(self) -> None:
        """企业账号登录就绪校验应优先复用缓存。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "login-state.json"
            self.client.check_cache.return_value = {
                "code": "2000",
                "success": True,
                "data": True,
            }

            with mock.patch.dict(
                os.environ,
                {"QXY_LOGIN_STATE_PATH": str(state_path)},
                clear=False,
            ):
                result = self.workflow.login_enterprise_account(
                    agg_org_id="7583454730897015",
                    account_id="5203935360402241",
                )

            self.assertTrue(result["ready"])
            self.assertEqual(result["source"], "cache")
            self.assertEqual(Path(result["state_file"]).resolve(), state_path.resolve())
            self.client.check_app_login.assert_not_called()

            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state_payload["aggOrgId"], "7583454730897015")
            self.assertEqual(state_payload["accountId"], "5203935360402241")
            self.assertEqual(state_payload["source"], "cache")

    def test_login_enterprise_account_returns_next_action_for_invalid_login_mode(self) -> None:
        """企业登录若遇到 14/15 限制，应返回结构化下一步建议。"""

        self.client.check_cache.return_value = {
            "code": "4000",
            "success": False,
            "message": "登录方式必须是14或者15",
        }

        result = self.workflow.login_enterprise_account(
            agg_org_id="7583454730897015",
            account_id="5203935360402241",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["nextAction"]["command"], "create-multi-account")
        self.assertEqual(result["nextAction"]["suggestedArgs"]["login_mode"], 15)


class TaxLoginConfigTest(unittest.TestCase):
    """覆盖凭证格式兼容逻辑。"""

    def test_config_supports_split_credentials(self) -> None:
        """配置读取应兼容 QXY_CLIENT_APPKEY 和 QXY_CLIENT_SECRET。"""

        with mock.patch.dict(
            os.environ,
            {
                "QXY_CLIENT_APPKEY": "10003110",
                "QXY_CLIENT_SECRET": "secret",
            },
            clear=True,
        ):
            config = Config().load()

        self.assertEqual(config.validate(), ("10003110", "secret"))


class TaxLoginClientApiTest(unittest.TestCase):
    """覆盖关键 endpoint 和请求体拼装。"""

    def setUp(self) -> None:
        self.client = TaxLoginClient(
            app_key="10003110",
            app_secret="secret",
        )

    @mock.patch.object(TaxLoginClient, "_send_request")
    @mock.patch.object(TaxLoginClient, "_encrypt_password", return_value="encrypted")
    def test_create_account_record_builds_new_api_payload(
        self,
        mock_encrypt: mock.Mock,
        mock_send_request: mock.Mock,
    ) -> None:
        """账号创建应走新登录业务的账号创建接口。"""

        self.client.create_account_record(
            agg_org_id=0,
            dq="33",
            username="13800138000",
            phone="13800138000",
            password="password",
            identity_type="BSY",
            login_mode=15,
        )

        path, body = mock_send_request.call_args.args
        self.assertEqual(path, "/v2/public/account/create")
        self.assertEqual(body["aggOrgId"], 0)
        self.assertEqual(body["dq"], "33")
        self.assertEqual(body["dlfs"], 15)
        self.assertEqual(body["gryhmm"], "encrypted")
        mock_encrypt.assert_called_once_with("password")

    @mock.patch.object(TaxLoginClient, "_send_request")
    def test_send_etax_login_sms_uses_new_endpoint(
        self,
        mock_send_request: mock.Mock,
    ) -> None:
        """发送短信验证码应走新登录业务的 etaxcookie 接口。"""

        self.client.send_etax_login_sms("7583454730897015", "5203935360402240")

        path, body = mock_send_request.call_args.args
        self.assertEqual(path, "/v2/public/login/remote/etaxcookie")
        self.assertEqual(
            body,
            {
                "aggOrgId": "7583454730897015",
                "accountId": "5203935360402240",
            },
        )


if __name__ == "__main__":
    unittest.main()
