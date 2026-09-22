#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Check standalone Skill portability, line endings, and pipe encoding offline."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CLIPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.skill = self.work / '独立 Skill 含空格' / 'dingtalk-aicard'
        shutil.copytree(ROOT / 'skills/dingtalk-aicard', self.skill,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.valid = self.work / '中文 卡片.json'
        self.valid.write_text(json.dumps([{'version': 'v1.0', 'updateComponents': {
            'surfaceId': 's', 'components': [{'id': 'root', 'component': 'Text', 'text': '中文 ⚠ 😀'}]}}]),
            encoding='utf-8')
        self.invalid = self.work / '错误 卡片.json'
        self.invalid.write_text('{', encoding='utf-8')

    def cli(self, *args, encoding='cp1252', script='aicard_lint.py'):
        env = dict(os.environ, PYTHONIOENCODING=encoding, PYTHONUTF8='0', PYTHONDONTWRITEBYTECODE='1',
                   TMPDIR=str(self.work), TMP=str(self.work), TEMP=str(self.work))
        env.pop('AICARD_PROTOCOL_DIR', None)
        env.pop('AICARD_VALIDATION_RULES', None)
        return subprocess.run([sys.executable, '-B', str(self.skill / 'scripts' / script), *args],
                              cwd=self.work, env=env, capture_output=True, timeout=30)

    def test_json_modes_are_ascii_and_keep_decoded_results(self):
        cases = [(['--self-check'], 0), (['--explain', 'Text'], 0),
                 (['--explain-many', 'Text', 'Row', '--compact'], 0),
                 ([str(self.valid)], 0), ([str(self.valid), '--preflight', 'resources'], 0),
                 ([str(self.invalid)], 2)]
        for args, expected in cases:
            baseline = None
            for encoding in ('cp1252', 'gbk', 'utf-8'):
                with self.subTest(args=args, encoding=encoding):
                    proc = self.cli(*args, '--format', 'json', encoding=encoding)
                    self.assertEqual(expected, proc.returncode, proc.stderr)
                    self.assertTrue(proc.stdout.isascii(), proc.stdout)
                    result = json.loads(proc.stdout)
                    if baseline is None:
                        baseline = result
                    else:
                        self.assertEqual(baseline, result)

    def test_text_output_help_and_errors_are_utf8(self):
        for encoding in ('cp1252', 'gbk'):
            for args, code, needle in ((['--explain', 'Text'], 0, 'Common components'),
                                       (['--help'], 0, 'Offline A2UI card validator'),
                                       ([str(self.invalid)], 2, 'Cannot read or parse')):
                with self.subTest(args=args, encoding=encoding):
                    proc = self.cli(*args, encoding=encoding)
                    self.assertEqual(code, proc.returncode, proc.stderr)
                    self.assertIn(needle, (proc.stdout + proc.stderr).decode('utf-8'))

    def test_reformatted_table_works_and_modified_table_fails_in_real_cli(self):
        table = self.skill / 'scripts/unicode-xid.json'
        raw = table.read_bytes()
        for content in (raw.replace(b'\n', b'\r\n'), b'\xef\xbb\xbf' + raw,
                        json.dumps(json.loads(raw), sort_keys=True, indent=4).encode('utf-8')):
            table.write_bytes(content)
            proc = self.cli('--self-check', '--format', 'json')
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertTrue(json.loads(proc.stdout)['ok'])
        changed = json.loads(raw)
        changed['classes']['XID_Start'] += '0'
        table.write_text(json.dumps(changed), encoding='utf-8')
        proc = self.cli('--self-check', '--format', 'json')
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertTrue(proc.stdout.isascii())
        diagnostic = json.loads(proc.stdout)['diagnostics'][0]
        self.assertEqual('input.protocol_unavailable', diagnostic['code'])
        self.assertIn('Character-table digest mismatch', diagnostic['message'])

    def test_setup_error_json_is_safe_for_legacy_code_pages(self):
        target = self.work / '用户已有环境 ⚠'
        target.mkdir()
        for encoding in ('cp1252', 'gbk', 'utf-8'):
            proc = self.cli('--venv', str(target), encoding=encoding, script='setup_env.py')
            self.assertEqual(2, proc.returncode, proc.stderr)
            self.assertTrue(proc.stdout.isascii())
            self.assertEqual('setup.unmanaged_directory', json.loads(proc.stdout)['error']['code'])


if __name__ == '__main__':
    unittest.main()
