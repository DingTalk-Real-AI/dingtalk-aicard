#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Check execution-block adaptation, shared text, and generation failure protection."""
import contextlib
import io
import os
from pathlib import Path
import re
import shlex
import tempfile
import unittest
from unittest.mock import patch

import build_dws_skill as build


class DwsSkillBuildTests(unittest.TestCase):
    # Localized fixture text tests that only marked execution blocks are replaced.
    @classmethod
    def setUpClass(cls):
        cls.source = Path(build.SRC, "SKILL.md").read_text()

    def replace_block(self, name, content):
        start = f"<!-- aicard:{name}:start -->"
        end = f"<!-- aicard:{name}:end -->"
        prefix, rest = self.source.split(start, 1)
        _, suffix = rest.split(end, 1)
        return prefix + start + "\n" + content + "\n" + end + suffix

    def test_python_execution_content_can_change_without_leaking_into_dws(self):
        expected = build.build_skill_md(self.source)
        for name in build.DWS_EXECUTION_BLOCKS:
            with self.subTest(block=name):
                changed = self.replace_block(
                    name, '任意环境说明\n```bash\n"/新的 Python 路径" '
                    '"/新的 Skill 路径/scripts/aicard_lint.py" --explain Action --format json\n```')
                self.assertEqual(expected, build.build_skill_md(changed))

    def test_common_body_survives_even_when_it_contains_old_replacement_phrases(self):
        common = "\n使用 Python 3.10+。这里只是公共说明，`--explain <名字>` 不应被全局替换。\n"
        expected = build.build_skill_md(self.source).rstrip("\n") + common
        self.assertEqual(expected, build.build_skill_md(self.source.rstrip("\n") + common))

    def test_generated_execution_commands_are_native_and_have_no_markers(self):
        result = build.build_skill_md(self.source)
        commands = [
            shlex.split(line)
            for block in re.findall(r"```bash\n(.*?)\n```", result, re.S)
            for line in block.splitlines() if line.strip()
        ]
        self.assertEqual({"explain", "lint"}, {cmd[2] for cmd in commands})
        self.assertTrue(all(cmd[:2] == ["dws", "aicard"] for cmd in commands))
        self.assertNotIn("scripts/aicard_lint.py", result)
        self.assertNotIn("setup_env.py", result)
        self.assertNotIn("<!-- aicard:", result)

    def test_malformed_execution_blocks_are_rejected(self):
        start = "<!-- aicard:query:start -->"
        end = "<!-- aicard:query:end -->"
        reversed_pair = self.source.replace(start, "QUERY_START").replace(end, start).replace("QUERY_START", end)
        nested = self.source.replace("aicard:runtime:end", "RUNTIME_END")
        nested = nested.replace("aicard:query:start", "aicard:runtime:end")
        nested = nested.replace("RUNTIME_END", "aicard:query:start")
        cases = {
            "缺失": self.source.replace(end, "", 1),
            "重复": self.source + "\n" + start + end,
            "未知": self.source.replace("aicard:query:", "aicard:unknown:"),
            "重复名称": self.source.replace("aicard:query:", "aicard:runtime:"),
            "反向": reversed_pair,
            "嵌套": nested,
            "未识别标记": self.source + "\n<!-- aicard:query:finish -->",
        }
        for name, source in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                build.build_skill_md(source)

    def test_failed_generation_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR") or build.ROOT) as directory:
            src, dst = Path(directory, "source"), Path(directory, "destination")
            src.mkdir()
            dst.mkdir()
            (src / "SKILL.md").write_text(self.source.replace("<!-- aicard:query:end -->", "", 1))
            sentinel = dst / "SKILL.md"
            sentinel.write_text("已有交付物")
            with patch.object(build, "SRC", str(src)), patch.object(build, "DST", str(dst)):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(1, build.main([]))
            self.assertEqual("已有交付物", sentinel.read_text())


if __name__ == "__main__":
    unittest.main()
