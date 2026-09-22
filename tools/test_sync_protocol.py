#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Test sync rollback, migration, and paired rules with workspace-local temporary files."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
# Localized failure-injection strings verify migration and rollback preserve existing data.
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync_protocol', ROOT / 'tools/sync_protocol.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR') or ROOT)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.repo = self.work / 'repo'
        for sub in ('tools', 'skills/dingtalk-aicard', 'shared', 'dws-aicard'):
            shutil.copytree(ROOT / sub, self.repo / sub, ignore=shutil.ignore_patterns('__pycache__'))
        self.source = self.work / 'open'
        shutil.copytree(ROOT / 'spec', self.source)
        self.rules = self.work / 'validation-rules.json'
        shutil.copy2(ROOT / 'skills/dingtalk-aicard/scripts/a2ui-validation-rules.json', self.rules)
        self.fixtures = self.work / 'conformance-fixtures'
        shutil.copytree(ROOT / 'shared/fixtures/valid', self.fixtures)
        names = sorted(p.name for p in self.fixtures.glob('*.json'))
        (self.fixtures / 'README.md').write_text('\n'.join(f'[{n}]({n})' for n in names))
        groups = sync.source_groups(self.source, self.rules)
        self.state = {'source': str(self.source), 'conformanceSource': str(self.fixtures), 'files': {
            k: {n: sync.digest(p) for n, p in group.items()} for k, group in groups.items()}}
        self.state['generated'] = {str(p): sync.digest(self.repo / p) for p in sync.generated_paths(self.repo)}
        self.save_state()
        self.before = sync.snapshot(self.repo, sync.managed_paths(self.repo))
        (self.source / 'README.md').write_text((self.source / 'README.md').read_text() + '\n同步测试说明\n')

    def save_state(self):
        (self.repo / sync.STATE_REL).write_text(json.dumps(self.state))

    def run_sync(self, **options):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return sync.synchronize(self.repo, self.source, self.rules, **options)

    def assert_unchanged(self):
        self.assertEqual(self.before, sync.snapshot(self.repo, sync.managed_paths(self.repo)))

    def test_missing_shard_or_rules_does_not_modify_installed_package(self):
        for path in (self.source / sync.PROTOCOL_FILES[0], self.rules, self.fixtures / 'README.md', self.fixtures / 'text.json', self.source / 'examples/form-interaction.json'):
            body = path.read_bytes()
            path.unlink()
            with self.assertRaises(ValueError):
                self.run_sync()
            self.assert_unchanged()
            path.write_bytes(body)

    def test_invalid_ref_or_rules_fails_before_publication(self):
        for path, mutate in (
            (self.source / 'common-types-basic.json', lambda doc: doc['$defs'].update(Bad={'$ref': './missing.json#/$defs/X'})),
            (self.rules, lambda doc: doc['references'][0].update(publicRef='wrong.json#/$defs/X')),
        ):
            original = path.read_bytes()
            doc = json.loads(original)
            mutate(doc)
            path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, 'Candidate package validation failed'):
                self.run_sync(skip_conformance=True)
            self.assert_unchanged()
            path.write_bytes(original)

    def test_conformance_failure_preserves_package_and_state(self):
        original = sync.subprocess.run
        def reject_conformance(command, **kwargs):
            if any(str(x).endswith('conformance.py') for x in command):
                return subprocess.CompletedProcess(command, 1, '注入一致性失败', '')
            return original(command, **kwargs)
        with patch.object(sync.subprocess, 'run', side_effect=reject_conformance):
            with self.assertRaisesRegex(ValueError, '注入一致性失败'):
                self.run_sync()
        self.assert_unchanged()

    def test_retired_file_with_local_edits_is_not_silently_deleted(self):
        path = self.repo / 'skills/dingtalk-aicard/references/protocol/catalog-basic.json'
        path.write_text('上次同步版本')
        self.state['files']['protocol'][path.name] = sync.digest(path)
        self.save_state()
        path.write_text('用户本地改动')
        self.before = sync.snapshot(self.repo, sync.managed_paths(self.repo))
        with self.assertRaisesRegex(ValueError, 'Refusing to overwrite local edits'):
            self.run_sync()
        self.assert_unchanged()

    def test_success_removes_retired_file_and_generates_consistent_copies(self):
        retired = []
        for name in ('catalog-basic.json', 'catalog-functions-a2ui.json'):
            path = self.repo / 'skills/dingtalk-aicard/references/protocol' / name
            path.write_text('{}')
            self.state['files']['protocol'][path.name] = sync.digest(path)
            retired.append(path)
        self.save_state()
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        for path in retired:
            self.assertFalse(path.exists())
        self.assertTrue((self.repo / 'skills/dingtalk-aicard/references/protocol/catalog-functions-core.json').is_file())
        self.assertEqual(0, self.run_sync(check=True))
        for script in ('build_index.py', 'build_dws_skill.py'):
            result = subprocess.run([sys.executable, '-B', str(self.repo / 'tools' / script), '--check'], capture_output=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        state = sync.load_state(self.repo)
        self.assertEqual(set(sync.PROTOCOL_FILES), set(state['files']['protocol']))
        self.assertFalse((self.repo / sync.SKILL_PROTOCOL / 'README.md').exists())
        self.assertEqual(sync.skill_examples_readme((self.repo / 'spec/examples/README.md').read_bytes()),
                         (self.repo / sync.SKILL_PROTOCOL / 'examples/README.md').read_bytes())
        self.assertIn(str(sync.GO_ASSETS), state['generated'])
        self.assertIn(sync.RULES_NAME, state['files']['validation_rules'])

    def test_repository_readme_stays_in_spec_and_old_docs_are_removed(self):
        # Model an older full distribution and verify removal of retired pages and Skill copies.
        for name in ('compatibility.md', 'glossary.md'):
            path = self.repo / 'spec' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('旧独立说明')
            self.state['files']['protocol'][name] = sync.digest(path)
        for directory in (sync.SKILL_PROTOCOL, Path('dws-aicard/skills/multi/dingtalk-aicard/references/protocol')):
            for name in ('README.md', 'compatibility.md', 'glossary.md'):
                path = self.repo / directory / name
                path.write_text('旧发行说明')
                self.state['generated'][str(path.relative_to(self.repo))] = sync.digest(path)
        self.save_state()
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        self.assertEqual((self.source / 'README.md').read_bytes(), (self.repo / 'spec/README.md').read_bytes())
        for name in ('compatibility.md', 'glossary.md'):
            self.assertFalse((self.repo / 'spec' / name).exists())
        for directory in (sync.SKILL_PROTOCOL, Path('dws-aicard/skills/multi/dingtalk-aicard/references/protocol')):
            for name in ('README.md', 'compatibility.md', 'glossary.md'):
                self.assertFalse((self.repo / directory / name).exists())
                self.assertNotIn(name, (self.repo / directory / 'examples/README.md').read_text())
        sync.check_local_inputs(self.repo)
        path = self.repo / sync.SKILL_PROTOCOL / 'glossary.md'
        path.write_text('旧术语')
        with self.assertRaisesRegex(ValueError, 'file set has drifted'):
            sync.check_local_inputs(self.repo)

    def test_excluded_doc_with_local_edits_is_protected(self):
        path = self.repo / sync.SKILL_PROTOCOL / 'compatibility.md'
        path.write_text('旧兼容说明')
        self.state['files']['protocol'][path.name] = sync.digest(path)
        self.state['generated'][str(path.relative_to(self.repo))] = sync.digest(path)
        self.save_state()
        path.write_text('用户本地改动')
        self.before = sync.snapshot(self.repo, sync.managed_paths(self.repo))
        with self.assertRaisesRegex(ValueError, 'Refusing to overwrite local edits'):
            self.run_sync(skip_conformance=True)
        self.assert_unchanged()

    def test_example_index_projection_keeps_source_and_skill_links_separate(self):
        source = (self.source / 'examples/README.md').read_bytes()
        projected = sync.skill_examples_readme(source)
        self.assertIn(b'../../../SKILL.md', projected)
        self.assertNotIn(b'../README.md', projected)
        self.assertEqual(source, (self.source / 'examples/README.md').read_bytes())
        with self.assertRaisesRegex(ValueError, 'Surface'):
            sync.skill_examples_readme(b'changed source index')

    def test_scenario_and_fixture_sources_are_independent(self):
        groups = sync.source_groups(self.source, self.rules)
        self.assertEqual(4, len(groups['protocol_examples']) - 1)
        self.assertEqual(54, len(groups['examples']))
        self.assertIn('form-interaction.json', groups['protocol_examples'])
        self.assertNotIn('form-interaction.json', groups['examples'])
        self.assertIn('text.json', groups['examples'])
        moved = self.work / 'custom-fixtures'
        self.fixtures.rename(moved)
        with self.assertRaises(ValueError):
            self.run_sync()
        self.assert_unchanged()
        self.assertEqual(0, self.run_sync(conformance=moved, skip_conformance=True))
        self.assertEqual('protocol/a2ui/conformance-fixtures', sync.load_state(self.repo)['conformanceSource']['path'])
        self.assertNotIn(str(self.work), (self.repo / sync.STATE_REL).read_text())

    def test_retired_scenario_is_removed_but_modified_copy_is_protected(self):
        path = self.repo / 'skills/dingtalk-aicard/references/protocol/examples/basic.json'
        path.write_bytes((self.fixtures / path.name).read_bytes())
        self.state['files']['protocol_examples'][path.name] = sync.digest(path)
        self.save_state()
        before = path.read_bytes()
        path.write_text('本地修改')
        with self.assertRaisesRegex(ValueError, 'Refusing to overwrite local edits'):
            self.run_sync()
        path.write_bytes(before)
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        self.assertFalse(path.exists())
        self.assertTrue((self.repo / 'shared/fixtures/valid/basic.json').exists())

    def test_write_failure_rolls_back_and_concurrent_change_is_rejected(self):
        (self.repo / 'skills/dingtalk-aicard/references/protocol/catalog-components-common.json').chmod(0o640)
        removed = Path('skills/dingtalk-aicard/references/index/aaa-retired.md')
        (self.repo / removed).write_text('待删除的旧索引')
        (self.repo / removed).chmod(0o640)
        self.before = sync.snapshot(self.repo, sync.managed_paths(self.repo))
        candidate = self.work / 'candidate'
        shutil.copytree(self.repo, candidate)
        (candidate / removed).unlink()
        for rel in ('skills/dingtalk-aicard/references/protocol/catalog-components-common.json', 'skills/dingtalk-aicard/references/index/components.md'):
            (candidate / rel).write_text('候选变更')
        original = sync.replace_file
        count = 0
        def fail_second(path, content, mode=None):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('注入写入失败')
            return original(path, content, mode=mode)
        with patch.object(sync, 'replace_file', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, '注入写入失败'):
                sync.publish(self.repo, candidate, self.before)
        self.assert_unchanged()
        (self.repo / 'skills/dingtalk-aicard/references/protocol/catalog-components-common.json').write_text('并发修改')
        with self.assertRaisesRegex(ValueError, 'Distribution files changed during validation'):
            sync.publish(self.repo, candidate, self.before)
        self.assertEqual('并发修改', (self.repo / 'skills/dingtalk-aicard/references/protocol/catalog-components-common.json').read_text())

    def test_unknown_examples_fail_check_and_are_never_deleted(self):
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        for directory in ('skills/dingtalk-aicard/references/protocol/examples', 'shared/fixtures/valid'):
            self.assertEqual(0, self.run_sync(check=True))
            extra = self.repo / directory / 'unregistered.json'
            extra.write_text('[]')
            self.before = sync.snapshot(self.repo, sync.managed_paths(self.repo))
            with self.subTest(directory=directory):
                self.assertEqual(1, self.run_sync(check=True))
                for force in (False, True):
                    with self.assertRaisesRegex(ValueError, 'Refusing to remove unregistered example'):
                        self.run_sync(force=force, skip_conformance=True)
                    self.assert_unchanged()
            extra.unlink()

    def test_publication_preserves_modes_including_new_executable(self):
        for mode in (0o644, 0o755):
            target = self.work / 'replace.txt'
            target.write_bytes(b'old')
            target.chmod(mode)
            sync.replace_file(target, b'new')
            self.assertEqual(mode, stat.S_IMODE(target.stat().st_mode))
        fresh = self.work / 'fresh.json'
        sync.replace_file(fresh, b'{}')
        self.assertEqual(0o644, stat.S_IMODE(fresh.stat().st_mode))
        candidate = self.work / 'mode-candidate'
        shutil.copytree(self.repo, candidate)
        rel = Path('dws-aicard/skills/multi/dingtalk-aicard/references/new-helper.py')
        (candidate / rel).write_text('#!/usr/bin/env python3\n')
        (candidate / rel).chmod(0o755)
        sync.publish(self.repo, candidate, self.before)
        self.assertEqual(0o755, stat.S_IMODE((self.repo / rel).stat().st_mode))

    def test_concurrent_permission_change_is_rejected(self):
        candidate = self.work / 'mode-candidate'
        shutil.copytree(self.repo, candidate)
        target = self.repo / 'skills/dingtalk-aicard/references/protocol/catalog-components-common.json'
        target.chmod(0o640)
        with self.assertRaisesRegex(ValueError, 'Distribution files changed during validation'):
            sync.publish(self.repo, candidate, self.before)
        self.assertEqual(0o640, stat.S_IMODE(target.stat().st_mode))

    def test_conformance_rejects_missing_extra_and_modified_fixtures_before_commands(self):
        import conformance
        baseline = self.repo / 'shared/fixtures/valid/text.json'
        original = baseline.read_bytes()
        extra = baseline.with_name('unregistered.json')
        for mutation in ('missing', 'extra', 'changed', 'empty_manifest', 'broken_manifest'):
            if mutation == 'missing':
                baseline.unlink()
            elif mutation == 'extra':
                extra.write_text('[]')
            elif mutation == 'changed':
                baseline.write_bytes(original + b' ')
            elif mutation == 'empty_manifest':
                (self.repo / sync.STATE_REL).write_text('{"files":{"examples":{}}}')
            else:
                (self.repo / sync.STATE_REL).write_text('{')
            with self.subTest(mutation=mutation), patch.object(conformance, 'ROOT', str(self.repo)), \
                    patch.object(conformance, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    conformance.main([])
                self.assertEqual(2, error.exception.code)
                run.assert_not_called()
            baseline.write_bytes(original)
            extra.unlink(missing_ok=True)
            self.save_state()

    def test_changed_fixture_is_validated_against_candidate_manifest(self):
        import conformance
        fixture = self.fixtures / 'text.json'
        fixture.write_bytes(fixture.read_bytes() + b'\n')
        validate = sync.validate_candidate
        def inspect(candidate, official, skip_conformance=False):
            files = conformance.managed_valid_files(candidate)
            self.assertEqual(54, len(files))
            self.assertEqual(sync.digest(fixture), sync.load_state(candidate)['files']['examples']['text.json'])
            return validate(candidate, official, skip_conformance)
        with patch.object(sync, 'validate_candidate', side_effect=inspect):
            self.assertEqual(0, self.run_sync(skip_conformance=True))
        self.assertEqual(54, len(conformance.managed_valid_files(self.repo)))

    def test_local_rebuild_needs_no_upstream_and_binds_go_assets(self):
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        shutil.rmtree(self.source)
        self.rules.unlink()
        shutil.rmtree(self.fixtures)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, sync.synchronize(self.repo, self.repo / 'spec',
                self.repo / 'skills/dingtalk-aicard/scripts' / sync.RULES_NAME,
                conformance=self.repo / 'shared/fixtures/valid', local=True, skip_conformance=True))
        state = sync.check_local_inputs(self.repo)
        self.assertEqual(sync.digest(self.repo / sync.GO_ASSETS), state['generated'][str(sync.GO_ASSETS)])

    def test_go_generation_failure_preserves_all_published_files(self):
        original = sync.subprocess.run
        def reject(command, **kwargs):
            if any(str(x).endswith('build_go_assets.py') for x in command):
                return subprocess.CompletedProcess(command, 1, '', '注入 Go 资产生成失败')
            return original(command, **kwargs)
        with patch.object(sync.subprocess, 'run', side_effect=reject):
            with self.assertRaisesRegex(ValueError, 'Go 资产生成失败'):
                self.run_sync(skip_conformance=True)
        self.assert_unchanged()

    def track_repository_docs(self):
        for name in sync.REPOSITORY_DOCS:
            (self.repo / name).write_text('现有仓库说明：' + name)
        recorded = {name: sync.digest(self.repo / name) for name in sync.REPOSITORY_DOCS}
        self.state['files']['repository_docs'] = recorded
        self.save_state()
        return dict(recorded)

    def test_external_import_without_docs_preserves_their_hashes(self):
        recorded = self.track_repository_docs()
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        self.assertEqual(recorded, sync.check_local_inputs(self.repo)['files']['repository_docs'])
        self.assertEqual(0, self.run_sync(check=True))

    def test_unprovided_docs_cannot_hide_missing_or_modified_files(self):
        self.track_repository_docs()
        doc = self.repo / 'README.md'
        original = doc.read_bytes()
        for missing in (False, True):
            if missing:
                doc.unlink()
            else:
                doc.write_bytes(original + b' changed')
            self.before = sync.snapshot(self.repo, sync.managed_paths(self.repo))
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'Refusing to overwrite local edits'):
                self.run_sync(skip_conformance=True)
            self.assert_unchanged()
            doc.write_bytes(original)

    def test_explicit_docs_source_updates_contents_and_hashes(self):
        self.track_repository_docs()
        docs = self.work / 'repository-docs'
        docs.mkdir()
        for name in sync.REPOSITORY_DOCS:
            (docs / name).write_text('上游新说明：' + name)
        self.assertEqual(0, self.run_sync(repository_docs=docs, skip_conformance=True))
        state = sync.check_local_inputs(self.repo)
        for name in sync.REPOSITORY_DOCS:
            self.assertEqual((docs / name).read_bytes(), (self.repo / name).read_bytes())
            self.assertEqual(sync.digest(docs / name), state['files']['repository_docs'][name])

    def test_release_versions_and_upstream_must_match_notice(self):
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        original = sync.load_state(self.repo)
        changes = [('a2uiVersion', '0.8'),
                   ('upstream', dict(original['upstream'], version='0.8')),
                   ('upstream', dict(original['upstream'], commit='0' * 40))]
        for key, value in changes:
            with self.subTest(key=key, value=value), patch.object(sync, 'load_state', return_value=dict(original, **{key: value})):
                with self.assertRaisesRegex(ValueError, 'disagrees with spec/NOTICE'):
                    sync.check_local_inputs(self.repo)

    def test_tampered_go_asset_cannot_be_silently_rebuilt(self):
        self.assertEqual(0, self.run_sync(skip_conformance=True))
        path = self.repo / sync.GO_ASSETS
        path.write_bytes(path.read_bytes() + b' ')
        self.before = sync.snapshot(self.repo, sync.managed_paths(self.repo))
        with self.assertRaisesRegex(ValueError, 'Refusing to overwrite local edits'):
            self.run_sync(skip_conformance=True)
        self.assert_unchanged()


if __name__ == '__main__':
    unittest.main()
