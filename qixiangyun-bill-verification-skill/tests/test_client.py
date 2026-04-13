"""票据查验 Skill - 单元测试"""

import json
import pytest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from client import BillVerificationClient
from config import Config, ConfigError
from exceptions import BillVerificationError


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


class TestBillVerificationClient:
    def setup_method(self):
        self.client = BillVerificationClient(
            client_appkey="test_key",
            client_secret="test_secret",
        )

    @patch("scripts.client.requests.post")
    def test_verify_tax_control(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps({"code": "2000", "data": {"result": "valid"}})}
                ]
            }
        }
        mock_post.return_value = mock_response

        result = self.client.verify_tax_control_invoice([{"fpdm": "1234", "fphm": "5678"}])
        assert result["code"] == "2000"

    @patch("scripts.client.requests.post")
    def test_verify_digital(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps({"code": "2000", "data": {"result": "valid"}})}
                ]
            }
        }
        mock_post.return_value = mock_response

        result = self.client.verify_digital_invoice([{"fphm": "12345678"}])
        assert result["code"] == "2000"

    @patch("scripts.client.requests.post")
    def test_verify_invoice(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps({"code": "2000", "data": {"invoiceStatus": "有效"}})}
                ]
            }
        }
        mock_post.return_value = mock_response

        result = self.client.verify_invoice("1234", "5678", "2024-01-15", "100.00")
        assert result["code"] == "2000"

    def test_verify_tax_control_empty_list(self):
        with pytest.raises(BillVerificationError):
            self.client.verify_tax_control_invoice([])

    def test_batch_verify_over_limit(self):
        with pytest.raises(BillVerificationError):
            self.client.batch_verify_invoices([{"fpdm": "1"}] * 51)

    @patch("scripts.client.requests.post")
    def test_validate_invoice_info(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps({"code": "2000", "data": {"valid": True}})}
                ]
            }
        }
        mock_post.return_value = mock_response

        result = self.client.validate_invoice_info(invoice_type_code="1234", invoice_number="5678")
        assert result["code"] == "2000"
