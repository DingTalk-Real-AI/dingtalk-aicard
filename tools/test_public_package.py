#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Check distribution links and standalone installation without network, Profile, or sending."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class PublicPackageTests(unittest.TestCase):
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
            for file in ('LICENSE', 'NOTICE'):
                self.assertEqual((destination / file).read_bytes(), (dws / file).read_bytes())


if __name__ == '__main__':
    unittest.main()
