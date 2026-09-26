#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Conformance baseline: every A2UI validator implementation must agree on these cases.

    Valid cases   shared/fixtures/valid/*.json — migrated from conformance-fixtures; no errors allowed
    Scenarios     skills/dingtalk-aicard/references/protocol/examples/*.json — four authoring scenarios and message prefixes
    Invalid cases shared/fixtures/invalid/*/ — expectations.json specifies validity, severity, code, and pointer
    Boundaries    shared/fixtures/boundaries/*/ — retained runtime/design cases; structural validation must not misreport
    Patterns      shared/fixtures/patterns/*.json — regression cases migrated from design patterns
    Official      a2ui/specification/v1_0/test/cases/*.json — official A2UI v1.0 conformance cases (--fragment)
            Intentional DingTalk open/ differences are registered in DIVERGENCES; lint must follow the DingTalk contract
    Generated     dws-aicard/skills/multi/dingtalk-aicard/ — must be built from skills/dingtalk-aicard/ by build_dws_skill.py

Run this baseline against Python; use --cmd and --explain-cmd to check a DWS Go implementation.

    python3 tools/conformance.py                    # run the default Python implementation
    python3 tools/conformance.py --cmd "dws aicard lint --file {file} --format json" --explain-cmd "dws aicard explain {name} --format json"

The command under test must accept a file path and output {"valid":bool,"diagnostics":[{"code","severity",...}]}.
Exit codes: 0 all cases pass; 1 at least one mismatch.
"""
from __future__ import annotations
import argparse, copy, glob, hashlib, json, os, shlex, subprocess, sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Intentional differences from official A2UI v1.0: (case filename fragment, description fragment) -> (expected DingTalk validity, reason)
# Each entry was checked against both schemas; confirm an actual protocol change in card-docs before editing.
DIVERGENCES = {
    ("initial_state_validation", "component metadata.extensions has invalid key name"): (True, "Unused DingTalk ComponentMetadata extensions pass through; the Surface UAX identifier constraint does not apply"),
    ("button_checks", "optional userMessage"): (False, "DingTalk Action.event does not expose userMessage"),
    ("icon_checks", "Valid standard icon string"): (False, "DingTalk IconName uses its own enum and does not accept the official star shorthand"),
    ("checkable_components", "check without static message"): (False, "DingTalk requires CheckRule.message; upstream makes it optional"),
    ("checkable_components", "Slider with valid steps"):       (False, "DingTalk Slider removed steps"),
    ("icon_checks", "literal svgPath"):                        (False, "DingTalk Icon.name does not accept {svgPath}; only its IconName enum"),
    ("icon_checks", "data bound svgPath"):                     (False, "DingTalk Icon.name does not accept {svgPath}; only its IconName enum"),
    ("function_catalog_validation", "openUrl: Invalid URL format"): (True, "DingTalk url has no format:uri and imposes no additional URI-scheme restriction"),
    ("tabs_checks", "empty tabs array"):                       (True, "DingTalk Tabs.tabs has no minItems; structural validation accepts an empty array"),
}

DEFAULT_CMD = shlex.join([sys.executable, os.path.join(ROOT, "skills", "dingtalk-aicard", "scripts", "aicard_lint.py"), "{file}", "--format", "json"])

DEFAULT_EXPLAIN_CMD = shlex.join([sys.executable, os.path.join(ROOT, "skills", "dingtalk-aicard", "scripts", "aicard_lint.py"), "--explain", "{name}", "--format", "json"])


def decode_command_result(proc):
    """Route failures through stderr/error.details; do not treat environment errors as negative protocol cases."""
    raw = proc.stdout.strip() or proc.stderr.strip()
    result = json.loads(raw)
    if isinstance(result, dict) and result.get("ok") is False:
        details = (result.get("error") or {}).get("details")
        if isinstance(details, dict):
            return details, True
    if isinstance(result, dict) and "data" in result and "valid" not in result and "kind" not in result:
        return result["data"], True
    return result, False


def check_explanation(result, expected):
    """The answer must match this query; returning some other valid component structure must fail."""
    if result != expected:
        differing = sorted(k for k in set(result) | set(expected) if result.get(k) != expected.get(k))
        raise ValueError("explain contract differs from the reference result: " + ", ".join(differing))


def temporary_directory():
    """Keep test artifacts within the repository; do not accept an environment variable pointing elsewhere."""
    root = Path(ROOT).resolve()
    directory = (root / ".analysis-artifacts" / "conformance-tmp").resolve()
    if root not in directory.parents or any(
            directory == forbidden or forbidden in directory.parents
            for forbidden in (Path("/private/tmp"), Path("/tmp"))):
        raise ValueError("Test temporary directory must be inside the repository, not in a system temporary directory")
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


# Register the nine original cases and their modes explicitly so documentation changes or missing files cannot silently reduce coverage.
# False uses the default message-array input; True additionally covers fragment adaptation with the same structural rules.
PATTERN_FIXTURES = {
    "approval.json": False,
    "approval-terminal.json": True,
    "form.json": False,
    "generic.json": False,
    "information.json": False,
    "notification.json": False,
    "report.json": False,
    "schedule.json": False,
    "task.json": False,
}


def pattern_fixture_cases(root):
    """Test data is independent of authoring docs; missing or unregistered cases fail the baseline."""
    directory = Path(root) / "shared/fixtures/patterns"
    actual = {p.name: p for p in directory.glob("*.json") if p.is_file()}
    missing = sorted(set(PATTERN_FIXTURES) - set(actual))
    extra = sorted(set(actual) - set(PATTERN_FIXTURES))
    if missing or extra:
        raise ValueError(f"Pattern regression manifest mismatch: missing {missing}; unregistered {extra}")
    return [(str(actual[name]), PATTERN_FIXTURES[name]) for name in sorted(actual)]


def run(cmd_template: str, path: str, timeout=30):
    # Pass the filename as a separate argument so spaces, quotes, or shell metacharacters cannot change the command.
    command = [part.replace("{file}", path) for part in shlex.split(cmd_template)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"_error": f"Validation command exceeded {timeout} seconds and was terminated"}
    except OSError as error:
        return {"_error": f"Cannot execute validation command: {error}"}
    try:
        result, wrapped = decode_command_result(proc)
    except json.JSONDecodeError:
        detail = (proc.stdout + proc.stderr).strip()[:300] or 'both stdout and stderr are empty'
        return {"_error": f"Command returned invalid JSON (exit code {proc.returncode}): {detail}",
                "exitCode": proc.returncode}
    if not isinstance(result, dict):
        return {"_error": "Command did not return a JSON object"}
    if not isinstance(result, dict) or not isinstance(result.get("valid"), bool) or not isinstance(result.get("diagnostics"), list):
        return {"_error": "Missing valid or diagnostics"}
    if any(not isinstance(d, dict) or not all(k in d for k in ("code", "severity", "pointer", "message")) for d in result["diagnostics"]):
        return {"_error": "Diagnostic fields are incomplete"}
    expected_codes = (0,) if result["valid"] else ((1, 3) if wrapped else (1,))
    if proc.returncode not in expected_codes:
        return {"_error": f"Exit code {proc.returncode} disagrees with valid={result['valid']} or indicates an environment failure"}
    has_errors = any(d["severity"] == "error" for d in result["diagnostics"])
    if result["valid"] == has_errors:
        return {"_error": "valid disagrees with error-severity diagnostics"}
    return result


def fixture_cases(root):
    """Lock invalid structural and boundary cases to the manifest; missing and unregistered inputs fail."""
    base = Path(root) / 'shared/fixtures'
    manifest = json.loads((base / 'expectations.json').read_text())
    actual = {str(path.parent.relative_to(base)) for section in ('invalid', 'boundaries')
              for path in (base / section).glob('*/input.json')}
    if not isinstance(manifest, dict) or not manifest or set(manifest) != actual:
        raise ValueError('Structural case manifest mismatch: missing or unregistered inputs')
    for name, expected in manifest.items():
        if (not isinstance(expected, dict) or not isinstance(expected.get('valid'), bool)
                or not isinstance(expected.get('diagnostics'), list)
                or expected['valid'] and bool(expected['diagnostics'])
                or not expected['valid'] and not expected['diagnostics']):
            raise ValueError(f'Structural case expectation is incomplete: {name}')
        for diagnostic in expected['diagnostics']:
            if (not isinstance(diagnostic, dict) or diagnostic.get('severity') != 'error'
                    or not all(isinstance(diagnostic.get(k), str) for k in ('code', 'pointer'))):
                raise ValueError(f'Structural error case lacks severity or pointer: {name}')
    return [(name, str(base / name / 'input.json'), expected) for name, expected in sorted(manifest.items())]


def check_expectation(result, expected):
    if '_error' in result:
        return result['_error'] or 'Validation command failed without an error message'
    if result['valid'] != expected['valid']:
        return f"Expected valid={expected['valid']}, got {result['valid']}"
    if expected['valid']:
        return 'Structurally valid cases must not produce runtime or design diagnostics' if result['diagnostics'] else None
    for wanted in expected['diagnostics']:
        if not any(all(actual.get(k) == v for k, v in wanted.items()) for actual in result['diagnostics']):
            return f'Expected structural error is missing: {wanted}'
    return None


def managed_valid_files(root):
    """Lock regression cases to the sync manifest so missing, extra, or changed files cannot pass silently."""
    root = Path(root)
    try:
        recorded = json.loads((root / 'shared/.protocol-sync.json').read_text())['files']['examples']
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError('Cannot read regression sync manifest') from error
    if not isinstance(recorded, dict) or not recorded:
        raise ValueError('Regression sync manifest is empty or malformed')
    directory = root / 'shared/fixtures/valid'
    actual = {p.name: p for p in directory.glob('*.json')}
    missing, extra = sorted(set(recorded) - set(actual)), sorted(set(actual) - set(recorded))
    if missing or extra:
        raise ValueError(f'Regression manifest mismatch: missing {missing}; unregistered {extra}')
    changed = [name for name, path in sorted(actual.items())
               if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != recorded[name]]
    if changed:
        raise ValueError('Regression fixture content has drifted: ' + '、'.join(changed))
    return [str(actual[name]) for name in sorted(actual)]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cmd", default=DEFAULT_CMD, help="Command under test; use {file} as a placeholder")
    parser.add_argument("--explain-cmd", default=None, help="explain command under test; use {name}; required with a custom lint command")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--official", default=None, help="Official A2UI specification/v1_0/test/cases directory; inferred from a sibling checkout by default")
    parser.add_argument("--require-official", action="store_true", help="Release check: fail if official baseline cases are missing")
    args = parser.parse_args(argv)
    if args.cmd != DEFAULT_CMD and not args.explain_cmd:
        parser.error("A custom --cmd also requires --explain-cmd to avoid mixing implementations")
    args.explain_cmd = args.explain_cmd or DEFAULT_EXPLAIN_CMD
    if "{name}" not in args.explain_cmd:
        parser.error("--explain-cmd must contain {name}")

    official_dir = args.official or os.path.normpath(os.path.join(
        ROOT, "..", "..", "a2ui", "specification", "v1_0", "test", "cases"))
    if args.require_official and not glob.glob(os.path.join(official_dir, '*.json')):
        parser.error('Release check requires official A2UI 1.0 cases')
    failures = []

    scene_dir = os.path.join(ROOT, "skills", "dingtalk-aicard", "references", "protocol", "examples")
    scene_files = sorted(glob.glob(os.path.join(scene_dir, "*.json")))
    expected_scenes = {"form-interaction.json", "host-action.json", "agent-run-progress.json", "agent-run.json", "data-report.json"}
    if {os.path.basename(p) for p in scene_files} != expected_scenes:
        parser.error("Public scenario set is missing files or contains unregistered files; conformance coverage must not shrink")

    try:
        valid_files = managed_valid_files(ROOT)
        pattern_cases = pattern_fixture_cases(ROOT)
        cases = fixture_cases(ROOT)
        import sync_protocol
        if sync_protocol.load_state(Path(ROOT)).get('formatVersion') == 2:
            sync_protocol.check_local_inputs(Path(ROOT))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    valid_files += sorted(glob.glob(os.path.join(ROOT, "shared", "fixtures", "regression-valid", "*.json")))
    valid_files += scene_files
    for path in valid_files:
        result = run(args.cmd, path)
        if "_error" in result:
            failures.append((os.path.basename(path), "Command failed", result["_error"]))
            continue
        errors = [d for d in result.get("diagnostics", []) if d.get("severity") == "error"]
        if not result["valid"] or errors:
            failures.append((os.path.basename(path), "Valid protocol fixture must not produce an error",
                             ",".join(d["code"] for d in errors[:3])))

    # Check every scenario message prefix structurally; this does not prove intermediate rendering is complete.
    scenario_phases = 0
    import tempfile
    for path in scene_files:
        messages = json.load(open(path, encoding="utf-8"))
        for end in range(1, len(messages)):
            if not any(m.get("updateComponents", {}).get("components") for m in messages[:end]):
                continue
            scenario_phases += 1
            with tempfile.NamedTemporaryFile("w", suffix=".json", dir=temporary_directory(),
                                             encoding="utf-8", delete=False) as handle:
                json.dump(messages[:end], handle, ensure_ascii=False)
                temp = handle.name
            try:
                result = run(args.cmd, temp)
                bad = [d for d in result.get("diagnostics", [])
                       if d["severity"] == "error"]
                if "_error" in result or not result.get("valid") or bad:
                    failures.append((os.path.basename(path), f"Invalid state after message {end}", str(bad or result)[:180]))
            finally:
                os.unlink(temp)

    for name, path, expected in cases:
        result = run(args.cmd, path)
        problem = check_expectation(result, expected)
        if problem is not None:
            failures.append((name, 'Structural expectation mismatch', problem))
        elif args.verbose:
            print(f"  ✅ {name}: valid={expected['valid']}")

    # Keep original pattern JSON as test assets; no longer extract them from Agent-facing Markdown.
    for path, fragment in pattern_cases:
        command = args.cmd + " --fragment" if fragment else args.cmd
        result = run(command, path)
        errors = [d for d in result.get("diagnostics", [])
                  if d.get("severity") == "error"]
        if "_error" in result:
            failures.append((os.path.basename(path), "Pattern regression validator command failed", result["_error"]))
        elif errors:
            failures.append((os.path.basename(path), "Pattern regression fixture must not have a structural error",
                             ",".join(d["code"] for d in errors[:3])))

    # Keep temporary files in the repository; never use system temporary directories.
    temp_root = temporary_directory()

    # Check preflight separately from general Schema; warnings must retain exit code 0.
    reference_path = Path(ROOT) / 'dws-aicard/internal/card/a2ui/testdata/preflight-references.json'
    reference_cases = json.loads(reference_path.read_text(encoding='utf-8'))
    if not reference_cases or len({case['name'] for case in reference_cases}) != len(reference_cases):
        parser.error('Reference-preflight cases are empty or contain duplicate names')
    for case in reference_cases:
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8', dir=temp_root) as handle:
            json.dump(case['messages'], handle)
            temp = handle.name
        try:
            command = [part.replace('{file}', temp) for part in shlex.split(args.cmd)]
            proc = subprocess.run(command + ['--preflight', case['mode']], capture_output=True, encoding='utf-8', timeout=30)
            result, wrapped = decode_command_result(proc)
            checked = result['preflight']
            expected_codes = (0,) if case['valid'] else ((1, 3) if wrapped else (1,))
            project = lambda ds: sorted((d['code'], d['severity'], d['pointer']) for d in ds)
            if (result.get('valid') is not True or checked['valid'] != case['valid']
                    or proc.returncode not in expected_codes
                    or project(checked['diagnostics']) != project(case['diagnostics'])):
                raise ValueError(f'Preflight expectation or exit-code mismatch: {proc.returncode} {checked}')
        except Exception as error:
            failures.append(('preflight/' + case['name'], 'Reference-preflight mismatch', str(error)[:250]))
        finally:
            os.unlink(temp)

    # ---- Official A2UI conformance cases: DingTalk open/ derives from v1.0, so most outcomes should match ----
    # Use --fragment to adapt official component and message fragments.
    official_dir = args.official or os.path.normpath(os.path.join(
        ROOT, "..", "..", "a2ui", "specification", "v1_0", "test", "cases"))
    official_total = 0
    if os.path.isdir(official_dir):
        frag_cmd = args.cmd + " --fragment" if "--fragment" not in args.cmd else args.cmd
        for path in sorted(glob.glob(os.path.join(official_dir, "*.json"))):
            suite = json.load(open(path, encoding="utf-8"))
            if suite.get("schema", "agent_to_renderer.json") != "agent_to_renderer.json":
                continue
            if suite.get("catalog", "basic") != "basic":
                continue                    # testing_catalog components are absent from the DingTalk Catalog
            suite_name = os.path.basename(path)
            for case in suite.get("tests", []):
                messages = case["data"] if isinstance(case["data"], list) else [case["data"]]
                for message in messages:
                    if isinstance(message, dict) and "version" not in message:
                        message["version"] = "v1.0"
                expected, why = case.get("valid", True), None
                for (sname, needle), (dt_valid, reason) in DIVERGENCES.items():
                    if sname in suite_name and needle in case["description"]:
                        expected, why = dt_valid, reason
                text_field_checks = [node for msg in messages for node in
                                     msg.get("updateComponents", {}).get("components", [])
                                     if node.get("component") == "TextField" and "checks" in node]
                function_expected, function_why = expected, why
                if text_field_checks:
                    expected, why = False, "DingTalk TextField does not expose checks; Button carries form rules"
                official_total += 1
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8", dir=temp_root) as handle:
                    json.dump(messages, handle)
                    temp = handle.name
                try:
                    result = run(frag_cmd, temp)
                finally:
                    os.unlink(temp)
                # Function cases originally attached checks to TextField; use Button for the same function assertions,
                # so removing that field does not reduce them all to unknown_property and lose function coverage.
                if text_field_checks and "function_catalog_validation" in suite_name:
                    adapted = copy.deepcopy(messages)
                    for msg in adapted:
                        for node in msg.get("updateComponents", {}).get("components", []):
                            if node.get("component") == "TextField" and "checks" in node:
                                node.pop("label", None)
                                node.pop("value", None)
                                node.update(component="Button", child="validationLabel",
                                            action={"event": {"name": "submit"}})
                    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8", dir=temp_root) as handle:
                        json.dump(adapted, handle)
                        adapted_path = handle.name
                    try:
                        adapted_result = run(frag_cmd, adapted_path)
                    finally:
                        os.unlink(adapted_path)
                    official_total += 1
                    if "_error" in adapted_result or adapted_result["valid"] != function_expected:
                        failures.append((f"official-function/Button/{suite_name}", case["description"],
                                         str(adapted_result.get("_error") or adapted_result.get("diagnostics"))[:180]))
                if "_error" in result:
                    failures.append((f"official/{suite_name}", case["description"], result["_error"]))
                    continue
                got = result["valid"]
                if got != expected:
                    tag = f"DingTalk divergence: {why}" if why else ("upstream expects valid" if case.get("valid", True) else "upstream expects invalid")
                    failures.append((f"official/{suite_name}", case["description"],
                                     f"{tag}, expected {'accepted' if expected else 'rejected'}, got {'accepted' if got else 'rejected'}"))
                elif args.verbose and why:
                    print(f"  ✅ official/{case['description'][:48]}: DingTalk result ({why})")
    else:
        print(f"Official A2UI case directory not found: {official_dir}; official conformance checks skipped")

    if args.require_official and official_total == 0:
        failures.append(('Official baseline', 'Release check ran no official cases', official_dir))

    # ---- DWS Skill must be generated from skills/dingtalk-aicard/: references/ byte-identical and SKILL.md following generation rules ----
    drift = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_dws_skill.py"), "--check"],
                           capture_output=True, text=True)
    dws_skill_total = 1
    if drift.returncode != 0:
        failures.append(("dws-aicard/skills", "DWS Skill differs from skills/dingtalk-aicard/",
                         (drift.stdout + drift.stderr).strip().splitlines()[-1][:120]))
    elif args.verbose:
        print("  ✅ dws-aicard/skills/multi/dingtalk-aicard matches skills/dingtalk-aicard/")

    # ---- references/index/ must be generated from the protocol: rebuild in memory and compare bytes ----
    idx = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_index.py"), "--check"],
                         capture_output=True, text=True)
    index_total = 1
    if idx.returncode != 0:
        failures.append(("references/index", "Index differs from protocol", (idx.stdout + idx.stderr).strip().splitlines()[-1][:120]))
    elif args.verbose:
        print("  ✅ references/index/ matches the protocol")

    go_assets = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_go_assets.py"), "--check"],
                               capture_output=True, text=True)
    if go_assets.returncode:
        failures.append(('Go assets', 'Generated assets have drifted', (go_assets.stdout + go_assets.stderr).strip()[:300]))

    # ---- --explain must cover all components, functions, and Token types with fields/arguments/enums and serializable examples ----
    import importlib.util
    lint_path = os.path.join(ROOT, "skills", "dingtalk-aicard", "scripts", "aicard_lint.py")
    spec_ = importlib.util.spec_from_file_location("aicard_lint", lint_path)
    lint_mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(lint_mod)
    proto = lint_mod.Protocol(os.path.join(ROOT, "skills", "dingtalk-aicard", "references", "protocol"))
    explain_names = list(proto.components) + list(proto.functions) + list(lint_mod.TOKEN_TYPES) + lint_mod.common_type_names(proto)
    for token in lint_mod.TOKEN_TYPES:
        explain_names += [item["name"] for item in lint_mod.explain(proto, token)["items"]]
    explain_total = len(explain_names)
    for name in explain_names:
        try:
            expected = lint_mod.explain(proto, name)
            if args.explain_cmd == DEFAULT_EXPLAIN_CMD:
                result = expected
            else:
                command = [part.replace("{name}", name) for part in shlex.split(args.explain_cmd)]
                proc = subprocess.run(command, capture_output=True, text=True, timeout=30)
                if proc.returncode:
                    raise ValueError(proc.stderr or proc.stdout)
                result, _ = decode_command_result(proc)
            check_explanation(result, expected)
            if result.get("kind") == "component":
                ds, _ = lint_mod.lint(result["example"], proto, fragment=True)
                bad = [d for d in ds if d["severity"] == "error"]
                if bad:
                    failures.append((f"explain/{name}", "Synthesized example violates the protocol", str(bad)[:180]))
                if args.cmd != DEFAULT_CMD:
                    with tempfile.NamedTemporaryFile("w", suffix=".json", dir=temporary_directory(),
                                                     encoding="utf-8", delete=False) as handle:
                        json.dump(result["example"], handle, ensure_ascii=False)
                        example_file = handle.name
                    try:
                        candidate = run(args.cmd + " --fragment", example_file)
                        if candidate.get("_error") or not candidate.get("valid"):
                            raise ValueError("Validator under test rejected this implementation's explain example: " + str(candidate)[:180])
                    finally:
                        os.unlink(example_file)
            json.dumps(result, ensure_ascii=False)
            body = {"component": result.get("fields"), "function": result.get("args"),
                    "token": result.get("items"), "token-item": result.get("type"), "type": result.get("fields")}.get(result["kind"])
            if result["kind"] == "unknown" or body is None:
                failures.append((f"explain/{name}", "--explain result is incomplete", json.dumps(result, ensure_ascii=False)[:100]))
            elif result["kind"] not in ("token", "token-item") and not isinstance(result.get("example"), (dict, list, str, int, float, bool)):
                failures.append((f"explain/{name}", "--explain lacks a synthesized example", ""))
        except Exception as error:               # Treat every exception as a mismatch; do not swallow it
            failures.append((f"explain/{name}", "--explain crashed", repr(error)[:120]))
    if args.verbose:
        print(f"  ✅ --explain covers {explain_total} entries (components {len(proto.components)}, functions {len(proto.functions)}, Token types {len(lint_mod.TOKEN_TYPES)}, common types {len(lint_mod.common_type_names(proto))})")

    total = len(valid_files) + len(cases) + len(pattern_cases) + official_total + dws_skill_total + explain_total + index_total + scenario_phases + len(reference_cases)
    print(f"Conformance baseline: {total - len(failures)}/{total} passed "
          f"({len(valid_files)} valid fixtures, {len(cases)} structural negative and boundary cases, "
          f"{len(pattern_cases)} pattern regressions, {official_total} official cases including "
          f"{len(DIVERGENCES)} named divergences and the TextField.checks field difference, "
          f"one DWS Skill consistency check, {explain_total} explain entries, one index check, "
          f"{scenario_phases} scenario prefixes, and {len(reference_cases)} reference-preflight cases)")
    for name, reason, detail in failures:
        print(f"  ✗ {name}: {reason} → {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
