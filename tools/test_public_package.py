#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Check distribution links and standalone installation without network, Profile, or sending."""
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class PublicPackageTests(unittest.TestCase):
    def test_execution_dependencies_match_each_distribution(self):
        standalone = ROOT / 'skills/dingtalk-aicard'
        native = ROOT / 'dws-aicard/skills/multi/dingtalk-aicard'
        source = (standalone / 'SKILL.md').read_text()
        target = (native / 'SKILL.md').read_text()
        for name in re.findall(r'scripts/([a-z_]+\.py)', source):
            self.assertTrue((standalone / 'scripts' / name).is_file())
        self.assertIn('scripts/aicard_lint.py', source)
        self.assertNotIn('dws chat message', source)
        self.assertNotIn('--biz-id', source)
        self.assertIn('dws chat message update-a2ui-card', target)
        self.assertIn('dws aicard lint', target)
        self.assertNotIn('scripts/', target)
        self.assertFalse((native / 'scripts').exists())
        for guidance in (source, target):
            self.assertNotIn('explain.json', guidance)
            self.assertNotIn('Schema validator', guidance)

    def test_standalone_documented_commands_and_wire_encoding_execute(self):
        skill = ROOT / 'skills/dingtalk-aicard'
        text = (skill / 'SKILL.md').read_text()
        messages = json.loads(re.search(r'```json\n(.*?)\n```', text, re.S).group(1))
        scratch = ROOT / '.analysis-artifacts/document-command-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            card = Path(directory) / 'card.a2ui.json'
            card.write_text(json.dumps(messages))
            script = skill / 'scripts/aicard_lint.py'
            help_result = subprocess.run([sys.executable, str(script), '--help'],
                                         capture_output=True, text=True, check=True)
            # Check prose flags as well as fenced commands; unsupported promises
            # such as an invented emit option must fail this test.
            for name in ('query', 'lint'):
                block = text.split(f'<!-- aicard:{name}:start -->')[1].split(f'<!-- aicard:{name}:end -->')[0]
                for flag in set(re.findall(r'--[a-z][a-z-]*', block)):
                    self.assertIn(flag, help_result.stdout)
                for command in re.findall(r'```bash\n(.*?)\n```', block, re.S):
                    for line in command.splitlines():
                        argv = shlex.split(line)
                        argv = [sys.executable if value == '<pythonExecutable>' else
                                str(script) if value.endswith('/scripts/aicard_lint.py') else
                                str(card) if value == 'card.a2ui.json' else value for value in argv]
                        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        json.loads(result.stdout)
            for mode in ('new-card', 'resources'):
                result = subprocess.run([sys.executable, str(script), str(card), '--preflight', mode, '--format', 'json'],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertTrue(report['valid'])
                self.assertTrue(report['preflight']['valid'])
            encoding = re.search(r'```python\n(.*?)\n```', text, re.S).group(1)
            result = subprocess.run([sys.executable, '-c', encoding], cwd=directory,
                                    capture_output=True, text=True, check=True, timeout=30)
            self.assertEqual([json.loads(item) for item in json.loads(result.stdout)], messages)

    def test_native_help_references_exist_in_distributed_skill(self):
        helper = (ROOT / 'dws-aicard/internal/helpers/aicard.go').read_text()
        references = re.findall(r'SkillDocumentation\("[^"]+", "dingtalk-aicard", "([^"]+)"\)', helper)
        self.assertTrue(references)
        for target in references:
            with self.subTest(target=target):
                self.assertTrue((ROOT / 'dws-aicard/skills/multi/dingtalk-aicard' / target).is_file())

    def test_public_and_installed_document_links_resolve(self):
        documents = [ROOT / 'README.md', ROOT / 'CONTRIBUTING.md']
        documents += list((ROOT / 'spec').rglob('*.md'))
        documents += list((ROOT / 'skills/dingtalk-aicard').rglob('*.md'))
        for doc in documents:
            for target in re.findall(r'\]\(([^\s)]+)\)', doc.read_text()):
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                path = (doc.parent / unquote(parsed.path)).resolve()
                with self.subTest(document=str(doc.relative_to(ROOT)), target=target):
                    self.assertTrue(path.exists(), str(path))
                    if path.suffix == '.json' and parsed.fragment.startswith('/'):
                        node = json.loads(path.read_text())
                        for part in parsed.fragment.lstrip('/').split('/'):
                            key = unquote(part).replace('~1', '/').replace('~0', '~')
                            node = node[int(key)] if isinstance(node, list) else node[key]

    def test_skill_works_outside_repository_with_bundled_protocol(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env.pop('AICARD_PROTOCOL_DIR', None)
        env.pop('AICARD_VALIDATION_RULES', None)
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT) as directory:
            destination = Path(directory) / 'installed/dingtalk-aicard'
            shutil.copytree(ROOT / 'skills/dingtalk-aicard', destination,
                            ignore=shutil.ignore_patterns('__pycache__'))
            script = destination / 'scripts/aicard_lint.py'
            for args in (['--self-check'], ['--explain', 'Action'],
                         [str(destination / 'references/protocol/examples/form-interaction.json')]):
                result = subprocess.run([sys.executable, '-B', str(script), *args, '--format', 'json'],
                                        cwd=directory, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                json.loads(result.stdout)
            for file in ('LICENSE', 'NOTICE'):
                self.assertTrue((destination / file).is_file())
                self.assertTrue((destination / 'references/protocol' / file).is_file())
            self.assertEqual((ROOT / 'LICENSE').read_bytes(), (destination / 'LICENSE').read_bytes())
            self.assertEqual((ROOT / 'spec/LICENSE').read_bytes(),
                             (destination / 'references/protocol/LICENSE').read_bytes())
            dws = ROOT / 'dws-aicard/skills/multi/dingtalk-aicard'
            for edition in (destination, dws):
                entry = edition / 'SKILL.md'
                self.assertLessEqual(entry.stat().st_size, 10_342)
                self.assertIn('## Delivery boundary', entry.read_text())
                if edition == dws:
                    self.assertIn('chat message update-a2ui-card', entry.read_text())
                self.assertFalse((edition / 'references/workflows').exists())
                self.assertFalse(any('evals' in path.parts for path in edition.rglob('*')))
                for doc in edition.rglob('*.md'):
                    for target in re.findall(r'\]\(([^\s)]+)\)', doc.read_text()):
                        parsed = urlsplit(target)
                        if parsed.scheme or not parsed.path:
                            continue
                        resolved = (doc.parent / unquote(parsed.path)).resolve()
                        with self.subTest(edition=str(edition), document=str(doc), target=target):
                            self.assertTrue(resolved.is_relative_to(edition.resolve()))
                            self.assertTrue(resolved.exists(), str(resolved))
            for file in ('LICENSE', 'NOTICE'):
                self.assertEqual((destination / file).read_bytes(), (dws / file).read_bytes())


if __name__ == '__main__':
    unittest.main()
