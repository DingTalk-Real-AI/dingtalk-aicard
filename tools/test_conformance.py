#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Check that conformance catches regressions, mismatches, gaps, and command failures."""
import contextlib
import io
from unittest.mock import patch
import copy
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
import conformance

ROOT = Path(__file__).resolve().parents[1]
ERROR = {'code': 'schema.unknown_property', 'severity': 'error', 'pointer': '/bogus', 'message': 'Unknown property'}


class ConformanceTests(unittest.TestCase):
    def expectation(self):
        return {'valid': False, 'diagnostics': [{k: ERROR[k] for k in ('code', 'severity', 'pointer')}]}

    def test_expected_error_requires_valid_false_severity_code_and_pointer(self):
        result = {'valid': False, 'diagnostics': [dict(ERROR)]}
        self.assertIsNone(conformance.check_expectation(result, self.expectation()))
        for key, value in (('code', 'different'), ('severity', 'warning'), ('pointer', '/wrong')):
            changed = copy.deepcopy(result)
            changed['diagnostics'][0][key] = value
            self.assertIsNotNone(conformance.check_expectation(changed, self.expectation()))
        changed = dict(result, valid=True)
        self.assertIsNotNone(conformance.check_expectation(changed, self.expectation()))

    def test_boundaries_accept_only_clean_structure_result(self):
        expected = {'valid': True, 'diagnostics': []}
        self.assertIsNone(conformance.check_expectation(expected, expected))
        for result in ({'valid': False, 'diagnostics': [ERROR]},
                       {'valid': True, 'diagnostics': [dict(ERROR, severity='warning')]},
                       {'_error': 'Execution failed'}):
            self.assertIsNotNone(conformance.check_expectation(result, expected))

    def test_repository_manifest_is_complete(self):
        self.assertEqual(39, len(conformance.fixture_cases(ROOT)))

    def test_missing_and_unregistered_inputs_fail(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as directory:
            base = Path(directory) / 'shared/fixtures'
            case = base / 'invalid/one/input.json'
            case.parent.mkdir(parents=True)
            case.write_text('[]')
            manifest = base / 'expectations.json'
            manifest.write_text(json.dumps({'invalid/one': self.expectation()}))
            self.assertEqual(1, len(conformance.fixture_cases(directory)))
            case.unlink()
            with self.assertRaises(ValueError):
                conformance.fixture_cases(directory)
            case.write_text('[]')
            extra = base / 'boundaries/extra/input.json'
            extra.parent.mkdir(parents=True)
            extra.write_text('[]')
            with self.assertRaises(ValueError):
                conformance.fixture_cases(directory)

    def test_manifest_rejects_empty_negative_and_warning_expectation(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as directory:
            base = Path(directory) / 'shared/fixtures'
            case = base / 'invalid/one/input.json'
            case.parent.mkdir(parents=True)
            case.write_text('[]')
            invalid = [
                {'valid': False, 'diagnostics': []},
                {'valid': False, 'diagnostics': [dict(ERROR, severity='warning')]},
                {'valid': True, 'diagnostics': [ERROR]},
                {'valid': False, 'diagnostics': [{'code': 'schema.invalid', 'severity': 'error'}]},
            ]
            for expected in invalid:
                (base / 'expectations.json').write_text(json.dumps({'invalid/one': expected}))
                with self.subTest(expected=expected), self.assertRaises(ValueError):
                    conformance.fixture_cases(directory)

    def run_result(self, result, code):
        source = 'import json,sys; print(json.dumps(' + repr(result) + ')); sys.exit(' + str(code) + ')'
        return conformance.run(shlex.join([sys.executable, '-c', source]), 'unused')

    def test_command_exit_and_error_severity_must_agree(self):
        for result, code in (({'valid': True, 'diagnostics': []}, 1),
                             ({'valid': True, 'diagnostics': [ERROR]}, 0),
                             ({'valid': False, 'diagnostics': [dict(ERROR, severity='warning')]}, 1),
                             ({'valid': False, 'diagnostics': []}, 1)):
            self.assertIn('_error', self.run_result(result, code))
        self.assertNotIn('_error', self.run_result({'valid': False, 'diagnostics': [ERROR]}, 1))
        self.assertNotIn('_error', self.run_result({'valid': True, 'diagnostics': []}, 0))

    def test_explain_must_answer_requested_name_and_fields(self):
        expected = {'kind': 'component', 'name': 'Tabs', 'fields': [{'name': 'tabs'}]}
        conformance.check_explanation(expected, expected)
        for bad in (dict(expected, name='Text'), dict(expected, fields=[]), dict(expected, kind='function')):
            with self.assertRaises(ValueError):
                conformance.check_explanation(bad, expected)

    def test_dws_failure_report_is_decoded_from_stderr(self):
        result = {'ok': False, 'error': {'details': {'valid': False, 'diagnostics': [ERROR]}}}
        source = 'import json,sys; print(json.dumps(' + repr(result) + '), file=sys.stderr); sys.exit(3)'
        command = shlex.join([sys.executable, '-c', source])
        self.assertNotIn('_error', conformance.run(command, 'unused'))

    def test_timeout_cannot_count_as_a_passing_case(self):
        command = shlex.join([sys.executable, '-c', 'import time; time.sleep(1)'])
        self.assertIn('_error', conformance.run(command, 'unused', timeout=0.05))

    def test_empty_output_cannot_pass_any_fixture_expectation(self):
        for code in (0, 1, 137):
            command = shlex.join([sys.executable, '-c', f'import os; os._exit({code})'])
            result = conformance.run(command, 'unused')
            with self.subTest(exit_code=code):
                self.assertTrue(result['_error'])
                self.assertEqual(code, result['exitCode'])
                for _, _, expected in conformance.fixture_cases(ROOT):
                    self.assertTrue(conformance.check_expectation(result, expected))

    @unittest.skipIf(os.name == 'nt', 'Signal exit codes apply only to POSIX')
    def test_signal_without_output_is_an_execution_failure(self):
        command = shlex.join([sys.executable, '-c',
                              'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'])
        result = conformance.run(command, 'unused')
        self.assertEqual(-15, result['exitCode'])
        self.assertTrue(conformance.check_expectation(result, self.expectation()))

    def test_empty_error_message_cannot_mean_success(self):
        self.assertTrue(conformance.check_expectation({'_error': ''}, self.expectation()))

    def test_release_check_rejects_absent_official_cases_before_commands(self):
        with patch.object(conformance, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                conformance.main(['--official', str(ROOT / 'missing-official-fixtures'), '--require-official'])
            self.assertEqual(2, error.exception.code)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
