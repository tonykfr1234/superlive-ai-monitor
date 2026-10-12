"""Publication checks use only synthetic secrets inside temporary directories."""
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCANNER = Path(__file__).resolve().parents[1] / 'scripts' / 'security_scan.py'
SPEC = importlib.util.spec_from_file_location('public_security_scan', SCANNER)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


def synthetic_token():
    return '123456789' + ':' + 'A' * 35


class PublicationSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
        return path

    def test_empty_and_placeholder_configuration_examples_are_allowed(self):
        self.write('config/telegram.env.example', 'BOT_TOKEN=\nCHAT_ID=\n')
        self.write('.env.example', 'BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN\nCHAT_ID=YOUR_NUMERIC_CHAT_ID\n')
        self.assertEqual(scanner.scan(self.root), [])

    def test_example_word_cannot_exempt_a_numeric_chat_id_anywhere_in_file(self):
        self.write('README.md', '# example and placeholder text\nCHAT_ID=' + str(123456789) + '\n')
        self.assertTrue(any('Numeric Telegram chat id' in item for item in scanner.scan(self.root)))

    def test_numeric_chat_ids_in_json_quoted_assignments_and_powershell_are_rejected(self):
        values = (json.dumps({'chat_id': -int('100123456789')}),
                  'CHAT_ID="' + str(123456789) + '"\n',
                  '$Chat_ID = ' + str(123456789) + ' # configured\n')
        for content in values:
            with self.subTest(content_type=content[:8]):
                self.write('settings.txt', content)
                self.assertTrue(any('Numeric Telegram chat id' in item for item in scanner.scan(self.root)))

    def test_secrets_are_rejected_inside_a_safe_example_filename(self):
        self.write('config/telegram.env.example', 'BOT_TOKEN=' + synthetic_token())
        self.assertTrue(any('Telegram bot token' in item for item in scanner.scan(self.root)))

    def test_private_names_are_case_insensitive_and_cover_local_variants(self):
        for name in ('Telegram.Env', '.ENV', '.Env.Local', 'Telegram_Profile.json',
                     'TELEGRAM_PROFILE.backup', 'credentials.private.json', 'SECRETS.json'):
            with self.subTest(name=name):
                path = self.write(name, '')
                self.assertTrue(any('FORBIDDEN FILE' in item for item in scanner.scan(self.root)))
                path.unlink()

    def test_private_runtime_directories_are_case_insensitive(self):
        self.write('RunTime/status.json', '{}')
        self.write('EVENTS/evidence.txt', '')
        self.write('Backups/config.txt', '')
        self.assertEqual(sum('PRIVATE RUNTIME PATH' in item for item in scanner.scan(self.root)), 3)

    def test_large_nonmedia_file_fails_instead_of_being_silently_skipped(self):
        self.write('large.txt', b'x' * (scanner.MAX_TEXT_BYTES + 1))
        self.assertTrue(any('OVERSIZED UNSCANNED FILE' in item for item in scanner.scan(self.root)))

    def test_only_exact_demo_media_paths_are_approved(self):
        headers = {'png': b'\x89PNG\r\n\x1a\n', 'gif': b'GIF89a', 'mp4': b'\0\0\0\x18ftypmp42'}
        for name, kind in scanner.DEMO_MEDIA.items():
            self.write(name, headers[kind] + b'synthetic demo')
        self.assertEqual(scanner.scan(self.root), [])
        self.write('docs/media/private.mp4', headers['mp4'])
        self.assertTrue(any('UNAPPROVED ARTIFACT' in item for item in scanner.scan(self.root)))

    def test_allowlisted_media_must_have_expected_signature(self):
        self.write('docs/media/polygon-selection.png', 'ordinary source text')
        self.assertTrue(any('INVALID DEMO MEDIA' in item for item in scanner.scan(self.root)))

    def test_allowlisted_media_does_not_exempt_embedded_credential_text(self):
        self.write('docs/media/polygon-selection.png', b'\x89PNG\r\n\x1a\n' + synthetic_token().encode())
        self.assertTrue(any('Telegram bot token' in item for item in scanner.scan(self.root)))

    def test_utf16_and_utf32_secrets_are_checked(self):
        for encoding in ('utf-16', 'utf-32'):
            with self.subTest(encoding=encoding):
                self.write('settings.txt', ('BOT_TOKEN=' + synthetic_token()).encode(encoding))
                self.assertTrue(any('Telegram bot token' in item for item in scanner.scan(self.root)))

    @unittest.skipUnless(shutil.which('git'), 'Git required to verify index-aware exclusions')
    def test_skipped_local_directories_cannot_hide_tracked_credentials(self):
        subprocess.run(['git', 'init', '--quiet', str(self.root)], capture_output=True, check=True)
        self.write('.vscode/note.txt', synthetic_token())
        self.write('.venv/runtime/status.json', '{}')
        subprocess.run(['git', '-C', str(self.root), 'add', '--force', '.vscode/note.txt',
                        '.venv/runtime/status.json'], capture_output=True, check=True)
        findings = scanner.scan(self.root)
        self.assertTrue(any('Telegram bot token' in item for item in findings))
        self.assertTrue(any('PRIVATE RUNTIME PATH' in item for item in findings))

    def test_untracked_local_cache_does_not_scan_installed_dependencies(self):
        self.write('.venv/installed.py', 'dependency source')
        self.write('__pycache__/module.pyc', b'compiled cache')
        self.assertEqual(scanner.scan(self.root), [])

    def test_unreadable_files_fail_closed(self):
        self.write('settings.txt', 'safe')
        with patch.object(Path, 'read_bytes', side_effect=PermissionError):
            self.assertTrue(any('UNREADABLE FILE' in item for item in scanner.scan(self.root)))

    def test_findings_never_print_matched_secret_values(self):
        self.write('settings.txt', synthetic_token())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = scanner.main([str(self.root)])
        self.assertEqual(result, 1)
        self.assertIn('settings.txt:1', output.getvalue())
        self.assertNotIn(synthetic_token(), output.getvalue())


if __name__ == '__main__':
    unittest.main()
