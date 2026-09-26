#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Install dws-aicard/ into the official dingtalk-workspace-cli repository.

dws-aicard/ mirrors the official source tree; each file is installed at the same relative path.
This is not a recursive copy. It first verifies the target repository and required integration symbols,
then uses the install ledger to classify files as additions, updates to our prior install, or conflicts,
and, when Go is available, verifies build, gofmt, native commands, and validator tests.

    python3 tools/install_to_dws.py                     # install into sibling ../dingtalk-workspace-cli
    python3 tools/install_to_dws.py --dws /path/to/dws  # specify the official repository
    python3 tools/install_to_dws.py --check             # inspect and print the plan without writing
    python3 tools/install_to_dws.py --uninstall         # remove files installed by this tool using its ledger
    python3 tools/install_to_dws.py --force             # also overwrite or remove target files modified after installation
    python3 tools/install_to_dws.py --skip-verify       # skip go build, go vet, go test, and gofmt

The private, Git-ignored shared/.dws-install.json ledger records the target, its HEAD at installation, and per-file SHA-256.
Without a ledger, existing target files identical to source are treated as installed.
Exit codes: 0 success; 1 failed precheck, conflict, or verification (with explicit notice if files were written); 2 usage error.

The target must include aicard in internal/app/root.go's builtinCommandNames.
This installer checks that integration point but does not rewrite the shared root.
Use a clean, disposable checkout for upgrades: installation is not transactional.
--uninstall deletes installed files; it does not restore their previous contents.
Managed target paths must be relative and contain no symlinks, including links
within the checkout. --force does not bypass this safety check. Do not modify
the target concurrently while installation or uninstallation is running.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "dws-aicard")
LEDGER = os.path.join(ROOT, "shared", ".dws-install.json")
DEFAULT_DWS = os.path.normpath(os.path.join(ROOT, "..", "dingtalk-workspace-cli"))
MODULE = "github.com/DingTalk-Real-AI/dingtalk-workspace-cli"
GO_DEPENDENCIES = ["github.com/santhosh-tekuri/jsonschema/v6@v6.0.2", "github.com/dlclark/regexp2@v1.11.5"]
SKILL_REL = os.path.join("skills", "multi", "dingtalk-aicard")
SKILL_MAX_BYTES = 10_342  # 10.1 KiB, rounded down to whole bytes.
JUNK = {"__pycache__", ".pyc"}
LOCAL_AUDIT = "internal/cli/schema_parameter_mapping_completeness_test.go"
LOCAL_AUDIT_ANCHOR = "func TestDeliveryCatalogLocalInterfacesAreExactAndReviewed(t *testing.T) {\n\twantReasons := map[string]string{\n"
LOCAL_AUDIT_ENTRIES = ('\t\t"aicard.explain":     "Reads the protocol index generated into the binary",\n'
                       '\t\t"aicard.lint":        "Uses the protocol embedded in the binary; does not access the network or execute card functions",\n')
# Match these exact localized legacy lines before replacing them; translating them would break migration.
LEGACY_LOCAL_AUDIT_ENTRIES = ('\t\t"aicard.explain":     "读取随二进制生成的协议索引",\n'
                              '\t\t"aicard.lint":        "使用二进制内嵌协议，不联网或执行卡片函数",\n')


def local_audit_content(dws: str, *, remove: bool = False) -> tuple[str, bool]:
    """Add, upgrade, or remove only two audited zero-RPC interface declarations; do not own the target test file."""
    body = read(os.path.join(dws, LOCAL_AUDIT))
    if body.count(LOCAL_AUDIT_ANCHOR) != 1:
        raise ValueError("Local-interface audit integration point changed: " + LOCAL_AUDIT)
    entry_pattern = re.compile(r'^[ \t]*"(aicard\.(?:explain|lint))"[ \t]*:[ \t]*("(?:[^"\\]|\\.)*")[ \t]*,[ \t]*$', re.M)
    entries = list(entry_pattern.finditer(body))
    expected = {match.group(1): json.loads(match.group(2))
                for match in entry_pattern.finditer(LOCAL_AUDIT_ENTRIES)}
    legacy = {match.group(1): json.loads(match.group(2))
              for match in entry_pattern.finditer(LEGACY_LOCAL_AUDIT_ENTRIES)}
    if entries:
        actual = {match.group(1): json.loads(match.group(2)) for match in entries}
        if (len(entries) != 2 or actual not in (expected, legacy)
                or len(re.findall(r'"aicard\.(?:explain|lint)"', body)) != 2):
            raise ValueError("The local-interface audit has different AICard declarations; inspect manually: " + LOCAL_AUDIT)
        if actual == expected and not remove:
            return body, False
        # Remove only the owned lines, preserving all unrelated formatting.
        for match in reversed(entries):
            end = match.end() + (body[match.end():match.end() + 1] == "\n")
            body = body[:match.start()] + body[end:]
        if not remove:
            body = body.replace(LOCAL_AUDIT_ANCHOR, LOCAL_AUDIT_ANCHOR + LOCAL_AUDIT_ENTRIES, 1)
        return body, True
    if '"aicard.lint"' in body or '"aicard.explain"' in body:
        raise ValueError("The local-interface audit has different AICard declarations; inspect manually: " + LOCAL_AUDIT)
    if remove:
        return body, False
    return body.replace(LOCAL_AUDIT_ANCHOR, LOCAL_AUDIT_ANCHOR + LOCAL_AUDIT_ENTRIES, 1), True

# Required target integration points: file, expected pattern (None for the token-aware root check), and dependent feature. A missing point means upstream changed the integration,
# so inspect the integration before installation; forcing a copy cannot fix it.
SEAMS = [
    ("internal/shortcut/chatmsg/card_update.go", r"^func NormalizeCardBizID\(\w+ string\) \(string, error\)",
     "aicard_run.go", "Server-issued card update ID normalization"),
    ("internal/app/root.go", None,
     "aicard root discovery", "aicard in the built-in visibility and reserved-command base set"),
    ("internal/corecmd/fortest.go", r"^func ExecuteContextCForTest\(",
     "aicard_test.go", "Command test execution with runtime contract validation"),
    ("internal/helpers/interfaces.go", r"^func RegisterPublicNamed\(name string, factory Factory\)",
     "register_aicard.go", "Handwritten RegisterPublicNamed registration path"),
    ("internal/helpers/register_products.go", r"^type wukongHandler struct",
     "register_aicard.go", "wukongHandler{name, buildFn} adapter"),
    ("internal/helpers/helpers.go", r"^func newGroupCommand\(command \*cobra\.Command\) \*cobra\.Command",
     "aicard.go", "Group-command navigation policy"),
    ("internal/helpers/helpers.go", r"^\s*groupRunE\s*=\s*cmdutil\.GroupRunE",
     "aicard.go", "Group-command RunE"),
    ("internal/helpers/leaf.go", r"^func DeclareLeafMetadata\(cmd \*cobra\.Command, spec LeafSpec\)",
     "aicard.go", "Metadata declaration pattern (Safety + Contract on an existing command)"),
    ("internal/helpers/leaf.go", r"^type LeafContract = corecmd\.ContractDecl",
     "aicard.go", "LeafContract alias"),
    ("internal/corecmd/runtimeannotate/constraints.go", r"^func AnnotateRuntimeConstraints\(",
     "aicard.go", "lint file, self-check, and fragment constraints"),
    ("internal/corecmd/runtimeannotate/annotate.go", r"^func AnnotateRuntimeRequiredFlags\(",
     "aicard.go", "Required-flag annotation (preview --file)"),
    ("internal/corecmd/runtimeannotate/constraints.go", r"^type RuntimeSchemaConstraints (?:= contract\.RuntimeSchemaConstraints|struct\s*\{)",
     "aicard.go", "Constraint structure"),
    ("internal/corecmd/contract/types.go", r"^type ToolIdentitySpec struct",
     "aicard.go", "Contract.Identity"),
    ("internal/corecmd/contract/types.go", r"^type SelectionSpec struct",
     "aicard.go", "Contract.Selection"),
    ("internal/corecmd/contract/types.go", r"^type InterfaceSpec struct",
     "aicard.go", "Contract.Interface"),
    ("internal/corecmd/contract/types.go", r"^type SafetySpec struct",
     "aicard.go", "Safety declaration"),
    ("internal/corecmd/contract/product.go", r"^func RegisterProductDecl\(",
     "aicard.go", "Product-level Agent routing declaration"),
    ("internal/corecmd/contract/product.go", r"^type HelpDocumentation struct",
     "aicard.go", "Typed product documentation links"),
    ("internal/corecmd/contract/product.go", r"^\s*Documentation\s+\[\]HelpDocumentation",
     "aicard.go", "Product help documentation field"),
    ("internal/corecmd/contract/product.go", r"^func SkillDocumentation\(label, skill, relativePath string\) HelpDocumentation",
     "aicard.go", "Embedded Skill documentation link constructor"),
    ("internal/corecmd/runtimeannotate/annotate.go", r"^func AnnotateRuntimePositionals\(",
     "aicard.go", "explain name positional argument"),
    ("skills/embed.go", r"go:embed all:mono all:multi",
     "skills/multi/dingtalk-aicard/", "Skills embedded by directory wildcard; no registration required"),
    ("test/unit/mono_multi_skill_content_test.go", r"G2: %s: metadata\.category",
     "skills/multi/dingtalk-aicard/SKILL.md", "Multi-Skill content contract G1-G5 (this script prechecks G1/G2)"),
    (".changes/README.md", r"category",
     ".changes/aicard-command-domain.md", "PR change-fragment contract"),
]


def sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def walk_source() -> dict[str, str]:
    """Inventory distributable files; report and skip Finder metadata, reject bytecode files."""
    out = {}
    for base, dirs, names in os.walk(SRC):
        dirs[:] = sorted(d for d in dirs if d not in JUNK)
        for name in sorted(names):
            if name == ".DS_Store":
                print(f"  Skipping local Finder metadata: {os.path.relpath(os.path.join(base, name), ROOT)}")
                continue
            if name in JUNK or name.endswith(".pyc"):
                raise SystemExit(f"dws-aicard/ contains a file that must not be distributed: {os.path.relpath(os.path.join(base, name), ROOT)}")
            full = os.path.join(base, name)
            out[os.path.relpath(full, SRC).replace(os.sep, "/")] = full
    if not out:
        raise SystemExit("dws-aicard/ is empty")
    return out


def load_ledger() -> dict:
    if not os.path.exists(LEDGER):
        return {"targets": {}}
    try:
        data = json.loads(read(LEDGER))
    except ValueError as exc:
        raise SystemExit(f"Install ledger is invalid: {LEDGER}: {exc}")
    return data if isinstance(data, dict) and "targets" in data else {"targets": {}}


def save_ledger(ledger: dict) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def git_head(dws: str) -> str:
    try:
        return subprocess.run(["git", "-C", dws, "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


# ---------- Pre-installation checks ----------

def has_builtin_aicard(source: str) -> bool:
    """Recognize the required literal map entry without matching comments or string contents."""
    # Keep literals as single tokens, including raw strings with embedded newlines.
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`|//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_][A-Za-z_0-9]*|[^\s]', source)
    tokens = [token for token in tokens if not token.startswith(('//', '/*'))]
    declaration = ['var', 'builtinCommandNames', '=', 'map', '[', 'string', ']', 'bool', '{']
    scope_depth = 0
    for start in range(len(tokens)):
        if tokens[start] == '{':
            scope_depth += 1
        elif tokens[start] == '}':
            scope_depth -= 1
        if scope_depth != 0 or tokens[start:start + len(declaration)] != declaration:
            continue
        depth = 1
        for index in range(start + len(declaration), len(tokens)):
            token = tokens[index]
            if (depth == 1 and tokens[index - 1] in ('{', ',')
                    and tokens[index:index + 3] == ['"aicard"', ':', 'true']
                    and tokens[index + 3:index + 4] in ([','], ['}'])):
                return True
            if token == '{':
                depth += 1
            elif token == '}':
                depth -= 1
                if depth == 0:
                    break
    return False


def validate_target_paths(dws: str, paths) -> None:
    """Reject unsafe paths before any target mutation, even when force is enabled."""
    root = os.path.realpath(dws)
    for rel in sorted(set(paths) | {LOCAL_AUDIT, 'go.mod', 'go.sum'}):
        parts = rel.split('/')
        if os.path.isabs(rel) or any(part in ('', '.', '..') for part in parts):
            raise ValueError(f"Unsafe target path: {rel!r}; expected a normalized relative path")
        path = root
        for part in parts:
            path = os.path.join(path, part)
            # islink also detects dangling links; exists would miss them.
            if os.path.islink(path):
                raise ValueError(f"Unsafe target path: {rel!r}; symlink at {path}")
        if os.path.commonpath([root, os.path.realpath(path)]) != root:
            raise ValueError(f"Unsafe target path: {rel!r}; resolves outside {root}")


def preflight_target(dws: str) -> list[str]:
    problems = []
    gomod = os.path.join(dws, "go.mod")
    if not os.path.isfile(gomod):
        return [f"{dws} is not a Go repository: go.mod is missing"]
    first = read(gomod).splitlines()[0].strip() if read(gomod).strip() else ""
    if first != f"module {MODULE}":
        return [f"{dws} is not the official DWS repository: first go.mod line is {first!r}; expected 'module {MODULE}'"]
    for rel, pattern, needed_by, why in SEAMS:
        full = os.path.join(dws, rel)
        if not os.path.isfile(full):
            problems.append(f"Integration-point file is missing: {rel} ({needed_by} requires: {why})")
            continue
        source = read(full)
        matched = has_builtin_aicard(source) if pattern is None else re.search(pattern, source, re.M)
        if not matched:
            expected = 'active top-level builtinCommandNames entry "aicard": true' if pattern is None else f'pattern /{pattern}/'
            problems.append(f"Integration-point symbol mismatch: {expected} not found in {rel} ({needed_by} requires: {why})")
    return problems


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n([\s\S]*?)\n---", text)
    if not m:
        return {}
    fm, bins, in_bins = {}, [], False
    for line in m.group(1).splitlines():
        if re.match(r"^\s+bins:\s*$", line):
            in_bins = True
            continue
        if in_bins:
            item = re.match(r"^\s+-\s+(\S+)", line)
            if item:
                bins.append(item.group(1))
                continue
            in_bins = False
        for key in ("name", "description"):
            hit = re.match(rf"^{key}:\s*(.+?)\s*$", line)
            if hit:
                fm[key] = hit.group(1)
        hit = re.match(r"^\s+category:\s*(\S+)", line)
        if hit:
            fm["category"] = hit.group(1)
        hit = re.match(r"^\s+internal:\s*(\S+)", line)
        if hit:
            fm["internal"] = hit.group(1)
    fm["bins"] = bins
    return fm


def preflight_source() -> list[str]:
    """Source checks: generated artifacts are current and the Skill meets G1/G2 (directory name, frontmatter, references/)."""
    problems = []
    drift = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_dws_skill.py"), "--check"],
                           capture_output=True, text=True)
    if drift.returncode != 0:
        problems.append("DWS Skill differs from skills/dingtalk-aicard/; run python3 tools/build_dws_skill.py first: \n"
                        + (drift.stdout + drift.stderr).strip())
    native = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_go_assets.py"), "--check"],
                            capture_output=True, text=True)
    if native.returncode != 0:
        problems.append("Go index has drifted: " + (native.stdout + native.stderr).strip())
    skill_dir = os.path.join(SRC, SKILL_REL)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return problems + [f"Missing {SKILL_REL}/SKILL.md"]
    if os.path.getsize(skill_md) > SKILL_MAX_BYTES:
        problems.append(f"DWS SKILL.md exceeds the {SKILL_MAX_BYTES}-byte entrypoint budget")
    if not os.path.isdir(os.path.join(skill_dir, "references")):
        problems.append("G1: Skill lacks a references/ directory")
    fm = parse_frontmatter(read(skill_md))
    name = os.path.basename(skill_dir)
    if not name.startswith("dingtalk-"):
        problems.append(f"G1: Multi-Skill directory name must start with dingtalk-*; got {name}")
    if fm.get("name") != name:
        problems.append(f"G2: frontmatter name {fm.get('name')!r} must match directory name {name!r}")
    if not fm.get("description"):
        problems.append("G2: frontmatter description is empty")
    if fm.get("category") != "product":
        problems.append(f"G2: metadata.category must be product; got {fm.get('category')!r}")
    if "dws" not in fm.get("bins", []):
        problems.append("G2: metadata.requires.bins must include dws")
    if fm.get("internal", "false") not in ("false", "no"):
        problems.append("Public discovery requires product Skill metadata.internal to be false or absent")
    return problems


# ---------- Plan ----------

def make_plan(src: dict[str, str], dws: str, owned: dict[str, str]) -> list[tuple[str, str, str]]:
    """One entry per file: (relative path, action, reason). Actions: add, update, same, conflict, remove, remove-conflict."""
    validate_target_paths(dws, set(src) | set(owned))
    plan = []
    for rel, full in src.items():
        target = os.path.join(dws, rel)
        want = sha256(full)
        if not os.path.exists(target):
            plan.append((rel, "add", ""))
        elif not os.path.isfile(target):
            plan.append((rel, "conflict", "Target is a directory or special file"))
        else:
            have = sha256(target)
            if have == want:
                plan.append((rel, "same", ""))
            elif owned.get(rel) == have:
                plan.append((rel, "update", "Replace an older version installed by this tool"))
            else:
                plan.append((rel, "conflict", "Target contains a different file with the same name (upstream or another author)"))
    for rel, recorded in sorted(owned.items()):
        if rel in src:
            continue
        target = os.path.join(dws, rel)
        if not os.path.isfile(target):
            continue
        if sha256(target) == recorded:
            plan.append((rel, "remove", "Source no longer contains this file; remove the older install"))
        else:
            plan.append((rel, "remove-conflict", "Source no longer contains this file, but the target copy has changed"))
    return plan


def prune_empty_dirs(dws: str, rel: str) -> None:
    """Prune empty parent directories after file deletion without crossing the target root or removing nonempty directories."""
    directory = os.path.dirname(os.path.join(dws, rel))
    root = os.path.abspath(dws)
    while os.path.abspath(directory) != root and os.path.isdir(directory) and not os.listdir(directory):
        os.rmdir(directory)
        directory = os.path.dirname(directory)


# ---------- Verification ----------

def verify(dws: str, go_files: list[str]) -> list[str]:
    failures = []
    go = shutil.which("go")
    gofmt = shutil.which("gofmt") or (os.path.join(os.path.dirname(go), "gofmt") if go else None)
    if gofmt and os.path.exists(gofmt) and go_files:
        res = subprocess.run([gofmt, "-l"] + go_files, capture_output=True, text=True, cwd=dws)
        if res.returncode != 0:
            failures.append("gofmt failed: " + (res.stderr or res.stdout).strip())
        elif res.stdout.strip():
            failures.append("gofmt reported differences (edit the source in this repository's dws-aicard/, not the target):\n  " + res.stdout.strip().replace("\n", "\n  "))
        else:
            print("  gofmt -l          clean")
    else:
        print("  gofmt             not installed locally; skipped")
    if not go:
        print("  go                Go is not installed locally; run build verification on a machine with Go:")
        print(f"                    cd {dws} && go build ./... && go vet ./internal/helpers/ && go test ./internal/card/a2ui/ -count=1 && go test ./internal/helpers/ -run Aicard -count=1 && go test ./internal/app/ -run '^TestCrossPlatformCoverageAicardRootDiscovery$' -count=1")
        return ["Go is unavailable, so build verification cannot complete; specify --skip-verify explicitly to copy only"]
    steps = [
        ("go build ./...", ["go", "build", "./..."]),
        ("go vet ./internal/helpers/", ["go", "vet", "./internal/helpers/"]),
        ("go test ./internal/card/a2ui/", ["go", "test", "./internal/card/a2ui/", "-count=1"]),
        ("go test ./internal/helpers/ -run Aicard", ["go", "test", "./internal/helpers/", "-run", "Aicard", "-count=1"]),
        ("go test ./internal/app/ (AICard root discovery)", ["go", "test", "./internal/app/", "-run", "^TestCrossPlatformCoverageAicardRootDiscovery$", "-count=1"]),
    ]
    for label, cmd in steps:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=dws, timeout=900)
        except subprocess.TimeoutExpired:
            failures.append(f"{label} timed out (900s)")
            break
        if res.returncode == 0:
            print(f"  {label:<44}passed")
        else:
            failures.append(f"{label} failed: \n" + (res.stdout + res.stderr).strip())
            break
    return failures


def print_next_steps(dws: str) -> None:
    print("\nNext steps in the official repository (Go toolchain required):")
    print("  make fmt && make lint")
    print("  make update-interface-baseline   # add aicard to the root command snapshot test/fixtures/cli-interface-baseline.txt")
    print("  make skill-command-integrity     # resolve DWS command references in Skill content to real commands")
    print("  make skill-mono-multi-content    # Multi-Skill content contract G1-G5")
    print("  make test")
    print("When opening a PR, .changes/aicard-command-domain.md is already installed with the mirror. CI Coverage requires 100% coverage of changed code;")
    print("  include command and internal/card/a2ui tests, and run full conformance with a newly built binary.")


# ---------- Main flow ----------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install dws-aicard/ into the official DWS repository")
    parser.add_argument("--dws", default=DEFAULT_DWS, help=f"Official repository path (default: {DEFAULT_DWS})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Inspect and print the plan without writing files")
    mode.add_argument("--uninstall", action="store_true", help="remove files installed by this tool using its ledger")
    parser.add_argument("--force", action="store_true", help="also overwrite or remove target files modified after installation")
    parser.add_argument("--skip-verify", action="store_true", help="skip go build, go vet, go test, and gofmt")
    args = parser.parse_args(argv)

    dws = os.path.realpath(args.dws)
    if not os.path.isdir(dws):
        print(f"Target does not exist: {dws}", file=sys.stderr)
        return 2
    ledger = load_ledger()
    entry = ledger["targets"].get(dws, {})
    owned: dict[str, str] = dict(entry.get("files", {}))
    head = git_head(dws)
    print(f"Target: {dws}" + (f" (HEAD {head})" if head else ""))
    if entry:
        print(f"Ledger: last installed {entry.get('installedAt')}, HEAD {entry.get('targetHead')}, {len(owned)} files")
    else:
        print("Ledger: none (first install or different machine; identical target files count as installed)")

    if args.uninstall:
        return uninstall(dws, owned, ledger, args.force)

    src = walk_source()
    try:
        validate_target_paths(dws, set(src) | set(owned))
    except ValueError as exc:
        print(f"{exc}; no files were written.")
        return 1

    print("\nPre-installation checks")
    problems = preflight_target(dws)
    try:
        audit_body, audit_changed = local_audit_content(dws)
    except (OSError, ValueError) as exc:
        problems.append(str(exc))
    print(f"  Target repository and {len(SEAMS)} integration points     {'passed' if not problems else 'failed'}")
    src_problems = preflight_source()
    print(f"  Source-generated artifacts and Skill gates    {'passed' if not src_problems else 'failed'}")
    for p in problems + src_problems:
        print("  ✗ " + p.replace("\n", "\n    "))
    if problems or src_problems:
        return 1

    try:
        plan = make_plan(src, dws, owned)
    except ValueError as exc:
        print(f"{exc}; no files were written.")
        return 1
    counts = {}
    for _, action, _ in plan:
        counts[action] = counts.get(action, 0) + 1
    print("\nPlan (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")")
    for rel, action, note in plan:
        mark = {"add": "+", "update": "~", "same": "=", "remove": "-", "conflict": "!", "remove-conflict": "!"}[action]
        print(f"  {mark} {action:<16}{rel}" + (f"    {note}" if note else ""))
    conflicts = [p for p in plan if p[1] in ("conflict", "remove-conflict")]
    if conflicts and not args.force:
        print(f"\n{len(conflicts)} conflicts; no files were written. Inspect the target before choosing --force to overwrite or delete.")
        return 1
    print(f"  Local-interface audit: {'added lint and explain' if audit_changed else 'already matches'} ({LOCAL_AUDIT})")
    if args.check:
        print("  Go requires: " + " ".join(GO_DEPENDENCIES))
        print("\n--check: no files written.")
        return 0

    print("\nWriting")
    installed: dict[str, str] = {}
    for rel, action, _ in plan:
        target = os.path.join(dws, rel)
        if action in ("add", "update", "same", "conflict"):
            if action == "conflict" and os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if action != "same":
                shutil.copyfile(src[rel], target)
            installed[rel] = sha256(target)
        elif action in ("remove", "remove-conflict"):
            os.remove(target)
            prune_empty_dirs(dws, rel)
    print(f"  Wrote {counts.get('add', 0) + counts.get('update', 0) + counts.get('conflict', 0)} module files, removed {counts.get('remove', 0) + counts.get('remove-conflict', 0)} files, "
          f"unchanged {counts.get('same', 0)} files")
    if audit_changed:
        with open(os.path.join(dws, LOCAL_AUDIT), "w", encoding="utf-8") as handle:
            handle.write(audit_body)
    ledger["targets"][dws] = {
        "installedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "targetHead": head,
        "source": os.path.relpath(SRC, ROOT),
        "files": installed,
        "localInterfaceAuditOwned": audit_changed or entry.get("localInterfaceAuditOwned", False),
    }
    save_ledger(ledger)
    print(f"  Ledger updated: {os.path.relpath(LEDGER, ROOT)}")

    # Add only native-validator dependencies; do not copy go.mod or overwrite existing target dependencies.
    if shutil.which("go"):
        result = subprocess.run(["go", "get", *GO_DEPENDENCIES, "./internal/card/a2ui"], cwd=dws)
        if result.returncode:
            print("Files were written, but Go dependency setup failed; fix the environment and rerun.")
            return 1
    elif not args.skip_verify:
        print("Files were written, but Go is unavailable for native dependencies or build verification.")
        return 1
    if args.skip_verify:
        print_next_steps(dws)
        return 0
    print("\nVerification")
    go_files = [os.path.join(dws, rel) for rel in installed if rel.endswith(".go")]
    failures = verify(dws, go_files)
    for f in failures:
        print("  ✗ " + f.replace("\n", "\n    "))
    print_next_steps(dws)
    if failures:
        print("\nFiles were written to the target, but verification failed. Fix the source and rerun, or restore the pre-install checkout. --uninstall deletes installed files; it does not restore the previous version.")
        return 1
    return 0


def uninstall(dws: str, owned: dict[str, str], ledger: dict, force: bool) -> int:
    if not owned:
        print("The ledger has no record for this target; nothing to uninstall.")
        return 1
    try:
        validate_target_paths(dws, owned)
    except ValueError as exc:
        print(f"{exc}; no files were removed or changed.")
        return 1
    if ledger["targets"][dws].get("localInterfaceAuditOwned"):
        try:
            body, changed = local_audit_content(dws, remove=True)
        except (OSError, ValueError) as exc:
            print(f"Audit declarations changed; no files were removed: {exc}")
            return 1
        if changed:
            with open(os.path.join(dws, LOCAL_AUDIT), "w", encoding="utf-8") as handle:
                handle.write(body)
        ledger["targets"][dws]["localInterfaceAuditOwned"] = False
    kept = []
    removed = 0
    for rel, recorded in sorted(owned.items()):
        target = os.path.join(dws, rel)
        if not os.path.isfile(target):
            continue
        if sha256(target) != recorded and not force:
            kept.append(rel)
            continue
        os.remove(target)
        prune_empty_dirs(dws, rel)
        removed += 1
    print(f"Removed {removed} files")
    for rel in kept:
        print(f"  ! Kept {rel}: modified after installation; use --force only if discarding those edits is intended")
    if kept:
        ledger["targets"][dws]["files"] = {rel: owned[rel] for rel in kept}
    else:
        ledger["targets"].pop(dws, None)
    save_ledger(ledger)
    return 1 if kept else 0


if __name__ == "__main__":
    sys.exit(main())
