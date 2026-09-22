#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Preserve regression coverage and fragment boundaries after moving pattern JSON out of authoring docs."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import conformance

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills/dingtalk-aicard/scripts"))
from aicard_lint import Protocol, lint


class PatternFixturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "shared/fixtures/patterns"
        shutil.copytree(ROOT / "shared/fixtures/patterns", self.directory)

    def test_missing_case_is_rejected(self):
        (self.directory / "approval-terminal.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing.*approval-terminal.json"):
            conformance.pattern_fixture_cases(self.root)

    def test_missing_directory_cannot_silently_skip_all_cases(self):
        shutil.rmtree(self.directory)
        with self.assertRaisesRegex(ValueError, "Pattern regression manifest mismatch"):
            conformance.pattern_fixture_cases(self.root)

    def test_unregistered_case_is_rejected(self):
        shutil.copy2(self.directory / "task.json", self.directory / "unregistered.json")
        with self.assertRaisesRegex(ValueError, "unregistered.*unregistered.json"):
            conformance.pattern_fixture_cases(self.root)

    def test_terminal_update_keeps_its_fragment_validation(self):
        cases = conformance.pattern_fixture_cases(self.root)
        self.assertEqual(9, len(cases))
        fragments = [Path(path).name for path, fragment in cases if fragment]
        self.assertEqual(["approval-terminal.json"], fragments)
        messages = json.loads((self.directory / "approval-terminal.json").read_text())
        protocol = Protocol(str(ROOT / "skills/dingtalk-aicard/references/protocol"))

        def errors(fragment):
            diagnostics, _ = lint(messages, protocol, fragment=fragment)
            return [d for d in diagnostics
                    if d["severity"] == "error"]

        self.assertFalse(errors(True))
        self.assertFalse(errors(False), "Message-array structural validation does not require external components to exist")


if __name__ == "__main__":
    unittest.main()
