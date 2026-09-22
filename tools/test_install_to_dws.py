#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Ensure installation changes only AICard entries in the shared audit file."""
import os
from pathlib import Path
import tempfile
import unittest

import install_to_dws as install


class LocalAuditTests(unittest.TestCase):
    def test_upgrade_and_remove_previous_audit_wording(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or install.ROOT) as root:
            path = Path(root) / install.LOCAL_AUDIT
            path.parent.mkdir(parents=True)
            original = install.LOCAL_AUDIT_ANCHOR + install.LEGACY_LOCAL_AUDIT_ENTRIES + '\t}\n}\n'
            path.write_text(original)
            upgraded, changed = install.local_audit_content(root)
            self.assertTrue(changed)
            self.assertIn(install.LOCAL_AUDIT_ENTRIES, upgraded)
            self.assertNotIn(install.LEGACY_LOCAL_AUDIT_ENTRIES, upgraded)
            removed, changed = install.local_audit_content(root, remove=True)
            self.assertTrue(changed)
            self.assertNotIn('aicard.explain', removed)

    def test_add_repeat_remove_preserve_unrelated_content(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or install.ROOT) as root:
            path = Path(root) / install.LOCAL_AUDIT
            path.parent.mkdir(parents=True)
            original = "// existing\n" + install.LOCAL_AUDIT_ANCHOR + '\t\t"other": "keep",\n\t}\n}\n'
            path.write_text(original)
            added, changed = install.local_audit_content(root)
            self.assertTrue(changed)
            self.assertIn('"other": "keep"', added)
            path.write_text(added)
            self.assertEqual((added, False), install.local_audit_content(root))
            self.assertEqual((original, True), install.local_audit_content(root, remove=True))
            path.write_text(original)
            self.assertEqual((original, False), install.local_audit_content(root, remove=True))

    def test_upstream_or_local_conflict_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or install.ROOT) as root:
            path = Path(root) / install.LOCAL_AUDIT
            path.parent.mkdir(parents=True)
            for body in ("changed upstream", install.LOCAL_AUDIT_ANCHOR + '\t"aicard.lint": "custom",\n'):
                path.write_text(body)
                for remove in (False, True):
                    with self.assertRaises(ValueError):
                        install.local_audit_content(root, remove=remove)
                    self.assertEqual(body, path.read_text())


if __name__ == "__main__":
    unittest.main()
