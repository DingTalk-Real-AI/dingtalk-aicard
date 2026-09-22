#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Use shared reference preflight cases for Python and Go without changing schema rules."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/dingtalk-aicard/scripts'))
import aicard_lint as lint

# Case names are English; localized card text intentionally exercises Unicode payloads.
FIXTURES = ROOT / 'dws-aicard/internal/card/a2ui/testdata/preflight-references.json'


class ReferencePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = lint.Protocol(lint.PROTOCOL_DIR)
        cls.cases = json.loads(FIXTURES.read_text(encoding='utf-8'))

    def test_shared_cases(self):
        for case in self.cases:
            with self.subTest(name=case['name']):
                original = copy.deepcopy(case['messages'])
                self.assertEqual([], lint.lint(original, self.protocol)[0])
                result = lint.preflight(original, self.protocol, case['mode'])
                self.assertEqual(case['valid'], result['valid'], result)
                project = lambda ds: sorted((d['code'], d['severity'], d['pointer']) for d in ds)
                self.assertEqual(project(case['diagnostics']), project(result['diagnostics']))
                self.assertEqual(case['messages'], original)
                self.assertFalse(result['renderingVerified'])

    def test_cli_warning_succeeds_and_reference_error_keeps_schema_valid(self):
        for name, code in [('Unreachable component is a warning', 0), ('Missing static child reference', 1)]:
            case = next(c for c in self.cases if c['name'] == name)
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as temp:
                path = Path(temp) / 'card.json'
                path.write_text(json.dumps(case['messages']), encoding='utf-8')
                proc = subprocess.run([sys.executable, '-B', str(ROOT / 'skills/dingtalk-aicard/scripts/aicard_lint.py'),
                                       str(path), '--preflight', 'new-card', '--format', 'json'],
                                      capture_output=True, encoding='utf-8', timeout=30)
                self.assertEqual(code, proc.returncode, proc.stderr)
                result = json.loads(proc.stdout)
                self.assertTrue(result['valid'])
                self.assertEqual(code == 0, result['preflight']['valid'])

    def test_deep_static_graph_uses_iterative_traversal(self):
        messages = copy.deepcopy(self.cases[0]['messages'])
        nodes = [{'id': 'root' if i == 0 else str(i), 'component': 'Column',
                  'children': [str(i + 1)]} for i in range(1500)]
        nodes.append({'id': '1500', 'component': 'Text', 'text': 'Last node'})
        messages[-1]['updateComponents']['components'] = nodes
        self.assertEqual([], lint.preflight(messages, self.protocol)['diagnostics'])


if __name__ == '__main__':
    unittest.main()
