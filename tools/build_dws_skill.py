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
  Create, edit, and validate DingTalk AI Card (A2UI) JSON files with native
  DWS commands. Use for cards built from requirements or images, structural
  protocol errors, named component or function contract lookup, and cloud
  templates created, retrieved, or revised by template ID and version.
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
    "runtime": """Use native DWS commands. Check `dws aicard --help` for `explain`, `lint`, `preview`, and `template`; copying this Skill does not install them. Lookup and lint are offline and need no Profile. Check leaf help and JSON envelopes (`ok`, `outcome`, `data`, `error.details`). Missing commands permit a draft, never a DWS validation claim.

### Cloud templates

| Intent | Command |
|---|---|
| Save a validated card | `dws aicard template create --file <file>` |
| Read a known ID and version | `dws aicard template get --template-id <id> --version <version> --out <new-file>` |
| Revise a template | `dws aicard template update --template-id <id> --file <file>` |

Cloud calls need the intended Profile, organization, and environment, plus an explicit `--mcp-id` or `DWS_AICARD_TEMPLATE_MCP_ID`; there is no default. `get` needs a known ID and version, not keywords. To derive a template, get it, edit the file, and create a new one; `update` appends a version under the same ID. Report the returned ID and version; never retry an uncertain write. A read of an absent version may return the latest content, so strict use must match the approved digest.

Use approved business bindings and data paths; do not substitute local samples. Business configuration owns activation and rollback. Send complete new cards or authorized instance deltas; delivery never modifies template assets.""",
    "query": """```bash
dws aicard explain Tabs --format json
dws aicard explain Text Row Column --compact --format json
```""",
    "lint": """```bash
dws aicard lint --file a2ui.json --format json
```

Choose `--preflight new-card` or `--preflight resources` for the selected scenario. Use `--emit` only when serialized message strings are needed; it cannot be combined with `--fragment`. Check exit status, `data.valid`, and `data.preflight.valid` when selected. Fix errors at their diagnostic pointers and rerun the selected check. `dws aicard lint --self-check --format json` verifies the full package.""",
    "delivery": """Follow the installed dingtalk-chat A2UI guidance for creation, updates, summaries, annotations, and completion; check leaf help for `--summary`, `--support-forward`, and update flags. A missing capability blocks delivery.

Send only when requested. Resolve exactly one target under the sending Profile: `--conversation-id` for a conversation or `--open-dingtalk-id` for a person. Names are not target IDs. JSON-encode the emitted `data.a2uiMessages` as one `--content` argument for `dws chat message send-a2ui-card`, with a readable fallback `--summary` and a policy-based `--support-forward=true|false` (default false). For a card instantiated from a business template, update only the data paths its business configuration authorizes.

`dws aicard preview --file a2ui.json` sends a self-preview in PROCESSING; `--dry-run` does not send. Finish a static preview with `dws chat message update-a2ui-card --flow-status FINISH`, the server-issued `data.bizId` (never request-side `bizCardId`), and `--a2ui-annotations` marking its main content component as `artifact`; otherwise the conversation summary becomes the server default. If it is absent, inspect `updateWarning` and the receipt; do not recreate automatically. `openTaskId` cannot be used with `query-send-status`.

After sending, identify this exact card from returned instance or message identifiers under the same Profile and target; generic card text and a nearby timestamp are not sufficient. Report request acceptance, readback, asynchronous summary, client rendering, and interaction separately; missing evidence stays unverified.""",
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
