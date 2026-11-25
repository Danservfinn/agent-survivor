"""Each Methodology section is pinned to the engine functions it describes."""

from __future__ import annotations

import unittest

from backend import methodology_sections as ms


class MethodologySectionsTest(unittest.TestCase):
    def test_every_described_function_exists(self) -> None:
        for sid, (_, names) in ms.SECTIONS.items():
            for name in names:
                self.assertTrue(callable(ms._resolve(name)), f"{sid}: {name}")

    def test_no_section_describes_code_that_changed(self) -> None:
        stale = ms.stale()
        self.assertEqual(
            stale,
            [],
            "The engine code these Methodology sections describe has changed: "
            + "; ".join(f"{sid} ({', '.join(ms.SECTIONS[sid][1])})" for sid in stale)
            + ". Re-read each section in frontend/src/app/methodology against the code, fix the text if a rule "
            "changed, then run: python3 -m backend.methodology_sections --update " + " ".join(stale),
        )

    def test_a_comment_change_does_not_make_a_section_stale(self) -> None:
        import importlib.util
        import sys
        import tempfile
        from pathlib import Path

        from backend.rules_changelog import code_hash

        def load(source: str):
            path = Path(tempfile.mkdtemp()) / "pinned_example.py"
            path.write_text(source)
            spec = importlib.util.spec_from_file_location(f"pinned_example_{abs(hash(source))}", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module.rule

        plain = load("def rule(x):\n    return x + 1\n")
        commented = load("def rule(x):\n    # why it adds one\n\n    return x + 1  # one\n")
        changed = load("def rule(x):\n    return x + 2\n")
        self.assertEqual(code_hash(plain), code_hash(commented))
        self.assertNotEqual(code_hash(plain), code_hash(changed))


if __name__ == "__main__":
    unittest.main()
