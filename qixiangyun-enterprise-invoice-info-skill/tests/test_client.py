"""企业开票信息查询 Skill - 单元测试"""

import json
import pytest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from client import EnterpriseInvoiceInfoClient
from config import Config, ConfigError
from exceptions import EnterpriseInvoiceInfoError


class TestConfig:
    def test_validate_missing_keys(self):
        config = Config()
        with pytest.raises(ConfigError):
            config.validate()

    def test_is_configured_false(self):
        config = Config()
        assert config.is_configured is False

    def test_load_from_env(self):
        with patch.dict("os.environ", {
            "QXY_CLIENT_APPKEY": "test_key",
            "QXY_CLIENT_SECRET": "test_secret",
        }):
            config = Config()
            config.load()
            assert config.client_appkey == "test_key"
            assert config.client_secret == "test_secret"
            assert config.is_configured is True


class TestEnterpriseInvoiceInfoClient:
    def setup_method(self):
        self.client = EnterpriseInvoiceInfoClient(
            client_appkey="test_key",
            client_secret="test_secret",
        )

    @patch("scripts.client.requests.post")
    def test_query_by_name(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps({
                        "code": "2000",
                        "data": {"enterpriseName": "企享云科技有限公司", "taxId": "91310000XXX"}
                    })}
                ]
            }
        }
        mock_post.return_value = mock_response

        result = self.client.query_enterprise_info(enterprise_name="企享云")
        assert result["code"] == "2000"

    @patch("scripts.client.requests.post")
    def test_query_by_credit_code(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps({
                        "code": "2000",
                        "data": {"enterpriseName": "企享云科技有限公司"}
                    })}
                ]
            }
        }
        mock_post.return_value = mock_response

        result = self.client.query_enterprise_info(credit_code="91310000MA1FLXXX0X")
        assert result["code"] == "2000"

    def test_query_missing_param(self):
        with pytest.raises(EnterpriseInvoiceInfoError):
            self.client.query_enterprise_info()
