#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Generate native DWS explain indexes and Unicode tables without altering the public protocol."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dws-aicard/internal/card/a2ui/assets.json"
EXPLAIN_FILE = OUTPUT.parent / "explain.json"


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def build_explain_bundle(explains, manifest):
    return {"formatVersion": 1,
            "manifestSha256": hashlib.sha256(serialized(manifest)).hexdigest(),
            "entries": {name: {"kind": result["kind"], "definition": result}
                        for name, result in sorted(explains.items())}}


def generate_all():
    spec = importlib.util.spec_from_file_location(
        "aicard_lint", ROOT / "skills/dingtalk-aicard/scripts/aicard_lint.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    proto = lint.Protocol(str(ROOT / "skills/dingtalk-aicard/references/protocol"))
    names = list(proto.components) + list(proto.functions) + list(lint.TOKEN_TYPES) + lint.common_type_names(proto)
    for kind in lint.TOKEN_TYPES:
        names += [item["name"] for item in lint.explain(proto, kind)["items"]]
    explains = {name: lint.explain(proto, name) for name in names}
    manifest = proto.manifest()
    bundle = build_explain_bundle(explains, manifest)
    child_refs = {name: result.get("childRefs", []) for name, result in explains.items()
                  if result["kind"] == "component"}
    # Python and Go share the same digest-verified character table without third-party regex.
    xid = proto.schema_engine.regex.classes
    assets = {"manifest": manifest, "explainSha256": hashlib.sha256(serialized(bundle)).hexdigest(),
              "childRefs": child_refs, "unicodeClasses": xid,
            "unicodeBaseline": {"unicodeVersion": lint.UNICODE_VERSION, "tableContentSha256": lint.UNICODE_TABLE_SHA256},
            "validationRules": Path(proto.rules_file).read_text(encoding="utf-8")}
    return assets, bundle


def generate():
    """Keep the validator metadata accessor used by existing diagnostics."""
    return generate_all()[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    assets, bundle = generate_all()
    expected = {OUTPUT: serialized(assets), EXPLAIN_FILE: serialized(bundle)}
    if args.check:
        actual = {path for path in expected if path.exists()}
        legacy = OUTPUT.parent / 'explain'
        if legacy.exists():
            actual.update(path for path in legacy.rglob('*') if path.is_file())
        if actual != set(expected) or any(path.read_bytes() != data for path, data in expected.items()):
            print("Go index differs from the protocol or reference implementation; run tools/build_go_assets.py")
            return 1
        print("Go index matches the protocol and reference implementation")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        for path, data in expected.items():
            path.write_bytes(data)
        print(f"Generated {len(expected)} Go asset files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
