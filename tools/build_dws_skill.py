#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Generate the DWS Skill from the standalone Skill.

skills/dingtalk-aicard/ is the only hand-maintained Skill source. Both
deliverables have byte-identical references/. Only the four marked execution
blocks in SKILL.md are adapted. Package LICENSE and NOTICE are copied intact.
Python scripts are not distributed with DWS; its native Go validator embeds
the supplementary rules.

    python3 tools/build_dws_skill.py           # Generate.
    python3 tools/build_dws_skill.py --check   # Compare without writing.
"""
from __future__ import annotations
import argparse, hashlib, os, re, shutil, sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "skills", "dingtalk-aicard")
DST = os.path.join(ROOT, "dws-aicard", "skills", "multi", "dingtalk-aicard")
SKILL_NAME = "dingtalk-aicard"
PACKAGE_NOTICES = ("LICENSE", "NOTICE")

DWS_FRONTMATTER = f"""---
name: {SKILL_NAME}
description: >
  Create, edit, and validate DingTalk AI Card (A2UI) JSON files offline.
  Use for cards built from requirements or images, structural protocol errors,
  and named component or function contract lookup through native DWS commands.
  Send a card to the current user only when a preview is requested.
metadata:
  cli_version: ">=0.2.14"
  category: product
  requires:
    bins:
      - dws
  cliHelp: "dws aicard --help"
  aicardVersion: "V0.8"
  protocolVersion: "1.0"
  catalogId: https://dingtalk.com/card/a2ui/catalogs/public/catalog.json
---"""


DWS_EXECUTION_BLOCKS = {
    "runtime": """This edition uses native DWS commands for lookup, validation, and preview; it does not need Python or the standalone Skill's scripts. On first use or an unrecognized command, run `dws aicard --help` to confirm that the binary provides `explain`, `lint`, and `preview`. Copying Skill files does not install commands in an older binary.

`explain` and `lint` use the embedded protocol offline and need no Profile. Check `dws aicard explain --help` before a batch or compact query; query names individually if unsupported. Follow the current command help. Use `dws aicard explain` for lookup, not `dws aicard lint --explain`. Inspect the exit code, `ok`, and `outcome`: successful content is in `data`, failures in `error`, and structural diagnostics in `error.details`.

If a command is missing, use a DWS build that includes aicard. Preserve the actual error if embedded protocol loading fails and inspect the DWS installation. When temporarily unavailable, the indexes can still guide a draft; state that DWS validation was not run. Do not present Python or manual checks as a DWS validation result.""",
    "query": """```bash
dws aicard explain Tabs --format json
dws aicard explain Text Row Column --compact --format json
```""",
    "lint": """Validate this file:

```bash
dws aicard lint --file card.a2ui.json --format json
```

Add `--preflight new-card` for a new card, or `--preflight resources` to check inline resources in a delta or host-created content. Confirm option support with `dws aicard lint --help`; an older binary cannot claim to have run preflight.

Read `data.valid` on success, plus `data.preflight.valid` and diagnostics when preflight is enabled. Warnings do not block. Structural or preflight errors cause a nonzero exit with details in `error.details`; `valid` still refers only to Schema checks. An environment or read failure is not a pass.

Add `--emit` when the sending layer needs an array of message strings. After structural validation, `data.a2uiMessages` contains individually serialized messages. JSON-encode the whole array for the sending parameter; do not hand-write shell escaping. `--fragment` and `--emit` are mutually exclusive. Run `dws aicard lint --self-check --format json` separately to check embedded protocol integrity.""",
    "delivery": """If the user requests a preview sent to themselves, use `dws aicard preview --file card.a2ui.json`. It really sends a card and requires a complete new-card sequence. `--dry-run` checks the local sending boundary without resolving identity or sending. To send to another person or a group, follow the current `dws chat message send-a2ui-card` contract for target and parameters.

`preview` reports `success` only when the card creation request is explicitly accepted, even if its receipt contains `openTaskId`. Keep that ID in the receipt, but do not pass it to `dws chat message query-send-status`, which is for current-user message sends. `deliveryVerified` and `renderingVerified` remain `false`. Conversation readback is separate evidence; generic card text and a nearby timestamp do not uniquely identify this card. The default sending status is `PROCESSING`. Update or finish through `dws chat message update-a2ui-card`, locating the original card by `bizId` without replaying creation. Investigate an unknown result before retrying; a receipt is not client acceptance evidence.""",
}

def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def build_skill_md(source: str) -> str:
    """Replace only marked execution blocks; keep common authoring guidance shared."""
    match = re.match(r"^---\n[\s\S]*?\n---", source)
    if not match:
        raise ValueError("skills/dingtalk-aicard/SKILL.md is missing frontmatter")
    body = source[match.end():]
    markers = list(re.finditer(r"<!-- aicard:([a-z-]+):(start|end) -->", body))
    if len(markers) != 2 * len(DWS_EXECUTION_BLOCKS):
        raise ValueError("SKILL.md needs one start/end marker pair for each of runtime, query, lint, and delivery")
    chunks, seen, cursor = [], set(), 0
    for start, end in zip(markers[::2], markers[1::2]):
        name = start.group(1)
        if (name not in DWS_EXECUTION_BLOCKS or name in seen
                or start.group(2) != "start" or end.groups() != (name, "end")):
            raise ValueError("SKILL.md has an unknown, repeated, nested, or out-of-order execution block: " + name)
        seen.add(name)
        chunks.extend((body[cursor:start.start()], DWS_EXECUTION_BLOCKS[name]))
        cursor = end.end()
    chunks.append(body[cursor:])
    rendered = "".join(chunks)
    if "<!-- aicard:" in rendered:
        raise ValueError("SKILL.md contains an unrecognized execution-block marker")
    return DWS_FRONTMATTER + rendered.rstrip("\n") + "\n"


def digest_tree(directory):
    out = {}
    for base, _, names in os.walk(directory):
        for name in names:
            if name.endswith(".pyc") or name == ".DS_Store":
                continue
            full = os.path.join(base, name)
            with open(full, "rb") as handle:
                out[os.path.relpath(full, directory)] = hashlib.sha256(handle.read()).hexdigest()
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Compare generated output with its source without writing files")
    args = parser.parse_args(argv)

    src_refs = os.path.join(SRC, "references")
    dst_refs = os.path.join(DST, "references")
    try:
        expected_md = build_skill_md(read(os.path.join(SRC, "SKILL.md")))
        notices = {name: Path(SRC, name).read_bytes() for name in PACKAGE_NOTICES}
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1

    if args.check:
        problems = []
        if not os.path.isdir(dst_refs):
            problems.append("DWS references/ is missing")
        else:
            a, b = digest_tree(src_refs), digest_tree(dst_refs)
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    problems.append(f"references/{k} differs between editions")
        md_path = os.path.join(DST, "SKILL.md")
        if not os.path.exists(md_path) or read(md_path) != expected_md:
            problems.append("SKILL.md differs from generated output")
        for name, expected in notices.items():
            path = os.path.join(DST, name)
            if not os.path.isfile(path) or Path(path).read_bytes() != expected:
                problems.append(name + " differs from generated output")
        if os.path.exists(os.path.join(DST, "scripts")):
            problems.append("DWS edition still contains scripts/; native DWS does not distribute Python scripts")
        if problems:
            print("DWS Skill differs from its source; regenerate with tools/build_dws_skill.py:")
            for p in problems[:10]:
                print("  ✗", p)
            return 1
        print(f"DWS Skill matches its generator ({len(digest_tree(dst_refs))} reference files, native commands).")
        return 0

    if os.path.isdir(DST):
        shutil.rmtree(DST)
    shutil.copytree(src_refs, dst_refs, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    with open(os.path.join(DST, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write(expected_md)
    for name, content in notices.items():
        with open(os.path.join(DST, name), "wb") as handle:
            handle.write(content)
    print(f"Generated {os.path.relpath(DST, ROOT)}: SKILL.md, LICENSE, NOTICE, and {len(digest_tree(dst_refs))} reference files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
