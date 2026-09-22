#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Generate three on-demand indexes from the bundled protocol.

The component, function, and Token indexes answer what is available in about
20 KB. aicard_lint.py --explain provides exact fields and examples. These
indexes are generated; --check rebuilds them in memory and detects drift.

    python3 tools/build_index.py           # Generate.
    python3 tools/build_index.py --check   # Compare only.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOCOL = os.path.join(ROOT, "skills", "dingtalk-aicard", "references", "protocol")
INDEX = os.path.join(ROOT, "skills", "dingtalk-aicard", "references", "index")
LINT = os.path.join(ROOT, "skills", "dingtalk-aicard", "scripts", "aicard_lint.py")

# Purpose summaries aid navigation; fields, defaults, and enums come from the protocol.
COMPONENT_PURPOSES = {
    'Button': 'Runs a connected action; its label belongs in the child component.',
    'ButtonGroup': 'Groups inline buttons; static prototypes may omit item actions.',
    'TextField': 'Collects text and saves input through a value binding.',
    'ChoicePicker': 'Selects one or more options; variant determines selection mode.',
    'List': 'Arranges components or data templates horizontally or vertically.',
    'ProgressBar': 'Shows completion as a continuous bar or compact blocks.',
    'Tag': 'Compact status or category label styled with theme and variant.',
    'Table': 'Shows structured records for row-by-row review with local pagination.',
    'Chart': 'Shows trends, comparisons, or proportions using contract-defined data.',
}


def load_lint():
    spec = importlib.util.spec_from_file_location("aicard_lint", LINT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def protocol_fingerprint() -> str:
    h = hashlib.sha256()
    for name in sorted(os.listdir(PROTOCOL)):
        if name.endswith(".json"):
            with open(os.path.join(PROTOCOL, name), "rb") as handle:
                h.update(name.encode()); h.update(handle.read())
    return h.hexdigest()[:16]


def first_sentence(text: str, limit: int) -> str:
    text = (text or "").strip().replace("\n", " ").replace("|", "／")
    for sep in (". ", "。"):
        if sep in text:
            text = text.split(sep, 1)[0] + sep.strip()
            break
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build(mod, proto) -> dict[str, str]:
    fp = protocol_fingerprint()
    footer = (f"\n---\nGenerated from `references/protocol/` (fingerprint `{fp}`). "
              "Do not edit this index: protocol changes regenerate it. "
              "For exact fields and a minimal example, query a name as shown in the Skill entrypoint; "
              "the whole catalog need not enter the model context.\n")

    # ---- Components ----
    rows = []
    for name in proto.components:
        r = mod.explain(proto, name)
        refs = ", ".join(x["path"] for x in r["childRefs"]) or "—"
        purpose = COMPONENT_PURPOSES.get(name) or first_sentence(r["description"], 88)
        rows.append((r["tier"], name, ", ".join(r["required"]) or "—", refs, purpose))
    rows.sort(key=lambda x: (list(mod.SHARD_LABELS.values()).index(x[0]), x[1]))
    basic = sum(1 for x in rows if x[0] == "Common components")
    comp = [f"# Component index ({len(rows)} total; {basic} common, {len(rows) - basic} others)",
            "",
            "Choose a component here, then query its fields and minimal example as shown in the Skill entrypoint. Start with common components and inspect other groups only when needed.",
            "Required fields include `id` and `component`. Child references show the field path to child components; `[]` denotes an array item.",
            "",
            "| Component | Group | Required | Child references | Purpose |", "|---|---|---|---|---|"]
    comp += [f"| `{n}` | {t} | {req} | {refs} | {desc} |" for t, n, req, refs, desc in rows]
    comp.append(footer)

    # ---- Functions ----
    frows = []
    for call in proto.functions:
        r = mod.explain(proto, call)
        req = ", ".join(a["name"] for a in r["args"] if a["required"]) or "—"
        frows.append((r["tier"], call, r["returnType"] or "—", req, "Yes" if r["hostAction"] else "", first_sentence(r["description"], 80)))
    frows.sort(key=lambda x: ({"Core functions": 0, "Expressions": 1, "Host actions": 2}.get(x[0], 3), x[1]))
    hosts = sum(1 for x in frows if x[4])
    fn = [f"# Function index ({len(frows)} total; {hosts} host actions)",
          "",
          "Value functions use `{\"call\": …, \"args\": {…}}` and may nest. Host actions require explicit "
          "`catalogId: urn:dingtalk:a2ui:host:v1`; the system function `@index` must not carry it. "
          "`checks[].condition` requires a boolean-returning function.",
          "",
          "| Function | Group | Returns | Required arguments | Host action | Purpose |", "|---|---|---|---|---|---|"]
    fn += [f"| `{c}` | {t} | {ret} | {req} | {host} | {desc} |" for t, c, ret, req, host, desc in frows]
    fn.append(footer)

    # ---- Tokens ----
    tok = ["# Token index", "",
           "`colorToken` and `IconName` use protocol enums. Prefer a registered full ID for `sizeToken`; "
           "other strings depend on host compatibility or fallback. Follow each field's Schema for container colors. "
           "Use a given Token for one meaning within a card.", ""]
    for kind, title in (("ColorToken", "Colors"), ("SizeToken", "Font sizes"), ("IconName", "Icons")):
        r = mod.explain(proto, kind)
        tok += [f"## {title}: `{kind}` ({r['count']} items)", ""]
        if r["groups"]:
            for group, names in r["groups"].items():
                tok.append(f"- **{group}**: " + ", ".join(f"`{x}`" for x in names))
            tok += ["", "| Token | Meaning |", "|---|---|"]
            tok += [f"| `{it['name']}` | {first_sentence(it['description'], 64)} |" for it in r["items"]]
        else:
            tok += ["| Token | Meaning |", "|---|---|"]
            tok += [f"| `{it['name']}` | {first_sentence(it['description'], 72)} |" for it in r["items"]]
        tok.append("")
    tok.append(footer)

    return {"components.md": "\n".join(comp), "functions.md": "\n".join(fn), "tokens.md": "\n".join(tok)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Compare generated indexes with the protocol without writing files.")
    args = parser.parse_args(argv)
    mod = load_lint()
    proto = mod.Protocol(PROTOCOL)
    files = build(mod, proto)
    if args.check:
        drift = [n for n, body in files.items()
                 if not os.path.exists(os.path.join(INDEX, n)) or open(os.path.join(INDEX, n), encoding="utf-8").read() != body]
        if drift:
            print("references/index/ has drifted from the protocol; regenerate with tools/build_index.py: " + ", ".join(drift))
            return 1
        print(f"references/index/ matches the protocol ({len(files)} files, fingerprint {protocol_fingerprint()}).")
        return 0
    os.makedirs(INDEX, exist_ok=True)
    for name, body in files.items():
        with open(os.path.join(INDEX, name), "w", encoding="utf-8") as handle:
            handle.write(body)
    sizes = {n: len(b.encode("utf-8")) for n, b in files.items()}
    print("Generated references/index/: " + ", ".join(f"{n} {s:,}B" for n, s in sizes.items()) + f" ({sum(sizes.values()):,}B total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
