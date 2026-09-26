#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Synchronize the 11 public Schemas, documentation, examples, and internal validation rules from card-docs.

By default, rebuild from root spec/; --source explicitly imports upstream. Build indexes, the DWS Skill, and Go assets in a candidate before replacing managed files.
Failed candidate validation leaves the package untouched; write failures roll back this run; local edits are protected by default.
--check reports drift without writing. Public scenarios come from open/examples; test baselines come from the sibling conformance-fixtures.
With a custom --source, use --validation-rules and --conformance-source for matching resources.
"""
from __future__ import annotations
import re
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE_REL = Path('shared/.protocol-sync.json')
DEFAULT_SOURCE = ROOT / 'spec'
SKILL_PROTOCOL = Path('skills/dingtalk-aicard/references/protocol')
GO_ASSETS = Path('dws-aicard/internal/card/a2ui/assets.json')
GO_EXPLAIN = Path('dws-aicard/internal/card/a2ui/explain.json')
REPOSITORY_DOCS = ('README.md', 'CONTRIBUTING.md')
PROTOCOL_FILES = (
    'catalog-components-common.json', 'catalog-components-composition.json',
    'catalog-components-inputs.json', 'catalog-components-specialized.json',
    'catalog-functions-core.json', 'catalog-functions-expressions.json',
    'catalog-functions-host-actions.json', 'common-types-basic.json',
    'common-types-extended.json', 'common-types-visual.json', 'agent-to-renderer.json',
    'README.md', 'LICENSE', 'NOTICE',
)
SPEC_ONLY_DOCS = frozenset(('README.md',))
SKILL_PROTOCOL_FILES = tuple(name for name in PROTOCOL_FILES if name not in SPEC_ONLY_DOCS)
RETIRED = ('catalog-basic.json', 'catalog-extended.json', 'catalog-functions-a2ui.json',
           'compatibility.md', 'glossary.md')
DESTINATIONS = {
    'protocol': Path('spec'),
    'protocol_examples': Path('spec/examples'),
    'examples': Path('shared/fixtures/valid'),
    'validation_rules': Path('skills/dingtalk-aicard/scripts'),
    'repository_docs': Path('.'),
}
RULES_NAME = 'a2ui-validation-rules.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def imported_bytes(relative_path, source):
    """Keep maintainer metadata upstream, not on the public repository homepage."""
    content = Path(source).read_bytes()
    if Path(relative_path) == Path('README.md'):
        opening = re.match(br'\A(?:\xef\xbb\xbf)?---\r?\n', content)
        if opening:
            closing = re.search(br'^---\r?$(?:\n|\Z)', content[opening.end():], re.MULTILINE)
            if not closing:
                raise ValueError('Repository README has unterminated front matter')
            content = content[opening.end() + closing.end():].lstrip(b'\r\n')
    return content


EXAMPLE_GUIDANCE = {
    'form-interaction.json': ('Form bindings, upload events, and submit checks', 'form.md'),
    'host-action.json': ('Host dialog and result writeback', 'form.md#example-interaction-notes'),
    'agent-run-progress.json': ('Running state with generation effects and tool icons', 'progress.md'),
    'agent-run.json': ('Execution updates through completion', 'progress.md'),
    'data-report.json': ('Tabs, table pagination, charts, Loop, and Stack', 'report.md'),
}


def skill_examples_readme(example_names):
    """Build Skill-owned navigation independently of imported README prose."""
    names = set(example_names)
    if names != set(EXAMPLE_GUIDANCE):
        raise ValueError('Skill example guidance does not match JSON files: '
                         f'unmapped={sorted(names - set(EXAMPLE_GUIDANCE))}; '
                         f'missing={sorted(set(EXAMPLE_GUIDANCE) - names)}')
    lines = ['# Choose an example', '',
             'Read only the matching JSON. Adapt its content and layout to the task.', '',
             '| Need | JSON example | Guidance |', '|---|---|---|']
    for name, (need, pattern) in EXAMPLE_GUIDANCE.items():
        label = pattern.split('.')[0].capitalize()
        lines.append(f'| {need} | [{name}]({name}) | [{label}](../../patterns/{pattern}) |')
    lines += ['', 'Execution replay requires the [batch instructions](../../patterns/progress.md#examples-and-replay); '
              'sending the whole array at once may hide intermediate states.', '',
              'These arrays initialize new cards. For existing cards, send only intended changes, not initialization. '
              'See [Construct and validate](../../../SKILL.md#construct-and-validate) for creation and update scenarios.', '']
    return '\n'.join(lines).encode('utf-8')


def skill_protocol_contents(root):
    source = root / 'spec'
    names = {Path(n) for n in SKILL_PROTOCOL_FILES}
    names.update(Path('examples') / p.name for p in (source / 'examples').iterdir() if p.is_file())
    index = skill_examples_readme(p.name for p in (source / 'examples').glob('*.json'))
    return {rel: index if rel == Path('examples/README.md')
            else (source / rel).read_bytes() for rel in sorted(names)}


def load_state(root):
    path = root / STATE_REL
    return json.loads(path.read_text()) if path.exists() else {}


def example_files(directory):
    readme = directory / 'README.md'
    examples = {p.name: p for p in sorted(directory.glob('*.json'))}
    if not examples or not readme.is_file():
        raise ValueError('Example source or index is missing: ' + str(directory))
    indexed = set(re.findall(r'\]\(([^/()#]+\.json)\)', readme.read_text()))
    if set(examples) != indexed:
        raise ValueError('Example files do not match the index: ' + str(directory))
    return examples


def source_groups(source, rules, conformance=None, *, fixture_index=True, repository_docs=None):
    missing = [name for name in PROTOCOL_FILES if not (source / name).is_file()]
    if missing:
        raise ValueError('Upstream protocol files are missing: ' + '、'.join(missing))
    if not rules.is_file():
        raise ValueError('Matching internal validation rules are missing: ' + str(rules))
    examples = example_files(source / 'examples')
    fixture_dir = conformance or source.parent / 'conformance-fixtures'
    fixtures = (example_files(fixture_dir) if fixture_index else
                {p.name: p for p in sorted(fixture_dir.glob('*.json'))})
    if not fixtures:
        raise ValueError('Regression baseline is empty: ' + str(fixture_dir))
    groups = {
        'protocol': {name: source / name for name in PROTOCOL_FILES},
        'protocol_examples': {**examples, 'README.md': source / 'examples/README.md'},
        'examples': fixtures,
        'validation_rules': {RULES_NAME: rules},
    }
    if repository_docs is not None:
        if any(not (repository_docs / name).is_file() for name in REPOSITORY_DOCS):
            raise ValueError('Repository documentation source is missing: ' + str(repository_docs))
        groups['repository_docs'] = {name: repository_docs / name for name in REPOSITORY_DOCS}
    return groups


def plan_sync(root, groups, state):
    changes, tampered, files, unexpected = {}, [], {}, []
    for key, sources in groups.items():
        dest = DESTINATIONS[key]
        recorded = state.get('files', {}).get(key, {})
        files[key] = {name: hashlib.sha256(imported_bytes(dest / name, path)).hexdigest()
                      for name, path in sources.items()}
        names = set(sources) | set(recorded)
        if key in ('protocol_examples', 'examples'):
            unexpected.extend(str(p.relative_to(root)) for p in sorted((root / dest).glob('*.json'))
                              if p.name not in names)
        if key == 'protocol':
            names.update(RETIRED)
        for name in sorted(names):
            if Path(name).name != name:
                raise ValueError('Sync state contains an invalid filename: ' + name)
            rel = dest / name
            current = digest(root / rel) if (root / rel).is_file() else None
            wanted = files[key].get(name)
            if current == wanted:
                continue
            if current is not None and current != recorded.get(name):
                tampered.append(str(rel))
            changes[rel] = sources.get(name)
    return changes, tampered, files, unexpected


def run_check(command, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise ValueError('Candidate package validation failed: ' + ' '.join(map(str, command)) + '\n' + result.stdout + result.stderr)
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)


def managed_paths(root):
    paths = {STATE_REL, GO_ASSETS, *(Path(n) for n in REPOSITORY_DOCS), Path('skills/dingtalk-aicard/scripts') / RULES_NAME}
    paths.add(GO_EXPLAIN)
    for directory in ('spec', 'skills/dingtalk-aicard/references/protocol', 'skills/dingtalk-aicard/references/index',
                      'shared/fixtures/valid', 'dws-aicard/skills/multi/dingtalk-aicard'):
        paths.update(p.relative_to(root) for p in (root / directory).rglob('*') if p.is_file())
    return paths


def snapshot(root, paths):
    return {p: ((root / p).read_bytes(), stat.S_IMODE((root / p).stat().st_mode))
            if (root / p).is_file() else None for p in paths}


def replace_file(path, content, mode=None):
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    # Stage next to destination files: avoid system temporary directories and keep os.replace on the same filesystem.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.protocol-write-', delete=False) as handle:
        temp = Path(handle.name)
        handle.write(content)
    try:
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def publish(root, candidate, before):
    paths = set(before) | managed_paths(candidate)
    expected = {p: before.get(p) for p in paths}
    if snapshot(root, paths) != expected:
        raise ValueError('Distribution files changed during validation; refusing overwrite; rerun sync')
    desired = snapshot(candidate, paths)
    def replace(rel, entry):
        content, mode = entry if entry is not None else (None, None)
        replace_file(root / rel, content, mode=mode)
    written = []
    try:
        # Update state last; do not mark success before replacing package files.
        for rel in sorted(paths, key=lambda p: (p == STATE_REL, str(p))):
            if desired[rel] == expected[rel]:
                continue
            written.append(rel)
            replace(rel, desired[rel])
    except BaseException:
        for rel in reversed(written):
            replace(rel, expected[rel])
        raise


def generated_paths(root):
    paths = {GO_ASSETS}
    paths.add(GO_EXPLAIN)
    for directory in (str(SKILL_PROTOCOL), 'skills/dingtalk-aicard/references/index', 'dws-aicard/skills/multi/dingtalk-aicard'):
        paths.update(p.relative_to(root) for p in (root / directory).rglob('*')
                     if p.is_file() and p.name != '.DS_Store' and '__pycache__' not in p.parts)
    return paths


def release_state(files, previous=None, *, notice):
    match = re.search(r"Baseline commit\s*:\s*([a-f0-9]{40})", notice)
    if not match or "DingTalk AICard Protocol V0.8" not in notice:
        raise ValueError("NOTICE lacks the V0.8 name or pinned upstream commit")
    # The release manifest contains logical provenance, versions, and digests, not local absolute paths or timestamps.
    return {'formatVersion': 2, 'aicardVersion': 'V0.8', 'a2uiVersion': '1.0',
            'messageVersion': 'v1.0',
            'source': {'name': 'card-docs', 'path': 'protocol/a2ui/open'},
            'conformanceSource': {'name': 'card-docs', 'path': 'protocol/a2ui/conformance-fixtures'},
            'validationRulesSource': {'name': 'card-docs', 'path': 'tools/a2ui-open-package/validation-rules.json'},
            'upstream': {'name': 'A2UI', 'version': '1.0',
                         'commit': match.group(1)},
            'files': files, 'generated': (previous or {}).get('generated', {})}


def check_local_inputs(root, *, generated=True, skill=True):
    """Runnable from a public clone: validate distribution inputs and copies without accessing card-docs or user environment."""
    state = load_state(root)
    if state.get('formatVersion') != 2 or state.get('aicardVersion') != 'V0.8' or state.get('messageVersion') != 'v1.0':
        raise ValueError('Portable V0.8 release manifest is missing; a maintainer must import the upstream protocol first')
    expected = release_state({}, notice=(root / 'spec/NOTICE').read_text())
    for key in ('a2uiVersion', 'upstream'):
        if state.get(key) != expected[key]:
            raise ValueError('Release manifest version or upstream source disagrees with spec/NOTICE: ' + key)
    if set(state.get('files', {}).get('protocol', {})) != set(PROTOCOL_FILES):
        raise ValueError('Release manifest protocol file set is incomplete')
    for key, files in state['files'].items():
        if key not in DESTINATIONS or not files:
            raise ValueError('Release manifest contains an invalid group: ' + key)
        for name, expected in files.items():
            if Path(name).name != name:
                raise ValueError('Release manifest contains an invalid filename')
            path = root / DESTINATIONS[key] / name
            if not path.is_file() or digest(path) != expected:
                raise ValueError('Distribution input differs from the manifest: ' + str(path.relative_to(root)))
    if {p.name for p in (root / 'shared/fixtures/valid').glob('*.json')} != set(state['files']['examples']):
        raise ValueError('Regression fixture set has drifted')
    wanted = {Path(n) for n in PROTOCOL_FILES} | {Path('examples') / n for n in state['files']['protocol_examples']}
    copies = {Path('spec'): {rel: (root / 'spec' / rel).read_bytes() for rel in wanted}}
    if skill:
        copies[SKILL_PROTOCOL] = skill_protocol_contents(root)
    for directory, contents in copies.items():
        actual = {p.relative_to(root / directory) for p in (root / directory).rglob('*')
                  if p.is_file() and p.name != '.DS_Store'}
        if actual != set(contents):
            raise ValueError('Protocol directory file set has drifted: ' + str(directory))
        for rel, expected_content in contents.items():
            if (root / directory / rel).read_bytes() != expected_content:
                raise ValueError('Protocol copy has drifted: ' + str(directory / rel))
    if generated:
        if set(state.get('generated', {})) != {str(p) for p in generated_paths(root)}:
            raise ValueError('Generated artifact manifest is incomplete')
        for rel, expected in state['generated'].items():
            path = Path(rel)
            if path.is_absolute() or '..' in path.parts or not (root / path).is_file() or digest(root / path) != expected:
                raise ValueError('Generated artifact differs from release manifest: ' + rel)
    return state


def validate_candidate(candidate, official, skip_conformance=False):
    env = dict(os.environ, TMPDIR=str(candidate), PYTHONDONTWRITEBYTECODE='1')
    env.pop('AICARD_PROTOCOL_DIR', None)
    env.pop('AICARD_VALIDATION_RULES', None)
    run_check([sys.executable, '-B', 'skills/dingtalk-aicard/scripts/aicard_lint.py', '--self-check'], candidate, env)
    for script in ('build_index.py', 'build_dws_skill.py', 'build_go_assets.py'):
        run_check([sys.executable, '-B', 'tools/' + script], candidate, env)
    state = load_state(candidate)
    state['generated'] = {str(p): digest(candidate / p) for p in sorted(generated_paths(candidate))}
    (candidate / STATE_REL).write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    check_local_inputs(candidate)
    if not skip_conformance:
        run_check([sys.executable, '-B', 'tools/test_scenario_examples.py'], candidate, env)
        command = [sys.executable, '-B', 'tools/conformance.py']
        if official and official.is_dir():
            command += ['--official', str(official)]
        run_check(command, candidate, env)


def synchronize(root, source, rules, *, conformance=None, check=False, force=False,
                skip_conformance=False, repository_docs=None, official=None, local=False):
    root, source, rules = Path(root).resolve(), Path(source).resolve(), Path(rules).resolve()
    conformance = (conformance or source.parent / 'conformance-fixtures').resolve()
    state = load_state(root)
    if local:
        # Rebuilds must not silently re-register release inputs; generator changes may rebuild derived files.
        check_local_inputs(root, generated=False, skill=False)
    groups = source_groups(source, rules, conformance, fixture_index=not local, repository_docs=repository_docs)
    source_hashes = {k: {n: digest(p) for n, p in g.items()} for k, g in groups.items()}
    changes, tampered, files, unexpected = plan_sync(root, groups, state)
    if repository_docs is None and 'repository_docs' in state.get('files', {}):
        # Omitting a documentation source means no documentation update, not removal of existing digest protection.
        recorded = state['files']['repository_docs']
        if set(recorded) != set(REPOSITORY_DOCS):
            raise ValueError('Managed repository documentation manifest is incomplete')
        files['repository_docs'] = recorded
        for name, expected in recorded.items():
            path = root / name
            if not path.is_file() or digest(path) != expected:
                tampered.append(name)
    # Protect existing distribution directories: even the first migration must not overwrite local Skill edits.
    for key, prefix in (('protocol', Path('.')), ('protocol_examples', Path('examples'))):
        recorded = state.get('files', {}).get(key, {})
        for name, expected in recorded.items():
            path = root / SKILL_PROTOCOL / prefix / name
            expected = state.get('generated', {}).get(str(path.relative_to(root)), expected)
            if path.is_file() and digest(path) != expected:
                tampered.append(str(path.relative_to(root)))
        expected_names = set(recorded) | set(groups[key])
        unexpected += [str(p.relative_to(root)) for p in (root / SKILL_PROTOCOL / prefix).glob('*')
                       if p.is_file() and p.name not in expected_names and p.name != '.DS_Store']
    for rel, expected in state.get('generated', {}).items():
        path = root / rel
        if path.is_file() and digest(path) != expected:
            tampered.append(rel)
    for rel in changes:
        print(('Update' if changes[rel] else 'Remove') + ' ' + str(rel))
    if check:
        problems = list(tampered) + list(unexpected)
        try:
            check_local_inputs(root)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            for script in ('build_index.py', 'build_dws_skill.py', 'build_go_assets.py'):
                run_check([sys.executable, '-B', 'tools/' + script, '--check'], root, env)
        except ValueError as error:
            problems.append(str(error))
        if problems:
            print('\n'.join(problems), file=sys.stderr)
        print('--check: no files modified')
        return int(bool(changes or problems))
    if unexpected:
        raise ValueError('Refusing to remove unregistered example or protocol files: ' + ', '.join(unexpected))
    if tampered and not force:
        raise ValueError('Refusing to overwrite local edits: ' + '、'.join(sorted(set(tampered))))
    before = snapshot(root, managed_paths(root))
    with tempfile.TemporaryDirectory(prefix='.protocol-sync-', dir=root) as temp:
        candidate = Path(temp)
        for sub in ('tools', 'spec', 'skills/dingtalk-aicard', 'shared', 'dws-aicard'):
            if (root / sub).is_dir():
                shutil.copytree(root / sub, candidate / sub, ignore=shutil.ignore_patterns('__pycache__'))
        for name in (*REPOSITORY_DOCS, 'LICENSE', 'NOTICE'):
            if (root / name).is_file():
                shutil.copy2(root / name, candidate / name)
        for rel, src in changes.items():
            target = candidate / rel
            if src is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(imported_bytes(rel, src))
                shutil.copymode(src, target)
        target = candidate / SKILL_PROTOCOL
        if target.exists():
            shutil.rmtree(target)
        for rel, content in skill_protocol_contents(candidate).items():
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (candidate / STATE_REL).write_text(json.dumps(release_state(files, state, notice=(candidate / 'spec/NOTICE').read_text()), ensure_ascii=False, indent=2) + '\n')
        validate_candidate(candidate, official, skip_conformance)
        current = source_groups(source, rules, conformance, fixture_index=not local, repository_docs=repository_docs)
        if {k: {n: digest(p) for n, p in g.items()} for k, g in current.items()} != source_hashes:
            raise ValueError('Upstream resources changed during validation; rerun sync')
        publish(root, candidate, before)
    print('Protocol, rules, indexes, DWS Skill, and Go assets were updated as one release snapshot.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help="Import upstream open/ as a maintainer; otherwise rebuild from this repository's spec/")
    parser.add_argument('--conformance-source', type=Path, help='Component regression baseline directory for import')
    parser.add_argument('--validation-rules', type=Path, help='Matching rules file for import')
    parser.add_argument('--repository-docs', type=Path, help='Source directory for README.md / CONTRIBUTING.md during import')
    parser.add_argument('--official', type=Path, help='Official A2UI 1.0 conformance case directory')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--refresh-generated', action='store_true',
                        help='Recompute only generated digests after checking unchanged managed sources and generators')
    parser.add_argument('--force', action='store_true', help='Explicitly discard local edits to managed generated files')
    parser.add_argument('--skip-conformance', action='store_true', help='Skip only the full test suite; keep self-check, generation, and digest verification')
    args = parser.parse_args(argv)
    if args.refresh_generated:
        if any((args.source, args.conformance_source, args.validation_rules, args.repository_docs,
                args.official, args.check, args.force, args.skip_conformance)):
            parser.error('--refresh-generated cannot be combined with import or sync options')
        try:
            check_local_inputs(ROOT, generated=False)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            for script in ('build_index.py', 'build_dws_skill.py', 'build_go_assets.py'):
                run_check([sys.executable, '-B', 'tools/' + script, '--check'], ROOT, env)
            state = load_state(ROOT)
            state['generated'] = {str(p): digest(ROOT / p) for p in sorted(generated_paths(ROOT))}
            replace_file(ROOT / STATE_REL, (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            check_local_inputs(ROOT)
            print('Refreshed generated digests without changing managed protocol or repository documentation.')
            return 0
        except (OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
    local = args.source is None
    if local and any((args.conformance_source, args.validation_rules, args.repository_docs)):
        parser.error('External rules, baselines, and documentation sources require --source')
    source = (args.source or DEFAULT_SOURCE).resolve()
    rules = ((args.validation_rules or source.parents[2] / 'tools/a2ui-open-package/validation-rules.json')
             if not local else ROOT / 'skills/dingtalk-aicard/scripts' / RULES_NAME).resolve()
    fixtures = args.conformance_source or (ROOT / 'shared/fixtures/valid' if local else source.parent / 'conformance-fixtures')
    try:
        return synchronize(ROOT, source, rules, conformance=fixtures, check=args.check, force=args.force,
                           skip_conformance=args.skip_conformance, repository_docs=args.repository_docs,
                           official=args.official, local=local)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
