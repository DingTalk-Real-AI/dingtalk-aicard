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
    "runtime": """This edition uses native DWS commands, not Python. Confirm `dws aicard --help` exposes `explain`, `lint`, and `preview`; copying the Skill does not install commands. Lookup and lint are offline and need no Profile. Check each command's current help and structured envelope (`ok`, `outcome`, `data`, or `error.details`). If unavailable, references can guide a draft, but do not claim DWS validation.""",
    "query": """```bash
dws aicard explain Tabs --format json
dws aicard explain Text Row Column --compact --format json
```""",
    "lint": """```bash
dws aicard lint --file card.a2ui.json --format json
```

Choose `--preflight new-card` or `--preflight resources` for the selected scenario, and use `--emit` only when serialized message strings are needed; it cannot be combined with `--fragment`. Check exit status and `data.valid` plus `data.preflight.valid` when selected. `dws aicard lint --self-check --format json` verifies the full package.""",
    "delivery": """With native `--emit`, read `data.a2uiMessages`.

Send only when requested. Resolve exactly one target under the sending profile: `--conversation-id` for a conversation or `--open-dingtalk-id` for a person. Names are not target IDs. Inspect installed command help, JSON-encode the emitted message-string array as one `--content` argument, and call `dws chat message send-a2ui-card`. Preserve the request, target, profile and returned `bizId`. An uncertain result must be checked before another create; do not resend automatically.

Creation starts in PROCESSING. Update with `dws chat message update-a2ui-card --biz-id <bizId> --content <message-strings> --flow-status <state>` under the original profile. Keep the same `surfaceId` and component IDs; send only intended `updateDataModel` or `updateComponents` changes, not another `createSurface` or unrelated defaults. Static cards require `--flow-status FINISH` with a nonempty valid delta; prepare it before sending and preserve business data. Stream complete messages, not JSON tokens. Resource preflight checks only the delta. If current state is known, validate it with the delta; Schema validity does not prove business safety.

`dws aicard preview --file card.a2ui.json` sends a self-preview in PROCESSING; `--dry-run` does not send. For a completed static preview, finish it under the same profile with `dws chat message update-a2ui-card --biz-id <data.bizId> --content <message-strings> --flow-status FINISH`, using a nonempty state-preserving delta for the same Surface. Do not replay form defaults. Use server-issued `data.bizId`, never request-side `bizCardId`. If absent, inspect `updateWarning` and the receipt; do not recreate automatically. `openTaskId` cannot be used with `query-send-status`.

After sending, use the available readback capability under the same profile and target to identify this exact card from returned instance/message identifiers. Generic card text and a nearby timestamp are not sufficient. Report request acceptance, same-target readback, client rendering and interaction separately. If readback is unavailable or inconclusive, mark it unverified; do not claim the user received or correctly rendered the card from API acceptance alone.""",
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
