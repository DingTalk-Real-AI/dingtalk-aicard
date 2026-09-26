#!/usr/bin/env python3
"""Tests for the downloadable standalone Skill archive."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import package_skill as package


class PackageSkillTest(unittest.TestCase):
    def setUp(self):
        # Keep fixtures outside the source Skill and the system temporary directory.
        directory = tempfile.TemporaryDirectory(prefix=".package-test-", dir=package.ROOT)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.skill = self.root / "skills" / package.NAME
        for rel in package.tracked_files():
            destination = self.skill / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package.SKILL / rel, destination)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        subprocess.run(["git", "add", "--", "skills"], cwd=self.root, check=True)
        for name, value in (("ROOT", self.root), ("SKILL", self.skill)):
            patcher = patch.object(package, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = self.root / "dist" / "skill.zip"

    def build(self, directory: str, name: str) -> tuple[Path, list[str]]:
        target = Path(directory) / name
        files = package.build(target)
        return target, files

    def test_archive_has_single_top_level_skill_directory(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            target, files = self.build(directory, "skill.zip")
            package.check(target, files)
            with zipfile.ZipFile(target) as archive:
                roots = {name.split("/", 1)[0] for name in archive.namelist()}
            self.assertEqual(roots, {package.NAME})

    def test_archive_contains_license_and_validator(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            target, _ = self.build(directory, "skill.zip")
            with zipfile.ZipFile(target) as archive:
                names = set(archive.namelist())
            for required in ("LICENSE", "NOTICE", "SKILL.md", "scripts/setup_env.py", "scripts/aicard_lint.py"):
                self.assertIn(f"{package.NAME}/{required}", names)

    def test_archive_is_reproducible(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            first, _ = self.build(directory, "first.zip")
            second, _ = self.build(directory, "second.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_archive_excludes_untracked_files(self):
        stray = self.skill / "scripts" / "__pycache__" / "package-skill-probe.pyc"
        stray.parent.mkdir()
        stray.write_bytes(b"fixture cache")
        package.build(self.target)
        with zipfile.ZipFile(self.target) as archive:
            self.assertFalse(any("__pycache__" in name for name in archive.namelist()))
        self.assertEqual(stray.read_bytes(), b"fixture cache")

    def test_output_cannot_overwrite_source(self):
        source = self.skill / "SKILL.md"
        before = source.read_bytes()
        with self.assertRaises(SystemExit):
            package.build(source)
        self.assertEqual(source.read_bytes(), before)

    def test_output_cannot_create_archive_inside_skill(self):
        target = self.skill / "new-directory" / "skill.zip"
        with self.assertRaises(SystemExit):
            package.build(target)
        self.assertFalse(target.parent.exists())

    def test_output_cannot_follow_symlink_to_source(self):
        source = self.skill / "SKILL.md"
        before = source.read_bytes()
        target = self.root / "alias.zip"
        target.symlink_to(source)
        with self.assertRaises(SystemExit):
            package.build(target)
        self.assertTrue(target.is_symlink())
        self.assertEqual(source.read_bytes(), before)

    def test_output_cannot_follow_symlinked_parent_into_skill(self):
        alias = self.root / "alias"
        alias.symlink_to(self.skill, target_is_directory=True)
        with self.assertRaises(SystemExit):
            package.build(alias / "skill.zip")
        self.assertFalse((self.skill / "skill.zip").exists())

    def test_output_cannot_alias_source_via_hardlink(self):
        source = self.skill / "SKILL.md"
        before = source.read_bytes()
        target = self.root / "alias.zip"
        os.link(source, target)
        with self.assertRaises(SystemExit):
            package.build(target)
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(target.samefile(source))

    def assert_failed_build_preserves_archive(self, failure):
        package.build(self.target)
        before = self.target.read_bytes()
        with failure, self.assertRaises((OSError, SystemExit)):
            package.build(self.target)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_read_failure_preserves_existing_archive(self):
        original_read = Path.read_bytes
        second = self.skill / package.tracked_files()[1]

        def fail_second(path):
            if path == second:
                raise OSError("Simulated input read failure")
            return original_read(path)

        self.assert_failed_build_preserves_archive(patch.object(Path, "read_bytes", fail_second))

    def test_validation_failure_preserves_existing_archive(self):
        self.assert_failed_build_preserves_archive(
            patch.object(package, "check", side_effect=SystemExit("Simulated validation failure")))

    def test_replace_failure_preserves_existing_archive(self):
        self.assert_failed_build_preserves_archive(
            patch.object(package.os, "replace", side_effect=OSError("Simulated replacement failure")))

    def test_failed_first_build_leaves_no_archive(self):
        with patch.object(package, "check", side_effect=SystemExit("Simulated validation failure")):
            with self.assertRaises(SystemExit):
                package.build(self.target)
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_crc_failure_is_rejected(self):
        files = package.build(self.target)
        with patch.object(zipfile.ZipFile, "testzip", return_value="corrupt-entry"):
            with self.assertRaisesRegex(SystemExit, "CRC"):
                package.check(self.target, files)


if __name__ == "__main__":
    unittest.main()
