#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Test setup caching, recovery, and process boundaries without network installs."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
# Localized paths and error strings exercise Unicode-safe environment recovery.
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/dingtalk-aicard/scripts/setup_env.py'
spec = importlib.util.spec_from_file_location('setup_env', SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path.cwd() / '.analysis-artifacts/test-setup-env'
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.target = self.work / "含 空格 ' $() 环境"
        self.env = dict(os.environ, TMPDIR=str(self.work), TMP=str(self.work), TEMP=str(self.work),
                        PYTHONDONTWRITEBYTECODE='1')

    def cached(self, digest=None):
        self.target.mkdir()
        state = {'owner': setup.OWNER, 'basePython': sys.executable,
                 'requirementsSha256': digest or hashlib.sha256(setup.REQUIREMENTS.read_bytes()).hexdigest()}
        (self.target / setup.MARKER).write_text(json.dumps(state), encoding='utf-8')

    def test_valid_cache_never_discovers_python_or_installs(self):
        self.cached()
        with patch.object(setup, 'self_check', return_value=True), \
                patch.object(setup, 'choose_python', side_effect=AssertionError('不应重新选择解释器')), \
                patch.object(setup, 'run_step', side_effect=AssertionError('不应再次安装')):
            result = setup.prepare(self.target)
        self.assertTrue(result['ready'])
        self.assertTrue(result['reused'])
        self.assertEqual(str(setup.env_python(self.target)), result['pythonExecutable'])

    def test_changed_requirements_install_in_same_environment(self):
        self.cached('旧摘要')
        def install(command, env, log, *args):
            self.assertEqual(str(setup.env_python(self.target)), command[0])
            self.assertIn('--require-virtualenv', command)
            self.assertEqual(str(setup.REQUIREMENTS), command[-1])
            self.assertEqual(self.work, Path(env['TMPDIR']).parent)
            self.assertEqual(['-X', 'utf8'], command[command.index('-X'):command.index('-X') + 2])
            self.assertEqual('utf-8', env['PYTHONIOENCODING'])
            self.assertEqual('1', env['PYTHONUTF8'])
        with patch.object(setup, 'probe', return_value=({'executable': sys.executable}, None)), \
                patch.object(setup, 'self_check', return_value=True), \
                patch.object(setup, 'choose_python', side_effect=AssertionError('不需重建')), \
                patch.object(setup, 'run_step', side_effect=install) as step:
            result = setup.prepare(self.target)
        self.assertFalse(result['reused'])
        self.assertEqual(1, step.call_count)
        state = json.loads((self.target / setup.MARKER).read_text())
        self.assertEqual(hashlib.sha256(setup.REQUIREMENTS.read_bytes()).hexdigest(), state['requirementsSha256'])

    def test_unknown_directory_is_preserved(self):
        self.target.mkdir()
        original = self.target / '用户文件.txt'
        original.write_text('保留', encoding='utf-8')
        for marker in (None, '[]', '{"owner":"其他工具"}'):
            if marker is not None:
                (self.target / setup.MARKER).write_text(marker)
            with self.subTest(marker=marker), self.assertRaises(setup.SetupError) as error:
                setup.prepare(self.target)
            self.assertEqual('setup.unmanaged_directory', error.exception.code)
            self.assertEqual('保留', original.read_text(encoding='utf-8'))

    def test_install_failure_keeps_environment_retryable(self):
        self.cached('旧摘要')
        with patch.object(setup, 'probe', return_value=({'executable': sys.executable}, None)), \
                patch.object(setup, 'self_check', return_value=True), \
                patch.object(setup, 'run_step', side_effect=setup.SetupError('setup.install_failed', '模拟安装失败')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, setup.main(['--venv', str(self.target)]))
        report = json.loads(output.getvalue())
        self.assertFalse(report['ready'])
        self.assertEqual('setup.install_failed', report['error']['code'])
        self.assertEqual('旧摘要', json.loads((self.target / setup.MARKER).read_text())['requirementsSha256'])
        with patch.object(setup, 'probe', return_value=({'executable': sys.executable}, None)), \
                patch.object(setup, 'self_check', return_value=True), patch.object(setup, 'run_step'):
            self.assertTrue(setup.prepare(self.target)['ready'])

    def test_protocol_failure_does_not_reinstall_dependencies(self):
        self.cached()
        failure = subprocess.CompletedProcess([], 2, json.dumps({'diagnostics': [
            {'code': 'input.protocol_unavailable', 'message': '缺少 catalog'}]}), '')
        with patch.object(setup.subprocess, 'run', return_value=failure), \
                patch.object(setup, 'run_step', side_effect=AssertionError('协议坏了不能重装依赖')), \
                self.assertRaises(setup.SetupError) as error:
            setup.prepare(self.target)
        self.assertEqual('setup.protocol_unavailable', error.exception.code)

    def test_broken_interpreter_is_skipped_and_explicit_choice_is_honored(self):
        with patch.object(setup, 'candidates', return_value=['broken', sys.executable]), \
                patch.object(setup, 'probe', side_effect=[(None, 'pyexpat 动态库异常'),
                                                       ({'executable': sys.executable}, None)]):
            base, skipped = setup.choose_python(None, self.env, self.target)
        self.assertEqual(sys.executable, base['executable'])
        self.assertEqual('broken', skipped[0]['python'])
        explicit = self.work / '不存在的 Python'
        with self.assertRaises(setup.SetupError) as error:
            setup.choose_python(str(explicit), self.env, self.target)
        self.assertEqual([str(explicit)], [r['python'] for r in error.exception.details['checked']])

    def test_self_check_timeout_does_not_reinstall(self):
        self.cached()
        with patch.object(setup.subprocess, 'run', side_effect=subprocess.TimeoutExpired('自检', 60)), \
                patch.object(setup, 'run_step', side_effect=AssertionError('超时不能被当成缺依赖')), \
                self.assertRaises(setup.SetupError) as error:
            setup.prepare(self.target)
        self.assertEqual('setup.self_check_timeout', error.exception.code)

    def test_probe_runs_real_healthy_python(self):
        if sys.version_info < (3, 10):
            self.skipTest('The test interpreter is below the supported Python 3.10 floor')
        try:
            import ssl, venv, ensurepip, pyexpat  # noqa: F401 - setup prerequisites
        except (ImportError, OSError) as error:
            self.skipTest(f'The test interpreter lacks a setup prerequisite: {error}')
        data, reason = setup.probe(sys.executable, self.env)
        self.assertIsNone(reason)
        self.assertEqual(sys.executable, data['executable'])
        self.assertGreaterEqual(tuple(data['version']), (3, 10))

    def test_missing_ensurepip_gets_install_guidance(self):
        with patch.object(setup, 'candidates', return_value=['/usr/bin/python3']), \
                patch.object(setup, 'probe', return_value=(None, 'Missing standard library module: ensurepip')), \
                self.assertRaises(setup.SetupError) as error:
            setup.choose_python(None, self.env, self.target)
        self.assertEqual('setup.python_unavailable', error.exception.code)
        self.assertIn('python3-venv', error.exception.details['hint'])
        self.assertEqual('/usr/bin/python3', error.exception.details['checked'][0]['python'])

    def test_probe_identifies_missing_bootstrap_module(self):
        if sys.version_info < (3, 10):
            self.skipTest('The test interpreter is below the supported Python 3.10 floor')
        simulated = setup.PROBE.replace('import ssl, venv, ensurepip, pyexpat',
                                        "raise ModuleNotFoundError(name='ensurepip')")
        with patch.object(setup, 'PROBE', simulated):
            data, reason = setup.probe(sys.executable, self.env)
        self.assertIsNone(data)
        self.assertEqual('Missing standard library module: ensurepip', reason)

    def test_subprocess_unicode_survives_legacy_parent_encoding(self):
        env = dict(self.env, PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
        code = 'import json; print(json.dumps({"executable": "中文 ⚠ 路径"}, ensure_ascii=False))'
        with patch.object(setup, 'PROBE', code):
            data, reason = setup.probe(sys.executable, env)
        self.assertIsNone(reason)
        self.assertEqual('中文 ⚠ 路径', data['executable'])
        self.assertTrue(setup.self_check(sys.executable, env))
        log = self.work / '编码日志.txt'
        command = [sys.executable, '-I', '-X', 'utf8', '-B', '-c',
                   'import sys; print("中文 ⚠"); sys.stderr.buffer.write(bytes([255]))']
        with contextlib.redirect_stderr(io.StringIO()):
            setup.run_step(command, env, log, 'setup.install_failed', '编码测试', 10)
        self.assertIn('中文 ⚠', log.read_text(encoding='utf-8'))
        self.assertIn(r'\xff', log.read_text(encoding='utf-8'))

    def test_self_check_uses_bundled_protocol_despite_external_overrides(self):
        env = dict(self.env, AICARD_PROTOCOL_DIR=str(self.work / '外部协议'),
                   AICARD_VALIDATION_RULES=str(self.work / '外部规则.json'))
        self.assertTrue(setup.self_check(sys.executable, env))

    def test_process_lock_prevents_parallel_install_and_releases(self):
        with setup.setup_lock(self.target):
            result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--venv', str(self.target)],
                                    capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual(2, result.returncode)
        self.assertEqual('setup.in_progress', json.loads(result.stdout)['error']['code'])
        with setup.setup_lock(self.target):
            pass
        self.assertFalse(self.target.exists())

    def test_run_step_preserves_arguments_and_reports_failures(self):
        output = self.work / "中文 ' $() 文件.txt"
        log = self.work / '安装日志.txt'
        command = [sys.executable, '-B', '-c',
                   'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2], encoding="utf-8")',
                   str(output), '引号"与换行\n原样保留']
        with contextlib.redirect_stderr(io.StringIO()):
            setup.run_step(command, self.env, log, 'setup.install_failed', '测试命令', 10)
        self.assertEqual(command[-1], output.read_text(encoding='utf-8'))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(setup.SetupError) as error:
            setup.run_step([sys.executable, '-B', '-c', 'import sys; print("失败原因"); sys.exit(9)'],
                           self.env, log, 'setup.install_failed', '失败命令', 10)
        self.assertEqual('setup.install_failed', error.exception.code)
        self.assertIn('失败原因', log.read_text(encoding='utf-8'))

    def test_rebuilding_never_uses_python_inside_target(self):
        with patch.object(setup, 'candidates', return_value=[str(setup.env_python(self.target))]), \
                patch.object(setup, 'probe', side_effect=AssertionError('不能探测待删除的解释器')), \
                self.assertRaises(setup.SetupError) as error:
            setup.choose_python(None, self.env, self.target)
        self.assertEqual('setup.python_unavailable', error.exception.code)

    def test_prohibited_temporary_directories_are_rejected_before_writing(self):
        for path in ('/private/tmp/aicard-test', '/tmp/aicard-test', '/'):
            with self.subTest(path=path), patch.object(setup, 'setup_lock', side_effect=AssertionError('禁止写入')), \
                    self.assertRaises(setup.SetupError) as error:
                setup.prepare(path)
            self.assertEqual('setup.invalid_directory', error.exception.code)


if __name__ == '__main__':
    unittest.main()
