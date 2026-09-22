#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Generate native DWS explain indexes and Unicode tables without altering the public protocol."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dws-aicard/internal/card/a2ui/assets.json"


def generate():
    spec = importlib.util.spec_from_file_location(
        "aicard_lint", ROOT / "skills/dingtalk-aicard/scripts/aicard_lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    proto = lint.Protocol(str(ROOT / "skills/dingtalk-aicard/references/protocol"))
    names = list(proto.components) + list(proto.functions) + list(lint.TOKEN_TYPES) + lint.common_type_names(proto)
    for kind in lint.TOKEN_TYPES:
        names += [item["name"] for item in lint.explain(proto, kind)["items"]]
    explains = {name: lint.explain(proto, name) for name in names}
    # Python and Go share the same digest-verified character table without third-party regex.
    xid = proto.schema_engine.regex.classes
    return {"manifest": proto.manifest(), "explain": explains, "unicodeClasses": xid,
            "unicodeBaseline": {"unicodeVersion": lint.UNICODE_VERSION, "tableContentSha256": lint.UNICODE_TABLE_SHA256},
            "validationRules": Path(proto.rules_file).read_text(encoding="utf-8")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = json.dumps(generate(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            print("Go index differs from the protocol or reference implementation; run tools/build_go_assets.py")
            return 1
        print("Go index matches the protocol and reference implementation")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding="utf-8")
        print(f"Generated {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
