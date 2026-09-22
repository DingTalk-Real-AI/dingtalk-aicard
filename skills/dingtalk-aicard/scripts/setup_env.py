#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Prepare the AICard validation environment once.

stdout returns ASCII-escaped JSON; stderr and logs use UTF-8.
The linter itself never installs dependencies.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
LINT = HERE / "aicard_lint.py"
MARKER = ".aicard-env.json"
OWNER = "dingtalk-aicard"
PROBE = """
import json, sys
assert sys.version_info >= (3, 10), 'Python 3.10+ is required'
try:
    import ssl, venv, ensurepip, pyexpat
except ModuleNotFoundError as error:
    print(f'Missing standard library module: {error.name}', file=sys.stderr)
    raise SystemExit(3)
print(json.dumps({'executable': sys.executable, 'version': list(sys.version_info[:3])}))
"""


class SetupError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def safe_directory(path):
    directory = Path(path).expanduser().resolve()
    if any(directory == p or p in directory.parents for p in (Path('/tmp'), Path('/private/tmp'))):
        raise SetupError('setup.invalid_directory', 'Place the environment in a writable task directory, not /tmp or /private/tmp')
    if directory == Path(directory.anchor):
        raise SetupError('setup.invalid_directory', 'The environment directory cannot be a filesystem root')
    return directory


def env_python(directory):
    return directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def executable_path(value):
    found = shutil.which(str(value))
    # Preserve the venv/bin/python symlink rather than resolving it to the base interpreter.
    return os.path.abspath(os.path.expanduser(found or str(value)))


def candidates(explicit=None):
    if explicit:
        return [executable_path(explicit)]
    paths = [sys.executable]
    paths.append(shutil.which('python3'))
    # Discover explicitly named versions on PATH; do not scan disks or cap the newest version.
    for entry in os.get_exec_path():
        try:
            names = [p for p in Path(entry or '.').iterdir()
                     if re.fullmatch(r'python3\.\d+(?:\.exe)?', p.name) and p.is_file()]
            names.sort(key=lambda p: int(re.search(r'3\.(\d+)', p.name).group(1)), reverse=True)
            paths.extend(str(p) for p in names)
        except OSError:
            continue
    paths += [shutil.which(name) for name in ('python', 'py')]
    return list(dict.fromkeys(executable_path(p) for p in paths if p))


def probe(python, env):
    try:
        result = subprocess.run([str(python), '-I', '-X', 'utf8', '-B', '-c', PROBE], env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='backslashreplace', timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, dict) and isinstance(data.get('executable'), str):
                return data, None
        reason = (result.stderr or result.stdout).strip()
        return None, reason[-1200:] or f'Interpreter exited with code {result.returncode}'
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return None, str(error)[:1200]


def choose_python(explicit, env, target):
    rejected = []
    for python in candidates(explicit):
        # Rebuilding the target environment must not remove its chosen base interpreter.
        if target in Path(python).parents:
            rejected.append({'python': python, 'reason': 'Choose a base Python outside the target environment'})
            continue
        data, reason = probe(python, env)
        if data is not None:
            return data, rejected
        rejected.append({'python': python, 'reason': reason})
    details = {'checked': rejected}
    if any('Missing standard library module: ensurepip' in item['reason']
           or 'Missing standard library module: venv' in item['reason'] for item in rejected):
        details['hint'] = ('On Debian/Ubuntu, install python3-venv (or the matching python3.x-venv package) '
                           'and retry; do not install into the system Python.')
    raise SetupError('setup.python_unavailable',
                     'No healthy Python 3.10+ found; choose one with --python or install Python first',
                     **details)


@contextmanager
def setup_lock(directory):
    # The OS releases the file lock on exit; no manual cleanup state is needed.
    lock = directory.with_name(directory.name + '.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        lock.close()
        raise SetupError('setup.in_progress', 'Another setup process is using this environment; wait for it to finish') from error
    try:
        yield
    finally:
        lock.close()


def self_check(python, env):
    try:
        result = subprocess.run([str(python), '-I', '-X', 'utf8', '-B', str(LINT), '--self-check', '--format', 'json',
                                 '--protocol-dir', str(HERE.parent / 'references/protocol'),
                                 '--validation-rules', str(HERE / 'a2ui-validation-rules.json')],
                                env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
        report = json.loads(result.stdout)
    except subprocess.TimeoutExpired as error:
        raise SetupError('setup.self_check_timeout',
                         'Environment self-check timed out and was stopped; inspect the process or system load before retrying') from error
    except (OSError, ValueError):
        return False
    if result.returncode == 0 and isinstance(report, dict) and report.get('ok') is True:
        return True
    diagnostics = report.get('diagnostics', []) if isinstance(report, dict) else []
    if any(d.get('code') == 'input.protocol_unavailable' for d in diagnostics):
        raise SetupError('setup.protocol_unavailable', 'Bundled protocol self-check failed; inspect protocol files rather than reinstalling dependencies',
                         diagnostics=diagnostics)
    return False


def run_step(command, env, log, code, message, timeout):
    print(message, file=sys.stderr, flush=True)
    try:
        # Child Python processes use UTF-8; escape non-UTF-8 errors from native tools in logs.
        result = subprocess.run(command, env=env, capture_output=True, text=True,
                                encoding='utf-8', errors='backslashreplace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        with log.open('a', encoding='utf-8') as handle:
            handle.write(f'{message}: {type(error).__name__}\n')
        raise SetupError(code, message + ' failed or timed out; inspect the log before retrying setup', logPath=str(log)) from error
    with log.open('a', encoding='utf-8') as handle:
        handle.write(message + '\n' + result.stdout + result.stderr + '\n')
    if result.returncode:
        raise SetupError(code, message + ' failed; inspect the log before retrying', logPath=str(log))


def prepare(directory, explicit=None):
    directory = safe_directory(directory)
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    directory.parent.mkdir(parents=True, exist_ok=True)
    with setup_lock(directory), tempfile.TemporaryDirectory(prefix='.aicard-setup-', dir=directory.parent) as temp:
        env = dict(os.environ, TMPDIR=temp, TMP=temp, TEMP=temp, PYTHONDONTWRITEBYTECODE='1',
                   PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1',
                   PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        python = env_python(directory)
        marker = directory / MARKER
        state = {}
        if directory.exists():
            try:
                state = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            if not isinstance(state, dict) or state.get('owner') != OWNER:
                raise SetupError('setup.unmanaged_directory',
                                 'The target directory is not managed by this script and will not be overwritten; use its Python directly or choose a new --venv directory')
        same_python = not explicit or executable_path(explicit) == state.get('basePython')
        if same_python and state.get('requirementsSha256') == digest and self_check(python, env):
            return {'ready': True, 'pythonExecutable': str(python), 'venvPath': str(directory), 'reused': True}

        log = directory.with_name(directory.name + '.setup.log')
        rejected = []
        healthy = probe(python, env)[0] if same_python and state else None
        if healthy is None:
            base, rejected = choose_python(explicit, env, directory)
            with log.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'selectedPython': base, 'skippedInterpreters': rejected}, ensure_ascii=False) + '\n')
            if directory.exists():
                shutil.rmtree(directory)  # Remove only the dedicated venv whose owner was verified above.
            directory.mkdir()
            state = {'owner': OWNER, 'basePython': base['executable'], 'version': base['version']}
            marker.write_text(json.dumps(state, ensure_ascii=False) + '\n', encoding='utf-8')
            run_step([base['executable'], '-I', '-X', 'utf8', '-B', '-m', 'venv', str(directory)], env, log,
                     'setup.venv_failed', 'Creating the dedicated AICard virtual environment', 120)

        # Install only into the dedicated venv; do not bypass PEP 668 on system Python.
        run_step([str(python), '-I', '-X', 'utf8', '-B', '-m', 'pip', '--require-virtualenv', 'install', '--no-user', '--no-cache-dir',
                  '--disable-pip-version-check', '-r', str(REQUIREMENTS)], env, log,
                 'setup.install_failed', 'Installing AICard validation dependencies', 300)
        if not self_check(python, env):
            raise SetupError('setup.environment_unavailable', 'The validator still cannot run after installation; inspect the environment log', logPath=str(log))
        state['requirementsSha256'] = digest
        marker.write_text(json.dumps(state, ensure_ascii=False) + '\n', encoding='utf-8')
        return {'ready': True, 'pythonExecutable': str(python), 'venvPath': str(directory),
                'reused': False, 'logPath': str(log), 'skippedInterpreters': [item['python'] for item in rejected]}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv', default=str(Path.cwd() / '.aicard-venv'), help='Dedicated environment directory; defaults to .aicard-venv in the task directory')
    parser.add_argument('--python', help='Base Python path or command; when omitted, probe interpreters on PATH')
    args = parser.parse_args(argv)
    try:
        report = prepare(args.venv, args.python)
    except SetupError as error:
        report = {'ready': False, 'error': {'code': error.code, 'message': str(error), **error.details}}
    except OSError as error:
        report = {'ready': False, 'error': {'code': 'setup.filesystem_error', 'message': str(error),
                                          'hint': 'Use a writable task directory, not a read-only Skill installation directory'}}
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['ready'] else 2


if __name__ == '__main__':
    sys.exit(main())
