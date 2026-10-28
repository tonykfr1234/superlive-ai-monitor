import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from setup_wizard import verify_telegram_token


class TokenVerificationTests(unittest.TestCase):
    token = '123:synthetic'

    def test_success_uses_read_only_get_with_verified_https(self):
        with patch('setup_wizard.requests.get', return_value=Mock(status_code=200, json=lambda: {'ok': True})) as get:
            self.assertIn('驗證成功', verify_telegram_token(self.token))
            get.assert_called_once_with('https://api.telegram.org/bot'+self.token+'/getMe', timeout=(5, 15))

    def test_transport_errors_do_not_claim_bad_credentials_or_leak_details(self):
        for error, label in [(requests.exceptions.SSLError, '憑證'), (requests.exceptions.Timeout, '逾時'), (requests.exceptions.ConnectionError, '網路')]:
            with patch('setup_wizard.requests.get', side_effect=error(self.token)):
                result = verify_telegram_token(self.token)
                self.assertIn(label, result)
                self.assertIn('尚無法判定', result)
                self.assertNotIn(self.token, result)

    def test_http_failure_categories(self):
        for code, label in [(401, '拒絕'), (404, '拒絕'), (429, '限制'), (500, '服務')]:
            with patch('setup_wizard.requests.get', return_value=Mock(status_code=code)):
                self.assertIn(label, verify_telegram_token(self.token))

    def test_malformed_and_unsuccessful_responses(self):
        with patch('setup_wizard.requests.get', return_value=Mock(status_code=200, json=Mock(side_effect=ValueError()))):
            self.assertIn('格式異常', verify_telegram_token(self.token))
        with patch('setup_wizard.requests.get', return_value=Mock(status_code=200, json=lambda: {'ok': False})):
            self.assertNotIn('驗證成功', verify_telegram_token(self.token))

    def test_invalid_input_never_contacts_telegram(self):
        with patch('setup_wizard.requests.get') as get:
            self.assertIn('格式不正確', verify_telegram_token(''))
            get.assert_not_called()
