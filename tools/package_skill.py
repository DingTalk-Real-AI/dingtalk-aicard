#!/usr/bin/env python3
"""Package the standalone Skill as a downloadable, reproducible zip.

The archive holds a single top-level `dingtalk-aicard/` directory, so unzipping it
into an agent's skills directory (for example `~/.claude/skills/`) installs the Skill
directly. Only files tracked by git under `skills/dingtalk-aicard/` are included,
which keeps local virtual environments, caches and evaluation output out of the
release. Add new Skill files to git before packaging; file contents are read from
the working tree, not from a commit. Entries are sorted and timestamped at a fixed
date, so identical contents and permissions produce byte-identical archives in
the same Python/zlib environment.

Output must be outside the Skill directory and must not alias an input file.
Every build validates a temporary archive before atomically replacing the output;
failed builds preserve any existing archive. Output symlinks are rejected.

Usage:
  python3 tools/package_skill.py            # write dist/dingtalk-aicard.zip
  python3 tools/package_skill.py --check    # also recheck the published archive
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "dingtalk-aicard"
NAME = "dingtalk-aicard"
DIST = ROOT / "dist"
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
REQUIRED = ("SKILL.md", "LICENSE", "NOTICE", "scripts/setup_env.py", "scripts/aicard_lint.py",
            "scripts/a2ui-validation-rules.json", "references")


def tracked_files() -> list[str]:
    """Paths relative to the Skill directory, as tracked by git."""
    out = subprocess.run(["git", "ls-files", "-z", "--", str(SKILL.relative_to(ROOT))],
                         cwd=ROOT, capture_output=True, check=True).stdout
    prefix = f"{SKILL.relative_to(ROOT).as_posix()}/"
    files = sorted(p[len(prefix):] for p in out.decode("utf-8").split("\0") if p)
    missing = [p for p in files if not (SKILL / p).is_file()]
    if missing:
        raise SystemExit(f"Tracked files missing from the working tree: {', '.join(missing)}")
    return files


def build(target: Path) -> list[str]:
    files = tracked_files()
    # Check both lexical and resolved paths, including symlinked parent directories.
    absolute = Path(os.path.abspath(target))
    resolved = target.resolve()
    if (absolute.is_relative_to(SKILL.absolute())
            or resolved.is_relative_to(SKILL.resolve())
            or target.is_symlink()
            or any(resolved == (SKILL / rel).resolve()
                   or (target.exists() and target.samefile(SKILL / rel)) for rel in files)):
        raise SystemExit("Archive output must be outside the Skill directory and must not alias an input file or be a symlink")
    target = resolved
    target.parent.mkdir(parents=True, exist_ok=True)
    # A sibling temporary file keeps replacement on the same filesystem.
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output:
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for rel in files:
                    info = zipfile.ZipInfo(f"{NAME}/{rel}", date_time=FIXED_TIME)
                    executable = (SKILL / rel).stat().st_mode & 0o111
                    info.external_attr = (0o100755 if executable else 0o100644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, (SKILL / rel).read_bytes(), compresslevel=9)
        check(temporary, files)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return files


def check(target: Path, files: list[str]) -> None:
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        bad_entry = archive.testzip()
    if bad_entry is not None:
        raise SystemExit(f"Archive CRC check failed: {bad_entry}")
    roots = {name.split("/", 1)[0] for name in names}
    if roots != {NAME}:
        raise SystemExit(f"Archive must contain one top-level '{NAME}/' directory, found {sorted(roots)}")
    inside = {name[len(NAME) + 1:] for name in names}
    if inside != set(files) or len(names) != len(files):
        raise SystemExit("Archive entries differ from the tracked Skill files")
    for required in REQUIRED:
        if not any(entry == required or entry.startswith(required + "/") for entry in inside):
            raise SystemExit(f"Archive is missing {required}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DIST / f"{NAME}.zip", help="archive path")
    parser.add_argument("--check", action="store_true", help="also recheck the published archive layout and CRC")
    args = parser.parse_args()

    files = build(args.out)
    if args.check:
        check(args.out, files)
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(f"{args.out} {len(files)} files, {args.out.stat().st_size} bytes, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
