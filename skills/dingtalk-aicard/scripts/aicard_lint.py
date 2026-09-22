#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Offline A2UI card validator for the standalone Skill.

Checks JSON and bundled A2UI protocol structure: fields, required properties,
types, and enums of messages, components, and functions. It does not execute
functions, merge runtime state, or judge layout, initial values, or delivery.

Uses jsonschema Draft 2020-12 without network access, sending, or DWS.
The Go implementation must agree on shared/fixtures.
Replace python3 below with the prepared interpreter; see setup_env.py.

    python3 aicard_lint.py card.a2ui.json           # Human-readable
    python3 aicard_lint.py card.a2ui.json --format json
    python3 aicard_lint.py --self-check             # Check package integrity

Exit codes: 0 passed; 1 validation error; 2 input or environment error.
CLI JSON uses ASCII escapes without changing parsed content. Text and stderr use UTF-8.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, math, os, re, sys
from urllib.parse import urljoin
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROTOCOL_DIR = os.environ.get("AICARD_PROTOCOL_DIR") or os.path.join(HERE, "..", "references", "protocol")
COMPONENT_SHARDS = ("catalog-components-common.json", "catalog-components-composition.json",
                    "catalog-components-inputs.json", "catalog-components-specialized.json")
FUNCTION_SHARDS = ("catalog-functions-core.json", "catalog-functions-expressions.json",
                   "catalog-functions-host-actions.json")
COMMON_SHARDS = ("common-types-basic.json", "common-types-extended.json", "common-types-visual.json")
SHARDS = (*COMPONENT_SHARDS, *FUNCTION_SHARDS, *COMMON_SHARDS, "agent-to-renderer.json")
VALIDATION_RULES = os.environ.get("AICARD_VALIDATION_RULES") or os.path.join(HERE, "a2ui-validation-rules.json")
# Python validation and Go assets share bundled character tables, independent of interpreter Unicode versions.
UNICODE_VERSION = '17.0.0'
UNICODE_TABLE = os.path.join(HERE, 'unicode-xid.json')
# Hash normalized versions and ranges; Git line endings, BOM, and formatting do not change the protocol.
UNICODE_TABLE_SHA256 = 'c7eea54774695e428d3683103ed1062491fcf49c6851a9d76b6a2384ea1805dc'
SHARD_LABELS = dict(zip((*COMPONENT_SHARDS, *FUNCTION_SHARDS),
                       ("Common components", "Composition", "Inputs", "Data and media",
                        "Core functions", "Expressions", "Host actions")))
JSON_TYPES = {"string": str, "boolean": bool, "number": (int, float),
              "integer": int, "object": dict, "array": list}


class ProtocolError(RuntimeError):
    pass


class DependencyError(ProtocolError):
    pass


class PythonEnvironmentError(ProtocolError):
    pass


class ValidationLimitError(RuntimeError):
    """Local resources could not complete validation; the input is not necessarily invalid."""
    pass


def escape_pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


class ProtocolRegex:
    """Standard regex with bundled XID rules for property expressions in this public protocol."""

    error = re.error
    identifier_pattern = r'^[\p{XID_Start}_][\p{XID_Continue}]*$'

    def __init__(self):
        try:
            with open(UNICODE_TABLE, 'rb') as handle:
                raw = handle.read()
            table = json.loads(raw)
            if table['unicodeVersion'] != UNICODE_VERSION:
                raise ValueError('Unicode version mismatch')
            self.classes = table['classes']
            if (not isinstance(self.classes, dict) or set(self.classes) != {'XID_Start', 'XID_Continue'}
                    or not all(isinstance(value, str) for value in self.classes.values())):
                raise ValueError('Incomplete character-property set')
            canonical = json.dumps({'unicodeVersion': table['unicodeVersion'], 'classes': self.classes},
                                   ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
            if hashlib.sha256(canonical).hexdigest() != UNICODE_TABLE_SHA256:
                raise ValueError('Character-table digest mismatch')
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ProtocolError(f'Bundled Unicode tables are unavailable: {error}; restore the complete Skill') from error
        self._compiled = {}

    def compile(self, expression):
        if expression not in self._compiled:
            expanded = expression
            if expression == self.identifier_pattern:
                for name, ranges in self.classes.items():
                    expanded = expanded.replace('\\p{' + name + '}', ranges)
            # Defer other expressions to the standard library; reject unsupported property escapes.
            self._compiled[expression] = re.compile(expanded)
        return self._compiled[expression]

    def search(self, expression, value):
        return self.compile(expression).search(value)


class SchemaEngine:
    """Resolve only bundled references; diagnostic selection never changes engine validity."""

    def __init__(self, docs, *, optimize=True):
        try:
            from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
            from referencing import Registry, Resource
        except ModuleNotFoundError as error:
            raise DependencyError(
                f"Python {sys.executable} lacks validation dependency {error.name}; "
                f"run {os.path.join(HERE, 'setup_env.py')} and use its pythonExecutable") from error
        except ImportError as error:
            raise PythonEnvironmentError(
                f"Python {sys.executable} cannot import a validation dependency: {error}; "
                f"run {os.path.join(HERE, 'setup_env.py')} to select a healthy interpreter") from error
        self.docs = docs
        regex = ProtocolRegex()
        self.regex = regex
        self._valid_subtrees = None
        self._tag_cache = {}
        self._owners = {}
        self._has_dynamic_refs = False

        def index_schema(node, base):
            if isinstance(node, dict):
                base = urljoin(base, node.get('$id', ''))
                self._owners[id(node)] = base
                self._has_dynamic_refs |= '$dynamicRef' in node or '$recursiveRef' in node
                for value in node.values():
                    index_schema(value, base)
            elif isinstance(node, list):
                for value in node:
                    index_schema(value, base)
        for doc in docs.values():
            index_schema(doc, doc['$id'])
        # JSON Schema uses Unicode property escapes, which Python re does not support.
        # Extend regex execution only, without changing Schema composition or references.
        def pattern(validator, expression, instance, schema):
            if isinstance(instance, str) and not regex.search(expression, instance):
                yield ValidationError(f"Text does not match pattern {expression!r}")

        def pattern_properties(validator, patterns, instance, schema):
            if not isinstance(instance, dict):
                return
            for expression, subschema in patterns.items():
                for key, value in instance.items():
                    if regex.search(expression, key):
                        yield from validator.descend(value, subschema, path=key, schema_path=expression)

        def additional_properties(validator, additional, instance, schema):
            if not isinstance(instance, dict):
                return
            patterns = schema.get("patternProperties", {})
            extras = [k for k in instance if k not in schema.get("properties", {}) and
                      not any(regex.search(expression, k) for expression in patterns)]
            if isinstance(additional, dict):
                for key in extras:
                    yield from validator.descend(instance[key], additional, path=key)
            elif not additional and extras:
                yield ValidationError("Unknown fields: " + repr(sorted(extras)))

        self.validator_type = validators.extend(Draft202012Validator, {
            "pattern": pattern, "patternProperties": pattern_properties,
            "additionalProperties": additional_properties})
        if optimize and not self._has_dynamic_refs:
            original_properties = self.validator_type.VALIDATORS['properties']

            def properties(validator, specs, instance, schema):
                # A branch with a mismatched constant has already failed; skip recursive arguments.
                if isinstance(instance, dict):
                    for key in ('component', 'call'):
                        sub = specs.get(key, {})
                        if (isinstance(sub, dict) and isinstance(sub.get('const'), str)
                                and key in instance and instance[key] != sub['const']):
                            yield from validator.descend(instance[key], sub, path=key, schema_path=key)
                            return
                yield from original_properties(validator, specs, instance, schema)

            self.validator_type.VALIDATORS['properties'] = properties
            original_descend = self.validator_type.descend

            def descend(validator, instance, schema, path=None, schema_path=None, resolver=None):
                # Cache only fully valid subtrees in this run; errors carry path context and are never reused.
                # Keep object references because the input is immutable during validation and IDs may be recycled.
                cache = self._valid_subtrees
                owner = resolver or validator._resolver
                key = (id(instance), id(schema), owner._base_uri)
                if cache is not None and key in cache:
                    return
                passed = True
                for error in original_descend(validator, instance, schema, path=path,
                                               schema_path=schema_path, resolver=resolver):
                    passed = False
                    yield error
                if passed and cache is not None:
                    cache[key] = (instance, schema)

            self.validator_type.descend = descend
        self.schema_format_checker = FormatChecker(formats=[])

        @self.schema_format_checker.checks("regex", raises=regex.error)
        def valid_regex(value):
            return not isinstance(value, str) or regex.compile(value) is not None
        self.registry = Registry().with_resources(
            (doc["$id"], Resource.from_contents(doc)) for doc in docs.values())
        self.cache = {}
        # The registry has no network retrieval; external references fail explicitly.
        for name, doc in docs.items():
            stack = [doc]
            resolver = self.registry.resolver(doc["$id"])
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    if "$ref" in node:
                        try:
                            resolver.lookup(node["$ref"])
                        except Exception as error:
                            raise ProtocolError(f"Protocol reference cannot be resolved in the package: {name} {node['$ref']}") from error
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)

    def validator(self, shard, spec=None):
        key = (shard, json.dumps(spec, sort_keys=True) if spec is not None else "")
        if key not in self.cache:
            def absolute(node):
                if isinstance(node, dict):
                    return {k: urljoin(self.docs[shard]["$id"], v) if k == "$ref" else absolute(v)
                            for k, v in node.items()}
                if isinstance(node, list):
                    return [absolute(v) for v in node]
                return node
            schema = self.docs[shard] if spec is None else absolute(spec)
            self.cache[key] = self.validator_type(schema, registry=self.registry)
        return self.cache[key]

    def _tag_values(self, schema, key, base, seen=frozenset()):
        """Finite constant set for diagnostic branch selection; unknown conditions are not guessed."""
        if not isinstance(schema, dict):
            return None
        token = (id(schema), key, base)
        if token in seen:
            return None
        if token in self._tag_cache:
            return self._tag_cache[token][1]
        seen = seen | {token}
        restrictions = []
        prop = schema.get('properties', {}).get(key, {})
        if isinstance(prop, dict):
            if isinstance(prop.get('const'), str):
                restrictions.append({prop['const']})
            elif isinstance(prop.get('enum'), list) and all(isinstance(v, str) for v in prop['enum']):
                restrictions.append(set(prop['enum']))
        if '$ref' in schema:
            resolved = self.registry.resolver(base).lookup(schema['$ref'])
            values = self._tag_values(resolved.contents, key, resolved.resolver._base_uri, seen)
            if values is not None:
                restrictions.append(values)
        for sub in schema.get('allOf', []):
            values = self._tag_values(sub, key, base, seen)
            if values is not None:
                restrictions.append(values)
        for keyword in ('oneOf', 'anyOf'):
            if keyword in schema:
                groups = [self._tag_values(sub, key, base, seen) for sub in schema[keyword]]
                if groups and all(g is not None for g in groups):
                    restrictions.append(set().union(*groups))
        result = set.intersection(*restrictions) if restrictions else None
        self._tag_cache[token] = (schema, result)
        return result

    def _declared_properties(self, schema, instance, base, seen=frozenset()):
        """Declared fields for diagnostics, including a matching branch with another type error."""
        if not isinstance(schema, dict) or (id(schema), base) in seen:
            return set(), set()
        seen = seen | {(id(schema), base)}
        names, patterns = set(schema.get('properties', {})), set(schema.get('patternProperties', {}))
        if '$ref' in schema:
            resolved = self.registry.resolver(base).lookup(schema['$ref'])
            other, pats = self._declared_properties(resolved.contents, instance, resolved.resolver._base_uri, seen)
            names |= other; patterns |= pats
        for keyword in ('allOf', 'oneOf', 'anyOf'):
            branches = schema.get(keyword, [])
            if keyword != 'allOf':
                matching = [sub for sub in branches if not any(
                        isinstance(instance.get(key), str) and values is not None and instance[key] not in values
                        for key in ('component', 'call')
                        for values in [self._tag_values(sub, key, base)])]
                # Diagnose an unknown name as a constant error, not as unknown common fields such as args.
                branches = matching or branches
            for sub in branches:
                other, pats = self._declared_properties(sub, instance, base, seen)
                names |= other; patterns |= pats
        if 'if' in schema:
            condition = self.validator_type(schema['if'], registry=self.registry,
                                            _resolver=self.registry.resolver(base)).is_valid(instance)
            branch = schema.get('then' if condition else 'else', {})
            other, pats = self._declared_properties(branch, instance, base, seen)
            names |= other; patterns |= pats
        return names, patterns

    def self_check(self):
        for doc in self.docs.values():
            self.validator_type.check_schema(doc, format_checker=self.schema_format_checker)

    @staticmethod
    def leaves(error):
        if not error.context:
            return [error]
        groups = defaultdict(list)
        for child in error.context:
            key = next(iter(child.schema_path), None)
            groups[key].extend(SchemaEngine.leaves(child))
        base = tuple(error.absolute_path)

        def score(items):
            # Prefer call/component interpretations when their tag is present.
            # A closed path-binding branch sees call as extra; do not misreport an unknown function as a missing path.
            tags = {key for key in ('component', 'call')
                    if isinstance(error.instance, dict) and key in error.instance}
            wrong_shape = sum(e.validator == 'additionalProperties' and tuple(e.absolute_path) == base
                              and bool(tags - set(e.schema.get('properties', {}))) for e in items)
            discriminator = sum(e.validator in ("const", "enum") and
                                tuple(e.absolute_path) in (base + ("component",), base + ("call",))
                                for e in items)
            wrong_type = sum(e.validator == "type" and tuple(e.absolute_path) == base for e in items)
            missing = sum(e.validator == "required" and tuple(e.absolute_path) == base for e in items)
            return (wrong_shape, wrong_type, discriminator, missing, -max(len(e.absolute_path) for e in items), len(items))

        return min(groups.values(), key=score)

    def check(self, value, shard, spec=None, pointer=""):
        # Select the matching envelope operation to avoid required-field noise from other operations.
        # Otherwise validate against the full oneOf.
        if spec is None and shard == "agent-to-renderer.json" and isinstance(value, dict):
            branches = self.docs[shard].get("oneOf", [])
            for branch in branches:
                name = branch.get("$ref", "").rsplit("/", 1)[-1]
                definition = self.docs[shard].get("$defs", {}).get(name, {})
                ops = set(definition.get("properties", {})) - {"version"}
                if len(ops) == 1 and ops <= value.keys() and set(value) <= ops | {"version"}:
                    spec = branch
                    break
        previous_cache = self._valid_subtrees
        self._valid_subtrees = {}
        try:
            errors = list(self.validator(shard, spec).iter_errors(value))
        except RecursionError as error:
            raise ValidationLimitError('The input exceeded this Python validator’s recursion budget; structural validation is incomplete') from error
        except Exception as error:
            raise ProtocolError(f"Protocol validation engine failed: {type(error).__name__}: {error}") from error
        finally:
            self._valid_subtrees = previous_cache
        constants = defaultdict(list)
        pending = list(errors)
        while pending:
            err = pending.pop()
            pending.extend(err.context)
            if err.validator == "const" and err.validator_value not in constants[tuple(err.absolute_path)]:
                constants[tuple(err.absolute_path)].append(err.validator_value)
        selected = [leaf for error in errors for leaf in self.leaves(error)]
        out, seen = [], set()
        codes = {"type": "type_mismatch", "enum": "bad_enum", "const": "bad_enum",
                 "required": "missing_required", "additionalProperties": "unknown_property",
                 "unevaluatedProperties": "unknown_property", "minimum": "below_minimum",
                 "maximum": "above_maximum", "minItems": "too_few_items", "maxItems": "too_many_items",
                 "minLength": "too_short", "maxLength": "too_long", "pattern": "pattern_mismatch"}
        labels = {"type": "Incorrect field type", "enum": "Value is outside the protocol enum",
                  "const": "Value does not match the protocol constant",
                  "required": "Missing required field", "additionalProperties": "Unknown field",
                  "unevaluatedProperties": "Unknown field", "minimum": "Number is below the minimum",
                  "maximum": "Number is above the maximum", "minItems": "Too few array items",
                  "maxItems": "Too many array items", "minLength": "Text is too short",
                  "maxLength": "Text is too long", "pattern": "Text does not match the protocol pattern"}
        for error in selected:
            path = pointer + "".join("/" + escape_pointer(x) for x in error.absolute_path)
            code = "schema." + codes.get(error.validator, "invalid")
            expected = error.validator_value
            if error.validator == "required" and isinstance(error.instance, dict):
                expected = [k for k in expected if k not in error.instance]
            if error.validator in ('additionalProperties', 'unevaluatedProperties') and isinstance(error.instance, dict):
                if error.validator == 'additionalProperties':
                    allowed = set(error.schema.get('properties', {}))
                    patterns = set(error.schema.get('patternProperties', {}))
                else:
                    base = self._owners.get(id(error.schema), self.docs[shard]['$id'])
                    allowed, patterns = self._declared_properties(error.schema, error.instance, base)
                extras = sorted(k for k in error.instance if k not in allowed and
                                not any(self.regex.search(pattern, k) for pattern in patterns))
                for name in extras:
                    field_path = path + '/' + escape_pointer(name)
                    key = (code, field_path)
                    if key not in seen:
                        seen.add(key)
                        d = Diagnostic(code, 'error', field_path, f'Unknown field: {name!r}', 'Remove or correct the field name')
                        d.update(keyword=error.validator,
                                 schemaPointer='/' + '/'.join(escape_pointer(x) for x in error.absolute_schema_path))
                        out.append(d)
                # Do not invent unknown-field errors when an argument error left common fields unevaluated.
                continue
            key = (code, path) if code == 'schema.bad_enum' else (code, path, json.dumps(expected, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            detail = json.dumps(expected, ensure_ascii=False)
            if error.validator == 'enum' and isinstance(expected, list) and len(expected) > 6:
                detail = json.dumps(expected[:6], ensure_ascii=False) + f' among {len(expected)} values; use --explain for the full enum'
            if error.validator != 'enum' and len(detail) > 180:
                detail = detail[:177] + "…"
            if error.validator == "const" and len(constants[tuple(error.absolute_path)]) > 1:
                candidates = constants[tuple(error.absolute_path)]
                detail = json.dumps(candidates[:6], ensure_ascii=False)
                if len(candidates) > 6:
                    detail += f" among {len(candidates)} values; use --explain for the full enum"
                message = "Value is outside the protocol enum; allowed values: " + detail
            else:
                message = labels.get(error.validator, "Protocol combination constraint failed") + f": {detail}"
            diagnostic = Diagnostic(code, "error", path, message, "Edit this location according to the bundled DingTalk protocol")
            diagnostic["keyword"] = error.validator
            diagnostic["schemaPointer"] = "/" + "/".join(escape_pointer(x) for x in error.absolute_schema_path)
            out.append(diagnostic)
        specific_paths = {d['pointer'] for d in out if d['code'] != 'schema.unknown_property'}
        out = [d for d in out if d['code'] != 'schema.unknown_property' or d['pointer'] not in specific_paths]
        if errors and not out:
            out.append(Diagnostic("schema.invalid", "error", pointer, "Input does not conform to the DingTalk protocol"))
        return out


def apply_validation_rules(documents, rules):
    """Compose supplementary rules on a copy; reject mismatched files, paths, or public references."""
    if (not isinstance(rules, dict) or rules.get("version") != 1
            or not isinstance(rules.get("definitions"), dict)
            or not isinstance(rules.get("references"), list)
            or not rules["definitions"] or not rules["references"]):
        raise ProtocolError("Unsupported supplementary validation-rule format")
    result = copy.deepcopy(documents)
    try:
        for file, definitions in rules["definitions"].items():
            if file not in result or not isinstance(definitions, dict):
                raise ProtocolError(f"Validation type does not match protocol shard: {file}")
            for name, definition in definitions.items():
                if name in result[file]["$defs"]:
                    raise ProtocolError(f"Validation type conflicts with a public definition: {name}")
                result[file]["$defs"][name] = copy.deepcopy(definition)
        for rule in rules["references"]:
            node = result[rule["file"]]
            if not isinstance(rule["path"], list) or not isinstance(rule["validationRef"], str):
                raise ProtocolError("Malformed supplementary validation reference")
            for key in rule["path"]:
                node = node[key]
            if not isinstance(node, dict) or node.get("$ref") != rule["publicRef"]:
                raise ProtocolError(f"Validation rule differs from public reference: {rule['file']}:{rule['path']}")
            node["$ref"] = rule["validationRef"]
    except (KeyError, IndexError, TypeError) as error:
        raise ProtocolError(f"Validation rule differs from a public protocol path: {error}") from error
    return result


# ---------------------------------------------------------------- Protocol loading

class Protocol:
    """Load eleven public Schemas and supplementary rules for references and component fields."""

    def __init__(self, directory: str, validation_rules=None):
        self.dir = os.path.abspath(directory)
        self.docs: dict[str, dict] = {}
        for name in SHARDS:
            path = os.path.join(self.dir, name)
            if not os.path.exists(path):
                raise ProtocolError(f"Missing protocol shard: {name} (directory {self.dir})")
            with open(path, encoding="utf-8") as handle:
                self.docs[name] = json.load(handle)
            if not isinstance(self.docs[name], dict) or not isinstance(self.docs[name].get("$id"), str):
                raise ProtocolError(f"Protocol shard is not a Schema object with $id: {name}")
        self.components: dict[str, tuple[dict, str]] = {}
        for shard in COMPONENT_SHARDS:
            for name, schema in self.docs[shard].get("components", {}).items():
                self.components[name] = (schema, shard)
        extended = self.docs["common-types-extended.json"]["$defs"]
        self.color_tokens = {o["const"] for o in self.docs["common-types-visual.json"]["$defs"]["ColorToken"]["oneOf"] if "const" in o}
        self._props_cache: dict[str, dict] = {}
        self._ref_paths = self._build_ref_paths()
        # Load functions from shards; add the system function separately.
        self.functions: dict[str, tuple[dict, str]] = {}
        for shard in FUNCTION_SHARDS:
            for name, schema in self.docs[shard].get("functions", {}).items():
                self.functions[name] = (schema, shard)
        # Host actions come from HostActionOwner's call.enum condition, not a duplicate name list.
        # System function @index is in common types, not Catalog.functions.
        basic_defs = self.docs["common-types-basic.json"]["$defs"]
        if "IndexSystemFunction" in basic_defs:
            self.functions["@index"] = (basic_defs["IndexSystemFunction"], "common-types-basic.json")
        self.host_actions = self._find_host_actions(extended.get("HostActionOwner", {}))
        self._fn_cache: dict[str, dict] = {}
        self.accepted_operations: set[str] = set(OFFICIAL_OPERATIONS)
        self.envelope = self._load_envelope()
        self.rules_file = os.path.abspath(validation_rules or VALIDATION_RULES)
        try:
            with open(self.rules_file, encoding="utf-8") as handle:
                self.validation_rules = json.load(handle)
        except (OSError, ValueError) as error:
            raise ProtocolError(f"Supplementary validation rules unavailable: {self.rules_file}: {error}") from error
        self.schema_engine = SchemaEngine(apply_validation_rules(self.docs, self.validation_rules))

    def _load_envelope(self) -> dict[str, dict]:
        """Derive operation fields and requirements from agent-to-renderer.json."""
        path = os.path.join(self.dir, "agent-to-renderer.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as handle:
            env = json.load(handle)
        table: dict[str, dict] = {}
        accepted = set()
        for branch in env.get("oneOf", []) or []:
            ref = branch.get("$ref", "") if isinstance(branch, dict) else ""
            name = ref.rsplit("/", 1)[-1]           # For example, UpdateComponentsMessage.
            if name.endswith("Message"):
                op = name[:-len("Message")]
                accepted.add(op[0].lower() + op[1:])
        self.accepted_operations = accepted or set(OFFICIAL_OPERATIONS)
        def find(node):
            if isinstance(node, dict):
                for op, body in (node.get("properties") or {}).items():
                    if op in KNOWN_OPERATIONS and isinstance(body, dict) and "properties" in body and op not in table:
                        table[op] = {"keys": set(body["properties"]), "required": set(body.get("required", [])),
                                     "closed": body.get("additionalProperties") is False}
                for v in node.values():
                    find(v)
            elif isinstance(node, list):
                for v in node:
                    find(v)
        find(env)
        return table

    # -- $ref ------------------------------------------------------------
    def deref(self, ref: str, current: str):
        if ref.startswith("#"):
            target, fragment = current, ref[1:]
        else:
            target, _, fragment = ref.partition("#")
            target = target.lstrip("./")
        node = self.docs.get(target)
        if node is None:
            return None, current
        for raw in fragment.strip("/").split("/"):
            if not raw:
                continue
            seg = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return None, target
        return node, target

    def _flatten(self, schema, current, acc=None, depth=0):
        """Expand allOf and $ref to collect properties, owner shards, and required fields."""
        acc = acc if acc is not None else {"props": {}, "required": set()}
        if not isinstance(schema, dict) or depth > 12:
            return acc
        if "$ref" in schema:
            target, shard = self.deref(schema["$ref"], current)
            if target is not None:
                self._flatten(target, shard, acc, depth + 1)
            return acc
        for key in schema.get("required", []) or []:
            acc["required"].add(key)
        for name, spec in (schema.get("properties") or {}).items():
            acc["props"].setdefault(name, (spec, current))
        for sub in schema.get("allOf", []) or []:
            self._flatten(sub, current, acc, depth + 1)
        return acc

    def component_props(self, name: str) -> dict:
        """Collect component properties and ComponentCommon properties supplied by outer allOf."""
        if name in self._props_cache:
            return self._props_cache[name]
        schema, shard = self.components[name]
        acc = self._flatten(schema, shard)
        self._flatten({"$ref": "./common-types-basic.json#/$defs/ComponentCommon"}, shard, acc)
        self._props_cache[name] = acc
        return acc

    def accepted_types(self, spec, current, depth=0):
        """Return the allowed JSON types and enum values for a property."""
        if not isinstance(spec, dict) or depth > 10:
            return None, None
        if "$ref" in spec:
            target, shard = self.deref(spec["$ref"], current)
            return self.accepted_types(target, shard, depth + 1) if target is not None else (None, None)
        if "enum" in spec:
            return {"string"}, set(spec["enum"])
        types, enum = set(), None
        if "type" in spec:
            types.add(spec["type"])
        if "const" in spec:
            types.add("string")
        for key in ("oneOf", "anyOf"):
            for sub in spec.get(key, []) or []:
                sub_types, sub_enum = self.accepted_types(sub, current, depth + 1)
                if sub_types:
                    types |= sub_types
                if sub_enum:
                    enum = (enum or set()) | sub_enum
        return (types or None), enum

    # -- Reference paths -------------------------------------------------
    def _ref_kind(self, ref):
        if not isinstance(ref, str):
            return None
        if "ChildList" in ref:
            return "list"
        if "ComponentId" in ref or ref.endswith("/Child"):
            return "id"
        return None

    def _scan_refs(self, schema, path=()):
        """Find paths whose $ref reaches ComponentId/Child (:id) or ChildList (:list)."""
        found = []
        if isinstance(schema, dict):
            kind = self._ref_kind(schema.get("$ref"))
            if kind:
                return [(path, kind)]
            if "items" in schema:
                found += self._scan_refs(schema["items"], path + ("[]",))
            for key in ("allOf", "anyOf", "oneOf"):
                for sub in schema.get(key, []) or []:
                    found += self._scan_refs(sub, path)
            for name, sub in (schema.get("properties") or {}).items():
                found += self._scan_refs(sub, path + (name,))
        return found

    def _build_ref_paths(self):
        """Map a component name to [(path, kind)] without hard-coded field names.

        Tabs uses tabs.[].child; Stack also has overlays.[].child. ChildList is
        terminal because runtime distinguishes static arrays from
        {componentId,path} templates. Descendant paths beneath a list must be
        dropped, or Loop.children would expose both the list and its
        componentId as root-context references, misclassifying relative bindings.
        """
        table = {}
        for name, (schema, _) in self.components.items():
            paths = self._scan_refs(schema)
            if not paths:
                continue
            lists = [p for p, kind in paths if kind == "list"]
            kept = [(p, kind) for p, kind in paths
                    if kind == "list" or not any(
                        len(p) > len(lp) and p[:len(lp)] == lp for lp in lists)]
            table[name] = kept
        return table

    def ref_paths(self, component_name):
        return self._ref_paths.get(component_name, [])

    @property
    def ref_field_names(self):
        return {p[0] for paths in self._ref_paths.values() for p, _ in paths if p}

    def _find_host_actions(self, node) -> set[str]:
        """HostActionOwner uses if/then to require host catalogId for named calls."""
        found: set[str] = set()
        if isinstance(node, dict):
            call = node.get("call")
            if isinstance(call, dict) and isinstance(call.get("enum"), list):
                found |= {v for v in call["enum"] if isinstance(v, str)}
            for value in node.values():
                found |= self._find_host_actions(value)
        elif isinstance(node, list):
            for value in node:
                found |= self._find_host_actions(value)
        return found

    def function_spec(self, call: str):
        """Function contract: return type, argument properties and shards, required set, closure."""
        if call in self._fn_cache:
            return self._fn_cache[call]
        entry = self.functions.get(call)
        if not entry:
            return None
        schema, shard = entry
        acc = self._flatten(schema, shard)
        spec = {"returnType": schema.get("returnType"), "args_props": {}, "args_required": set(),
                "args_closed": False, "args_extra": {}}
        args = acc["props"].get("args")
        if args:
            args_schema, args_shard = args
            inner = self._flatten(args_schema, args_shard)
            spec["args_props"] = inner["props"]
            spec["args_required"] = inner["required"]
            spec["args_closed"] = (args_schema.get("additionalProperties") is False
                                   or args_schema.get("unevaluatedProperties") is False)
            spec["args_extra"] = {k: v for k, v in args_schema.items()
                                  if k in ("anyOf", "minProperties")}
        self._fn_cache[call] = spec
        return spec

    def slot_kinds(self, spec, current, depth=0) -> set:
        """Allowed slot forms: literal, binding, or function; catches bindings with extra keys."""
        kinds: set = set()
        if not isinstance(spec, dict) or depth > 10:
            return kinds
        ref = spec.get("$ref")
        if isinstance(ref, str):
            if ref.endswith("/DataBinding"):
                return {"binding"}
            if "FunctionCall" in ref:
                return {"function"}
            target, shard = self.deref(ref, current)
            return self.slot_kinds(target, shard, depth + 1) if target is not None else kinds
        if "enum" in spec or "const" in spec:
            kinds.add("string")
        t = spec.get("type")
        if isinstance(t, str):
            kinds.add(t)
        elif isinstance(t, list):
            kinds |= set(t)
        for key in ("oneOf", "anyOf", "allOf"):
            for sub in spec.get(key, []) or []:
                kinds |= self.slot_kinds(sub, current, depth + 1)
        return kinds

    def manifest(self):
        import hashlib
        digests = {}
        for name in SHARDS:
            with open(os.path.join(self.dir, name), "rb") as handle:
                digests[name] = hashlib.sha256(handle.read()).hexdigest()[:16]
        with open(self.rules_file, "rb") as handle:
            digests["a2ui-validation-rules.json"] = hashlib.sha256(handle.read()).hexdigest()[:16]
        return digests


# ---------------------------------------------------------------- Structural validation

OFFICIAL_OPERATIONS = ('createSurface', 'updateComponents', 'updateDataModel',
                       'deleteSurface', 'callRendererFunction', 'agentFunctionResponse')
KNOWN_OPERATIONS = OFFICIAL_OPERATIONS
HOST_CATALOG_ID = 'urn:dingtalk:a2ui:host:v1'


class Diagnostic(dict):
    def __init__(self, code, severity, pointer, message, hint=''):
        super().__init__(code=code, severity=severity, pointer=pointer, message=message, hint=hint)


def lint(payload, protocol: Protocol, fragment: bool = False):
    """Validate message structure only; fragment changes input wrapping, not the criteria."""
    messages, prefix = payload, None
    if fragment and isinstance(payload, dict):
        if 'component' in payload:
            messages = [{'version': 'v1.0', 'updateComponents': {'surfaceId': 'fragment', 'components': [payload]}}]
            prefix = '/0/updateComponents/components/0'
        else:
            messages, prefix = [payload], '/0'
    elif fragment and isinstance(payload, list) and payload and all(
            isinstance(c, dict) and 'component' in c for c in payload):
        messages = [{'version': 'v1.0', 'updateComponents': {'surfaceId': 'fragment', 'components': payload}}]
        prefix = '/0/updateComponents/components'
    if not isinstance(messages, list) or not messages:
        return [Diagnostic('input.not_array', 'error', '', 'Input must be a nonempty A2UI message array')], {}
    # JSON disallows non-finite numbers; impose no extra policy on tree depth or message order.
    pending = [(messages, '')]
    while pending:
        value, pointer = pending.pop()
        if isinstance(value, float) and not math.isfinite(value):
            return [Diagnostic('input.non_finite_number', 'error', pointer, 'JSON does not allow NaN or Infinity')], {}
        if isinstance(value, (dict, list)):
            entries = value.items() if isinstance(value, dict) else enumerate(value)
            pending.extend((child, pointer + '/' + escape_pointer(key)) for key, child in entries)
    diagnostics = []
    for index, message in enumerate(messages):
        diagnostics += protocol.schema_engine.check(message, 'agent-to-renderer.json', pointer=f'/{index}')
    if prefix:
        for d in diagnostics:
            pointer = d['pointer']
            d['pointer'] = pointer[len(prefix):] if pointer == prefix or pointer.startswith(prefix + '/') else ''
    return diagnostics, {}  # Preserve output compatibility without merging state or rendering metrics.


# ---------------------------------------------------------------- --explain

TOKEN_TYPES = ("ColorToken", "SizeToken", "IconName")

# Handwritten pitfalls cover real failure modes; fields and enums remain protocol-derived.
NOTES: dict[str, list[str]] = {
    "ButtonGroup": ["buttons[] is an inline object array; each item's host writeback belongs to its own metadata, not the parent."],
    "ChoicePicker": ["options[] is inline; both single- and multi-select value bindings hold string arrays.",
                     "variant selects the mode. Only the default dropdown fires action on confirmation; checkbox and chips update local state."],
    "Table": ["data is a structured object containing meta and data; pagination is local, so provide all rows at once."],
    "Chart": ["data.type selects chart shape; data.data is an array of {x,y} points, not component IDs."],
    "ImageUpload": ["value binds an array of image URLs; upload and deletion update local state, which submit context can read.",
                    "action fires only after successful upload, not failure or cancellation, and supports only the event branch."],
    "CheckableImageList": ["images accepts strings or {url} objects; value is an array of selected image indexes."],
    "Tabs": ["Child references are in tabs[].child, not top-level children; each child must identify an existing component."],
    "Stack": ["The base is child; overlay references are in overlays[].child. Both paths must resolve."],
    "Loop": ["children uses the {componentId, path} template form. Relative bindings are valid only inside that template context."],
    "Row": ["align=stretch stretches row children on the cross axis; the target client implements the exact layout.",
            "Wrap or group many adjacent items for the target width; component count is not a protocol limit."],
    "Column": ["children arranges vertical content. An empty array is structurally valid; retain it only if useful."],
    "Image": ["There is no protocol-level URL-scheme allowlist; non-HTTPS support depends on the host.",
              "smallFeature is at most 96×96; with text, keep title within two lines and summary within three to avoid distortion."],
    "Markdown": ["The current protocol describes <a atId> as plain-text fallback, without a guaranteed clickable mention. Put only the display name inside the tag.",
                 "Split unrelated topics into Column or Card groups instead of one Markdown block."],
    "Button": ["Select variant by action priority. The validator does not impose a primary-button count.",
               "Put business IDs such as bizId in action.event.context so callbacks can be attributed.",
               "child references a Text component ID; do not put the label directly on Button."],
    "Text": ["Prefer a registered full sizeToken ID ending in __font_size; host fallback determines other strings.",
             "Prefer colorToken. Use only contracted customLightColor/customDarkColor for custom theme colors; do not add color."],
    "Tag": ["Keep theme consistent with meaning; consult this protocol's enum."],
    "CardHeader": ["Groups an avatar, title, and trailing information when the content calls for it."],
    "ColorToken": ["Use a given token for one meaning within a card.",
                   "Only registered enum values are valid; hex and CSS colors are rejected."],
    "SizeToken": ["Prefer a registered full ID. Other strings are allowed by the protocol but subject to host fallback."],
    "IconName": ["Use the full *_L_outlined or *_L_filled name; the rendering font determines the icon."],
    "openUrl": ["Choose a URL supported by the host; non-HTTPS compatibility is a host concern, not a protocol rejection."],
    "promptText": ["The input parameter is initialValue; text is an output field, not an input."],
}


def _short(desc, limit=96):
    desc = (desc or "").strip().replace("\n", " ")
    return desc if len(desc) <= limit else desc[:limit - 1] + "…"


def _type_label(protocol: Protocol, spec, shard):
    """Prefer a common-type name, then a JSON type; include enums and accepted forms."""
    ref = spec.get("$ref") if isinstance(spec, dict) else None
    types, enum = protocol.accepted_types(spec, shard)
    kinds = protocol.slot_kinds(spec, shard)
    if isinstance(ref, str):
        label = ref.rsplit("/", 1)[-1]
    else:
        label = "|".join(sorted(types)) if types else "any"
    if enum:
        vals = sorted(enum)
        label += " {" + ", ".join(vals[:6]) + (", …" if len(vals) > 6 else "") + "}"
    accepts = [k for k in ("binding", "function") if k in kinds]
    return label, accepts, enum


def _schema_source(protocol, spec, shard):
    """Locate public source by object identity, preserving array indexes and JSON Pointer escaping."""
    if not hasattr(protocol, '_schema_sources'):
        sources = {}
        def visit(node, owner, pointer):
            if isinstance(node, dict):
                sources[(owner, id(node))] = owner + '#' + pointer
                for key, child in node.items():
                    visit(child, owner, pointer + '/' + escape_pointer(key))
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    visit(child, owner, pointer + '/' + str(index))
        for owner, doc in protocol.docs.items():
            visit(doc, owner, '')
        protocol._schema_sources = sources
    return protocol._schema_sources.get((shard, id(spec)))


def _shape(protocol, spec, shard, depth=0, seen=frozenset()):
    """Expand local field shapes; dynamic values, function sets, and Tokens bound the lookup."""
    if not isinstance(spec, dict):
        return spec
    result = {k: copy.deepcopy(v) for k, v in spec.items() if k in (
        'type', 'const', 'enum', 'required', 'additionalProperties', 'unevaluatedProperties',
        'minimum', 'maximum', 'minItems', 'maxItems', 'minLength', 'maxLength', 'pattern')}
    # Field tables show top-level descriptions; retain nested descriptions for open strings.
    if depth and spec.get('description'):
        result['description'] = _short(spec['description'], 320)
    def locate():
        source = _schema_source(protocol, spec, shard)
        if source:
            result['source'] = source
    if depth and len(spec.get('description', '')) > 320:
        locate()
    ref = spec.get('$ref')
    if ref:
        name = ref.rsplit('/', 1)[-1]
        result['ref'] = name
        if name == 'FunctionCall':
            result['lookup'] = 'explain <function name>'
            locate()
            return result
        if name.startswith('Dynamic') or name in TOKEN_TYPES or name in ('HostStaticValueTree', 'HostCompiledValueTree'):
            result['forms'] = sorted(protocol.slot_kinds(spec, shard))
            if 'function' in result['forms']:
                result['functionKind'] = 'value'
            return result
        key = (shard, ref)
        if key in seen or depth >= 4:
            result['lookup'] = 'explain ' + name
            locate()
            return result
        target, owner = protocol.deref(ref, shard)
        if target is not None:
            result.update(_shape(protocol, target, owner, depth + 1, seen | {key}))
        return result
    if depth >= 4 and any(k in spec for k in ('properties', 'items', 'oneOf', 'anyOf', 'allOf')):
        result['detailDeferred'] = True
        locate()
        return result
    if 'properties' in spec:
        result['properties'] = {name: _shape(protocol, child, shard, depth + 1, seen)
                                for name, child in spec['properties'].items()}
    if 'items' in spec:
        result['items'] = _shape(protocol, spec['items'], shard, depth + 1, seen)
    for key in ('oneOf', 'anyOf', 'allOf'):
        if key in spec:
            result[key] = [_shape(protocol, branch, shard, depth + 1, seen) for branch in spec[key]]
    return result


def _field_details(protocol, spec, shard):
    details = {'description': spec.get('description', '') if isinstance(spec, dict) else ''}
    if isinstance(spec, dict):
        # Field tables already have types; add shapes for objects, arrays, and branching common types.
        ref_name = spec.get('$ref', '').rsplit('/', 1)[-1]
        if any(k in spec for k in ('properties', 'items', 'oneOf', 'anyOf', 'allOf', 'enum', 'const')) or ref_name in ('Action', 'ChildList', 'DataBinding', 'CheckRule'):
            details['shape'] = _shape(protocol, spec, shard)
    return details


def _event_only_slots(protocol, name):
    slots = set()
    for rule in protocol.validation_rules['references']:
        path = rule['path']
        if (rule['validationRef'].endswith('/EventAction') and path[:2] == ['components', name]):
            parts = []
            for index, key in enumerate(path):
                if key == 'properties' and index + 1 < len(path):
                    parts.append(str(path[index + 1]))
                elif key == 'items' and parts:
                    parts[-1] += '[]'
            slots.add('.'.join(parts))
    return sorted(slots)


def _restrict_event_shape(shape):
    """Narrow Action by slot constraints, including depth-limited required-only branches."""
    shape['allowedActions'] = ['event']
    if 'oneOf' in shape:
        shape['oneOf'] = [branch for branch in shape['oneOf']
                          if 'event' in branch.get('properties', {}) or 'event' in branch.get('required', [])]


def _apply_event_only_slots(fields, slots):
    """Map rule paths into explain shapes so nested event-only slots omit functionCall."""
    by_name = {field['name']: field for field in fields}
    for slot in slots:
        parts = slot.replace('[]', '.[]').split('.')
        field = by_name.get(parts[0])
        if not field:
            continue
        shape = field.get('shape')
        for part in parts[1:]:
            if not isinstance(shape, dict):
                break
            shape = shape.get('items') if part == '[]' else shape.get('properties', {}).get(part)
        if isinstance(shape, dict):
            _restrict_event_shape(shape)
        if len(parts) == 1:
            field['allowedActions'] = ['event']


def _synth(protocol: Protocol, spec, shard, depth=0):
    """Synthesize schema placeholders: child IDs, bindings, first enum value, and required object fields."""
    if not isinstance(spec, dict) or depth > 8:
        return "REPLACE_VALUE"
    ref = spec.get("$ref")
    if isinstance(ref, str):
        if ref.endswith("/ComponentId") or ref.endswith("/Child"):
            return "REPLACE_CHILD_ID"
        if "ChildList" in ref:
            return ["REPLACE_CHILD_ID"]
        if ref.endswith("/DataBinding"):
            return {"path": "/REPLACE_PATH"}
        target, tshard = protocol.deref(ref, shard)
        return _synth(protocol, target, tshard, depth + 1) if target is not None else "REPLACE_VALUE"
    if "const" in spec:
        return spec["const"]
    if "enum" in spec:
        return sorted(spec["enum"])[0]
    typ = spec.get("type")
    if typ == "array" or "items" in spec:
        return [_synth(protocol, spec.get("items", {}), shard, depth + 1)]
    if typ == "object" or "properties" in spec or "allOf" in spec:
        acc = protocol._flatten(spec, shard)
        return {k: _synth(protocol, *acc["props"][k], depth + 1) for k in sorted(acc["required"]) if k in acc["props"]}
    for key in ("oneOf", "anyOf"):
        branches = spec.get(key) or []
        for branch in branches:                     # Prefer literal branches over binding or function forms.
            bref = branch.get("$ref", "") if isinstance(branch, dict) else ""
            if "DataBinding" in bref or "FunctionCall" in bref:
                continue
            return _synth(protocol, branch, shard, depth + 1)
        if branches:
            return _synth(protocol, branches[0], shard, depth + 1)
    if typ == "string":
        return "REPLACE_TEXT"
    if typ in ("number", "integer"):
        return spec.get("minimum", 0)
    if typ == "boolean":
        return False
    if isinstance(typ, list) and typ:
        return _synth(protocol, {"type": typ[0]}, shard, depth + 1)
    return "REPLACE_TEXT"


def _placeholder(protocol: Protocol, component, prop, spec, shard):
    if prop.lower().endswith("url"):
        return "https://example.com/REPLACE_WITH_RESOURCE"
    return _synth(protocol, spec, shard)


def _explain_component(protocol: Protocol, name):
    schema, shard = protocol.components[name]
    acc = protocol.component_props(name)
    required = {r for r in acc["required"] if r != "component"}
    fields = []
    for prop, (spec, pshard) in acc["props"].items():
        if prop == "component":
            continue
        label, accepts, _ = _type_label(protocol, spec, pshard)
        fields.append({"name": prop, "type": label, "required": prop in required, "accepts": accepts,
                       **_field_details(protocol, spec, pshard)})
    fields.sort(key=lambda f: (not f["required"], f["name"]))
    example = {"id": "REPLACE_ID", "component": name}
    for prop, (spec, pshard) in acc["props"].items():
        if prop in required and prop != "id":
            example[prop] = _placeholder(protocol, name, prop, spec, pshard)
    child_refs = [{"path": ".".join(path), "kind": kind} for path, kind in protocol.ref_paths(name)]
    event_only = _event_only_slots(protocol, name)
    _apply_event_only_slots(fields, event_only)
    result = {"kind": "component", "name": name,
              "tier": SHARD_LABELS[shard], "shard": shard,
              "description": schema.get("description", ""),
              "required": sorted(required), "fields": fields, "childRefs": child_refs,
              "example": example, "notes": NOTES.get(name, []), "eventOnlySlots": event_only}
    action_fields = [prop for prop, (spec, _) in acc['props'].items()
                     if spec.get('$ref', '').endswith('/Action') and prop not in event_only]
    if action_fields or name == 'ButtonGroup':
        result['hostWriteback'] = {
            'path': ('buttons[].metadata.extensions.dt_actionBindingsV1.action.resultPath'
                     if name == 'ButtonGroup' else 'metadata.extensions.dt_actionBindingsV1.<slot>.resultPath'),
            'slots': action_fields if name != 'ButtonGroup' else ['action'],
            'binding': _shape(protocol, {'$ref': './common-types-extended.json#/$defs/HostActionBinding'}, shard),
            'note': 'Only for slots supporting host-action writeback; each inline ButtonGroup item uses its own metadata.'}
    return result



def _explain_function(protocol: Protocol, call):
    spec = protocol.function_spec(call)
    schema, shard = protocol.functions[call]
    args = []
    for prop, (aspec, ashard) in spec["args_props"].items():
        label, accepts, _ = _type_label(protocol, aspec, ashard)
        args.append({"name": prop, "type": label, "required": prop in spec["args_required"], "accepts": accepts,
                     **_field_details(protocol, aspec, ashard)})
    args.sort(key=lambda f: (not f["required"], f["name"]))
    example = {"call": call, "args": {}}
    for prop, (aspec, ashard) in spec["args_props"].items():
        if prop in spec["args_required"]:
            example["args"][prop] = _placeholder(protocol, None, prop, aspec, ashard)
    if call in protocol.host_actions:
        example["catalogId"] = HOST_CATALOG_ID
    description, marker, result_block = schema.get("description", "").partition("\n\nResult schema:\n")
    result_schema = None
    if marker:
        match = re.fullmatch(r"```json\n([\s\S]+)\n```", result_block)
        if not match:
            raise ProtocolError(f"Malformed host-function result description: {call}")
        result_schema = json.loads(match.group(1))
    tier = SHARD_LABELS.get(shard, "System function")
    return {"kind": "function", "name": call, "tier": tier, "shard": shard,
            "returnType": spec["returnType"], "allowedCallers": schema.get("allowedCallers"),
            "usage": "action.functionCall" if call in protocol.host_actions or spec["returnType"] == "void" else "value expression",
            "hostAction": call in protocol.host_actions, "resultSchema": result_schema,
            "description": description,
            "args": args, "argsClosed": spec["args_closed"], "example": example,
            "notes": NOTES.get(call, [])}


def _explain_token(protocol: Protocol, kind):
    defs = protocol.docs["common-types-visual.json"]["$defs"][kind]
    branches = defs.get("oneOf") or defs.get("anyOf") or []
    items = [{"name": b["const"], "description": _short(b.get("description"))} for b in branches if "const" in b]
    groups: dict[str, list[str]] = {}
    if kind == "ColorToken":
        for item in items:
            m = re.match(r"(?:extended_|common_)?([a-z]+)", item["name"])
            groups.setdefault(m.group(1) if m else "other", []).append(item["name"])
    return {"kind": "token", "name": kind, "count": len(items),
            "description": _short(defs.get("description"), 240),
            "items": items, "groups": groups, "notes": NOTES.get(kind, [])}


def common_type_names(protocol: Protocol):
    """Queryable common types from three $defs sets, excluding Token types handled separately."""
    out = []
    for shard in COMMON_SHARDS:
        out += [k for k in protocol.docs[shard].get("$defs", {}) if k not in TOKEN_TYPES]
    return out


def _explain_type(protocol: Protocol, name):
    for shard in COMMON_SHARDS:
        defs = protocol.docs[shard].get("$defs", {})
        if name in defs:
            schema = defs[name]; break
    else:
        return None
    acc = protocol._flatten(schema, shard)
    fields = []
    for prop, (spec, pshard) in acc["props"].items():
        label, accepts, _ = _type_label(protocol, spec, pshard)
        fields.append({"name": prop, "type": label, "required": prop in acc["required"], "accepts": accepts,
                       **_field_details(protocol, spec, pshard)})
    fields.sort(key=lambda f: (not f["required"], f["name"]))
    # Show required and key fields of oneOf/anyOf alternatives.
    branches = []
    for key in ("oneOf", "anyOf"):
        for b in schema.get(key, []) or []:
            if not isinstance(b, dict):
                continue
            ref = b.get("$ref")
            if isinstance(ref, str):
                branches.append({"rule": key, "ref": ref.rsplit("/", 1)[-1]})
            else:
                branches.append({"rule": key, "required": sorted(b.get("required", []) or []),
                                 "keys": sorted((b.get("properties") or {}).keys()),
                                 "type": b.get("type"), "const": b.get("const"), "enum": b.get("enum")})
    return {"kind": "type", "name": name, "shard": shard,
            "description": schema.get("description", ""),
            "required": sorted(acc["required"]), "fields": fields, "branches": branches,
            "shape": _shape(protocol, schema, shard),
            "example": _synth(protocol, schema, shard), "notes": NOTES.get(name, [])}


def _explain_token_item(protocol: Protocol, name):
    for kind in TOKEN_TYPES:
        defs = protocol.docs["common-types-visual.json"]["$defs"][kind]
        for b in defs.get("oneOf") or defs.get("anyOf") or []:
            if b.get("const") == name:
                return {"kind": "token-item", "name": name, "type": kind,
                        "description": _short(b.get("description"), 240), "notes": NOTES.get(kind, [])}
    return None

def explain(protocol: Protocol, name: str) -> dict:
    """Explain one component, function, or Token with fields, example, and pitfalls."""
    key = (name or "").strip()
    lower = key.lower()
    by_lower = {k.lower(): k for k in protocol.components}
    fn_lower = {k.lower(): k for k in protocol.functions}
    tok_lower = {k.lower(): k for k in TOKEN_TYPES}
    if lower in by_lower:
        return _explain_component(protocol, by_lower[lower])
    if lower in fn_lower:
        return _explain_function(protocol, fn_lower[lower])
    if lower in tok_lower:
        return _explain_token(protocol, tok_lower[lower])
    item = _explain_token_item(protocol, key)          # One Token, such as common_red1_color.
    if item:
        return item
    ty_lower = {k.lower(): k for k in common_type_names(protocol)}
    if lower in ty_lower:                              # Common type, such as Action or ChildList.
        return _explain_type(protocol, ty_lower[lower])
    import difflib
    pool = list(protocol.components) + list(protocol.functions) + list(TOKEN_TYPES) + common_type_names(protocol)
    return {"kind": "unknown", "name": key,
            "suggestions": difflib.get_close_matches(key, pool, n=5, cutoff=0.5)}


def render_explain(result: dict) -> str:
    out = []
    kind = result["kind"]
    if kind == "unknown":
        out.append(f"Unknown name {result['name']!r}")
        if result["suggestions"]:
            out.append("  Did you mean: " + ", ".join(result["suggestions"]))
        out.append("  Supported lookups: bundled components, functions (including @index), "
                   "ColorToken / SizeToken / IconName and individual Tokens, and common types "
                   "(Action / DataBinding / ChildList / CheckRule …)")
        return "\n".join(out)
    if kind == "token-item":
        out.append(f"{result['name']}  ← {result['type']}  {result['description']}")
        for n in result["notes"]:
            out.append(f"  - {n}")
        return "\n".join(out)
    if kind == "type":
        out.append(f"{result['name']} (common type, {result['shard']})  {result['description']}")
        if result["branches"]:
            parts = []
            for b in result["branches"]:
                if "ref" in b:
                    parts.append(b["ref"])
                elif b.get("required"):
                    parts.append("required " + "+".join(b["required"]))
                elif b.get("const") is not None:
                    parts.append(f"const {b['const']}")
                elif b.get("enum"):
                    parts.append("enum " + "/".join(map(str, b["enum"][:4])))
                else:
                    parts.append(str(b.get("type") or "…"))
            out.append(f"  Alternatives ({result['branches'][0]['rule']}): " + " | ".join(parts))
        if result["fields"]:
            out.append("  Fields:")
            for f in result["fields"]:
                star = "*" if f["required"] else " "
                tags = " ".join({"binding": "binding", "function": "function"}[a] for a in f["accepts"])
                out.append(f"    {star} {f['name']:<20} {f['type']:<34} {tags:<10} {f['description']}")
    elif kind == "component":
        out.append(f"{result['name']} ({result['tier']})  {result['description']}")
        out.append(f"  Required: {', '.join(result['required']) or 'none besides id / component'}")
        out.append("  Fields:")
        for f in result["fields"]:
            star = "*" if f["required"] else " "
            tags = " ".join({"binding": "binding", "function": "function"}[a] for a in f["accepts"])
            out.append(f"    {star} {f['name']:<20} {f['type']:<34} {tags:<10} {f['description']}")
        if result["childRefs"]:
            out.append("  Child references: " + "; ".join(f"{r['path']} ({r['kind']})" for r in result["childRefs"]))
    elif kind == "function":
        host = ", host action (catalogId required)" if result["hostAction"] else ""
        out.append(f"{result['name']} ({result['tier']}{host})  {result['description']}")
        out.append(f"  Returns: {result['returnType']}   Caller: {result['allowedCallers'] or 'rendererOnly'}"
                   f"   Arguments: {'closed' if result['argsClosed'] else 'open'}")
        if result.get("resultSchema") is not None:
            out.append("  Result shape: " + json.dumps(result["resultSchema"], ensure_ascii=False))
        out.append("  Arguments:")
        for f in result["args"]:
            star = "*" if f["required"] else " "
            tags = " ".join({"binding": "binding", "function": "function"}[a] for a in f["accepts"])
            out.append(f"    {star} {f['name']:<16} {f['type']:<34} {tags:<10} {f['description']}")
    else:
        out.append(f"{result['name']}  {result['count']} items  {result['description']}")
        if result["groups"]:
            for g, names in result["groups"].items():
                out.append(f"  {g:<10} {', '.join(names)}")
        else:
            for item in result["items"]:
                out.append(f"    {item['name']:<48} {item['description']}")
    for field in result.get('fields', []) + result.get('args', []):
        if 'shape' in field:
            out.append('  ' + field['name'] + ' shape: ' + json.dumps(field['shape'], ensure_ascii=False))
    if result.get('shape'):
        out.append('  Shape: ' + json.dumps(result['shape'], ensure_ascii=False))
    if result.get('eventOnlySlots'):
        out.append('  Event-only slots: ' + ', '.join(result['eventOnlySlots']))
    if result.get('hostWriteback'):
        out.append('  Host writeback: ' + json.dumps(result['hostWriteback'], ensure_ascii=False))
    if kind not in ("token", "token-item"):
        out.append("  Minimal structural example (replace placeholders and assemble messages for the delivery scenario):")
        out.append("    " + json.dumps(result["example"], ensure_ascii=False))
    if result["notes"]:
        out.append("  Common pitfalls:")
        for n in result["notes"]:
            out.append(f"    - {n}")
    return "\n".join(out)


def explain_many(protocol, names, compact=False):
    """Build a batch view without changing the single-name explain shape."""
    contracts = [explain(protocol, name) for name in dict.fromkeys(names)]
    result = {"kind": "bundle", "formatVersion": 1, "contracts": contracts}
    if not compact:
        return result
    import hashlib
    from collections import Counter
    canonical = lambda obj: json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    counts = Counter()
    def count(node):
        if isinstance(node, dict):
            key = canonical(node)
            if len(key.encode('utf-8')) >= 256:
                counts[key] += 1
            for value in node.values():
                count(value)
        elif isinstance(node, list):
            for value in node:
                count(value)
    count(contracts)
    definitions = {}
    def pack(node, replace=True):
        if isinstance(node, dict):
            key = canonical(node)
            if replace and counts[key] > 1:
                name = hashlib.sha256(key.encode()).hexdigest()[:16]
                if name not in definitions:
                    definitions[name] = pack(node, False)
                return {"$contractRef": name}
            return {k: pack(v) for k, v in node.items()}
        if isinstance(node, list):
            return [pack(v) for v in node]
        return node
    result['contracts'] = pack(contracts)
    result['definitions'] = definitions
    return result


def _component_reference_diagnostics(nodes, protocol, surface_pointer, surface_id):
    """Check reachable references in the final snapshot; template edges check existence only."""
    if 'root' not in nodes:
        return [Diagnostic('reference.missing_root', 'error', surface_pointer,
                            f'Surface {surface_id} is missing the root component in its final snapshot')]

    def slots(node, path, pointer):
        if not path:
            yield node, pointer
        elif path[0] == '[]' and isinstance(node, list):
            for index, item in enumerate(node):
                yield from slots(item, path[1:], pointer + '/' + str(index))
        elif isinstance(node, dict) and path[0] in node:
            yield from slots(node[path[0]], path[1:], pointer + '/' + escape_pointer(path[0]))

    edges = {}
    for cid, (component, pointer) in nodes.items():
        edges[cid] = []
        for path, kind in protocol.ref_paths(component.get('component')):
            for value, location in slots(component, path, pointer):
                if kind == 'id' and isinstance(value, str):
                    edges[cid].append((value, location, False))
                elif kind == 'list' and isinstance(value, list):
                    edges[cid].extend((child, location + '/' + str(i), False)
                                      for i, child in enumerate(value) if isinstance(child, str))
                elif kind == 'list' and isinstance(value, dict) and isinstance(value.get('componentId'), str):
                    edges[cid].append((value['componentId'], location + '/componentId', True))

    diagnostics, reachable, pending = [], set(), ['root']
    while pending:
        cid = pending.pop()
        if cid in reachable:
            continue
        reachable.add(cid)
        for target, pointer, _ in edges[cid]:
            if target not in nodes:
                diagnostics.append(Diagnostic('reference.dangling_child', 'error', pointer,
                                               f'Surface {surface_id} references undefined child component {target}'))
            elif target not in reachable:
                pending.append(target)

    # Iterative three-color DFS avoids treating shared DAG children or deep chains as cycles.
    colors = {}
    for start in sorted(reachable):
        if colors.get(start):
            continue
        colors[start] = 1
        stack = [(start, iter(edges[start]))]
        while stack:
            cid, iterator = stack[-1]
            edge = next(iterator, None)
            if edge is None:
                colors[cid] = 2
                stack.pop()
                continue
            target, pointer, template = edge
            if template or target not in nodes:
                continue
            if colors.get(target) == 1:
                diagnostics.append(Diagnostic('reference.cycle', 'error', pointer,
                                               f'Surface {surface_id} has a static component-reference cycle: {cid} → {target}'))
            elif not colors.get(target):
                colors[target] = 1
                stack.append((target, iter(edges[target])))
    for cid in sorted(set(nodes) - reachable):
        diagnostics.append(Diagnostic('reference.unreachable_component', 'warning', nodes[cid][1] + '/id',
                                       f'Component {cid} is unreachable from root or its templates; retain it if needed for a later update'))
    return diagnostics


def preflight(messages, protocol, mode='new-card'):
    """Offline preflight after Schema validation; never sends, rewrites, or evaluates expressions."""
    import base64
    import binascii
    from urllib.parse import unquote_to_bytes
    diagnostics = []
    def error(code, pointer, message):
        diagnostics.append(Diagnostic(code, 'error', pointer, message))
    def components(body, pointer, state):
        seen = set()
        for index, component in enumerate(body.get('components', [])):
            cid = component['id']
            location = pointer + '/components/' + str(index)
            if cid in seen:
                error('reference.duplicate_component_id', location + '/id',
                      f'Component {cid} is defined twice in one message; replacing an ID across messages is valid')
            seen.add(cid)
            state['nodes'][cid] = (component, location)
    states = {}
    for i, message in enumerate(messages):
        op = next((key for key in ('createSurface', 'updateDataModel', 'updateComponents', 'deleteSurface') if key in message), None)
        body = message.get(op, {})
        sid = body.get('surfaceId')
        pointer = f'/{i}/{op}'
        if mode != 'new-card':
            continue
        if op == 'createSurface':
            if sid in states:
                error('delivery.surface_duplicated', pointer, 'A new-card snapshot cannot create the same Surface twice')
            if body.get('catalogId') != protocol.docs['catalog-components-common.json']['catalogId']:
                error('delivery.catalog_id', pointer + '/catalogId', 'A new card must explicitly use the bundled catalogId')
            states[sid] = {'data': isinstance(body.get('dataModel'), dict),
                           'components': isinstance(body.get('components'), list),
                           'nodes': {}, 'pointer': pointer + '/surfaceId'}
            components(body, pointer, states[sid])
        elif op in ('updateDataModel', 'updateComponents'):
            if op == 'updateDataModel' and (not isinstance(body.get('path'), str) or not body['path'].startswith('/')):
                error('delivery.data_model_path', pointer + '/path', 'The current DingTalk creation route requires an explicit data path beginning with /')
            if sid not in states:
                error('delivery.surface_not_created', pointer + '/surfaceId', 'Create the Surface before updating it in a new-card snapshot')
            elif op == 'updateComponents':
                states[sid]['components'] = True
                components(body, pointer, states[sid])
            elif body.get('path') == '/':
                states[sid]['data'] = 'value' in body
            elif 'value' in body:
                states[sid]['data'] = True
        else:
            error('delivery.unsupported_create_operation', pointer, 'The current new-card route rejects this operation; send lifecycle deltas separately')
    if mode == 'new-card':
        if not states:
            error('delivery.create_surface_missing', '', 'The new-card file is missing createSurface')
        for sid, state in sorted(states.items()):
            if not state['data']:
                error('delivery.data_model_missing', '', f'Surface {sid} lacks data initialization; a static new card may set root path / to an empty object')
            if not state['components']:
                error('delivery.components_missing', '', f'Surface {sid} is missing updateComponents')
            else:
                diagnostics.extend(_component_reference_diagnostics(state['nodes'], protocol, state['pointer'], sid))
    # Scan component resource fields only; ordinary text containing data: is not a resource.
    resource_keys = {'url', 'darkUrl', 'imageUrl', 'posterUrl', 'coverUrl', 'images'}
    def resource(node, pointer, field=''):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ('metadata', 'action', 'checks') or key.startswith('on'):
                    continue
                resource(value, pointer + '/' + key.replace('~', '~0').replace('/', '~1'), key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                resource(value, pointer + '/' + str(index), field)
        elif isinstance(node, str) and field in resource_keys and node.lower().startswith('data:'):
            header, sep, content = node.partition(',')
            if not sep or re.search(r'%(?![0-9a-fA-F]{2})', content):
                error('resource.invalid_data_uri', pointer, 'The data URI lacks a separator or contains invalid escaping')
                return
            if header.lower().endswith(';base64'):
                try:
                    decoded = base64.b64decode(unquote_to_bytes(content), validate=True)
                    if not decoded:
                        raise ValueError('empty')
                except (binascii.Error, ValueError):
                    error('resource.invalid_base64', pointer, 'Resource Base64 is empty or damaged; re-encode the original file instead of copying tool logs')
    for i, message in enumerate(messages):
        for operation in ('createSurface', 'updateComponents'):
            for j, component in enumerate(message.get(operation, {}).get('components', [])):
                resource(component, f'/{i}/{operation}/components/{j}')
    unverified = (['template expansion and binding evaluation', 'intermediate-frame references and runtime state'] if mode == 'new-card'
                  else ['component-reference closure and binding evaluation'])
    return {'mode': mode, 'valid': not any(d['severity'] == 'error' for d in diagnostics), 'diagnostics': diagnostics,
            'renderingVerified': False,
            'unverified': unverified + ['media decoding and client resource loading', 'client rendering and interaction']}


def main(argv=None):
    # Use UTF-8 text and ASCII-escaped JSON for pipes with legacy code pages.
    # Configure only at the CLI entrypoint; imports and StringIO tests remain unaffected.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description="Offline A2UI card validator (DWS not required)")
    parser.add_argument("file", nargs="?", help="JSON file containing an A2UI message array")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--strict", action="store_true", help=argparse.SUPPRESS)  # Legacy compatibility; no extra checks.
    parser.add_argument("--fragment", action="store_true",
                        help="Accept one component, a component array, or one message under the same structural rules")
    parser.add_argument("--self-check", action="store_true", help="Check bundled protocol integrity only")
    parser.add_argument("--protocol-dir", default=PROTOCOL_DIR)
    parser.add_argument("--validation-rules", default=VALIDATION_RULES, help="Supplementary validation rules matching the protocol")
    parser.add_argument("--explain", metavar="NAME",
                        help="Explain one component, function, common type, Token type, or Token name without validating a file")
    parser.add_argument('--explain-many', nargs='+', metavar='NAME', help='Query several names in one bundle')
    parser.add_argument('--compact', action='store_true', help='For explain-many only: move repeated structures into definitions')
    parser.add_argument('--preflight', choices=['new-card', 'resources'], help='Explicit offline preflight after structural validation; does not change valid')
    args = parser.parse_args(argv)
    if (args.compact and not args.explain_many or args.explain_many and (args.explain or args.file or args.self_check)
            or args.preflight and (not args.file or args.fragment or args.explain or args.explain_many or args.self_check)):
        parser.error('Batch lookup and file validation are exclusive; compact requires explain-many; preflight requires a complete message file')

    def failure(code, message):
        d = Diagnostic(code, "error", "", message)
        if args.format == "json":
            print(json.dumps({"valid": False, "diagnostics": [d], "metrics": {}, "renderingVerified": False}, ensure_ascii=True))
        else:
            print(message, file=sys.stderr)
        return 2

    try:
        protocol = Protocol(args.protocol_dir, args.validation_rules)
    except DependencyError as error:
        return failure("input.dependencies_unavailable", str(error))
    except PythonEnvironmentError as error:
        return failure("input.python_environment_unavailable", str(error))
    except (ProtocolError, OSError, ValueError, RecursionError, KeyError, TypeError) as error:
        return failure("input.protocol_unavailable", f"Protocol package unavailable: {error}")

    if args.explain_many:
        result = explain_many(protocol, args.explain_many, args.compact)
        print(json.dumps(result, ensure_ascii=True, separators=(',', ':') if args.compact else None,
                         indent=None if args.compact else 2))
        return 1 if any(explain(protocol, name)['kind'] == 'unknown' for name in args.explain_many) else 0

    if args.explain:
        result = explain(protocol, args.explain)
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            print(render_explain(result))
        return 0 if result["kind"] != "unknown" else 1

    if args.self_check or not args.file:
        try:
            protocol.schema_engine.self_check()
        except Exception as error:
            return failure("input.protocol_unavailable", f"Invalid protocol Schema: {error}")
        report = {
            "ok": True,
            "protocolDir": protocol.dir,
            "components": len(protocol.components),
            "functions": len(protocol.functions),
            "colorTokens": len(protocol.color_tokens),
            "componentsWithChildren": len(protocol._ref_paths),
            "digests": protocol.manifest(),
        }
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            print(f"Protocol package {protocol.dir}")
            print(f"  {report['components']} components; {report['componentsWithChildren']} may contain children")
            print(f"  {report['colorTokens']} ColorTokens")
            for name, digest in report["digests"].items():
                print(f"  {name:32} sha256:{digest}")
        return 0

    try:
        # Accept a BOM from common Windows editors only at the user-file boundary.
        with open(args.file, encoding="utf-8-sig") as handle:
            messages = json.load(handle, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"JSON does not allow {x}")))
    except (OSError, ValueError, RecursionError) as error:
        return failure("input.invalid_json", f"Cannot read or parse {args.file}: {error}")
    try:
        diagnostics, metrics = lint(messages, protocol, fragment=args.fragment)
    except ValidationLimitError as error:
        return failure("input.validation_incomplete", str(error))
    except ProtocolError as error:
        return failure("input.protocol_unavailable", str(error))
    errors = [d for d in diagnostics if d["severity"] == "error"]
    valid = not errors
    report = {"valid": valid, "diagnostics": diagnostics, "metrics": metrics, "renderingVerified": False}
    checked = preflight(messages, protocol, args.preflight) if args.preflight and valid else None
    if checked is not None:
        report['preflight'] = checked

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        for item in diagnostics:
            mark = "✗" if item["severity"] == "error" else "!"
            print(f"  {mark} [{item['code']}] {item['pointer']}  {item['message']}")
            if item["hint"]:
                print(f"      → {item['hint']}")
        print(f"\n{'Passed' if valid else 'Failed'}: {len(errors)} structural errors")
        print('Only JSON and A2UI protocol structure were checked; runtime state, client rendering, and interactions remain unverified.')
        if checked is not None:
            print(json.dumps({'preflight': checked}, ensure_ascii=False, indent=2))
    return 0 if valid and (checked is None or checked['valid']) else 1


if __name__ == "__main__":
    sys.exit(main())
