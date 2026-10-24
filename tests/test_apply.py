"""plan -> apply -> undo on a temporary folder, with a fake model."""

import hashlib
import io
import json
import re
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from sloth import apply as A
from sloth import config
from sloth import plan as P
from sloth.registry import Registry, Subject

ANSWERS = {
    "fla unit 1.docx": {"subject": "FLA", "unit": 1, "unit_evidence": "Unit 1"},
    "unit 3.docx": {"subject": "FLA", "unit": None},
    "graphs.docx": {"subject": "Discrete Maths", "unit": 4, "unit_evidence": "Unit 4"},
    "os notes.docx": {"subject": None, "new_subject": {"name": "Operating Systems", "keywords": ["CPU"]}},
    "receipt.docx": {"is_college": False, "subject": None},
    "vague.docx": {"subject": "FLA", "confidence": 0.3},
}
TEXT = {
    "fla unit 1.docx": "FLA Unit 1 finite automata",
    "graphs.docx": "Discrete Maths Unit 4 graphs",
}


def docx(path: Path, text: str) -> None:
    body = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' \
           f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", body)


class FakeOllama:
    def chat(self, model, messages, schema=None, options=None):
        name = re.search(r"Filename: (.+)", messages[-1]["content"])[1].strip()
        raw = {"reason": f"fake answer for {name}", "is_college": True, "subject": None, "new_subject": None,
               "unit_evidence": None, "unit": None, "doc_type": "notes", "confidence": 0.95}
        raw.update(ANSWERS[name])
        return json.dumps(raw), {}


def manifest(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        out.append(f"D {rel}" if p.is_dir() else f"F {rel} {hashlib.sha256(p.read_bytes()).hexdigest()}")
    return out


class Loop(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.inbox, self.target, self.sandbox = tmp / "box/inbox", tmp / "box/College", tmp / "box"
        self.inbox.mkdir(parents=True)
        for name in ANSWERS:
            docx(self.inbox / name, TEXT.get(name, f"text of {name}"))
        (self.target / "FLA/Unit 1").mkdir(parents=True)
        (self.target / "FLA/Unit 1/fla unit 1.docx").write_text("an older file with the same name")
        self.cfg = config.Config(watched=[self.inbox], target_root=self.target, state=tmp / "state")
        self.cfg.state.mkdir()
        Registry(self.cfg.registry_path, [
            Subject("FLA", aliases=["Formal Languages and Automata"]),
            Subject("Discrete Maths", aliases=["DM"]),
        ]).save()

    def tearDown(self):
        self._tmp.cleanup()

    def make_plan(self):
        registry = Registry.load(self.cfg.registry_path)
        plan = P.make_plan([self.inbox], self.cfg, registry, ollama=FakeOllama())
        P.save_plan(plan, self.cfg)
        return plan

    def run_quiet(self, fn, *args, answers=(), **kw):
        it = iter(answers)
        with redirect_stdout(io.StringIO()):
            return fn(*args, ask=lambda prompt: next(it), **kw)

    def test_plan_moves_nothing(self):
        before = manifest(self.sandbox)
        plan = self.make_plan()
        self.assertEqual(manifest(self.sandbox), before)
        dests = {it["name"]: it["dest_dir"] for it in plan["items"]}
        self.assertEqual(dests["fla unit 1.docx"], "FLA/Unit 1")
        self.assertEqual(dests["unit 3.docx"], "FLA/Unit 3")
        self.assertEqual(dests["vague.docx"], P.NEEDS_LOOK)
        self.assertIsNone(dests["receipt.docx"])

    def test_cancel_moves_nothing(self):
        before = manifest(self.sandbox)
        self.make_plan()
        rc = self.run_quiet(A.apply, self.cfg, answers=["a", "n"])
        self.assertEqual(rc, 1)
        self.assertEqual(manifest(self.sandbox), before)
        self.assertIsNone(Registry.load(self.cfg.registry_path).get("Operating Systems"))

    def test_apply_then_undo_restores_everything(self):
        before = manifest(self.sandbox)
        self.make_plan()
        self.assertEqual(self.run_quiet(A.apply, self.cfg, answers=["a", "y"]), 0)

        t = self.target
        self.assertEqual((t / "FLA/Unit 1/fla unit 1.docx").read_text(), "an older file with the same name")
        self.assertTrue((t / "FLA/Unit 1/fla unit 1 (2).docx").exists())
        self.assertTrue((t / "FLA/Unit 3/unit 3.docx").exists())
        self.assertTrue((t / "Discrete Maths/Unit 4/graphs.docx").exists())
        self.assertTrue((t / "Operating Systems/os notes.docx").exists())
        self.assertTrue((t / "_Needs a look/vague.docx").exists())
        self.assertTrue((self.inbox / "receipt.docx").exists())
        self.assertIsNotNone(Registry.load(self.cfg.registry_path).get("Operating Systems"))

        # The same plan can't be applied twice.
        self.assertEqual(self.run_quiet(A.apply, self.cfg), 1)

        self.assertEqual(self.run_quiet(A.undo, self.cfg, answers=["y"]), 0)
        self.assertEqual(manifest(self.sandbox), before)
        self.assertEqual(self.run_quiet(A.undo, self.cfg), 1)  # nothing left

    def test_undo_refuses_if_a_moved_file_changed(self):
        self.make_plan()
        self.run_quiet(A.apply, self.cfg, answers=["s", "y"])
        moved = self.target / "FLA/Unit 3/unit 3.docx"
        original = moved.read_bytes()
        moved.write_bytes(original + b"edited")

        self.assertEqual(self.run_quiet(A.undo, self.cfg, answers=["y"]), 1)
        self.assertTrue(moved.exists())  # nothing moved back
        self.assertFalse((self.inbox / "fla unit 1.docx").exists())

        # --skip-changed undoes the rest and leaves the edited file alone.
        self.assertEqual(self.run_quiet(A.undo, self.cfg, skip_changed=True, answers=["y"]), 1)
        self.assertTrue(moved.exists())
        self.assertTrue((self.inbox / "fla unit 1.docx").exists())

    def test_undo_refuses_if_original_name_is_taken(self):
        self.make_plan()
        self.run_quiet(A.apply, self.cfg, answers=["s", "y"])
        (self.inbox / "graphs.docx").write_text("a new download with the same name")
        self.assertEqual(self.run_quiet(A.undo, self.cfg, answers=["y"]), 1)
        self.assertEqual((self.inbox / "graphs.docx").read_text(), "a new download with the same name")

    def test_source_changed_after_plan_is_skipped(self):
        self.make_plan()
        docx(self.inbox / "unit 3.docx", "rewritten after planning")
        self.run_quiet(A.apply, self.cfg, answers=["s", "y"])
        self.assertTrue((self.inbox / "unit 3.docx").exists())
        self.assertFalse((self.target / "FLA/Unit 3").exists())

    def test_merge_new_subject_into_existing(self):
        self.make_plan()
        # "Operating Systems" is merged into FLA (silly, but it tests the path); 1 = FLA.
        self.run_quiet(A.apply, self.cfg, answers=["m", "1", "y"])
        self.assertTrue((self.target / "FLA/os notes.docx").exists())
        self.assertIn("Operating Systems", Registry.load(self.cfg.registry_path).get("FLA").aliases)


class RealFolderLatch(unittest.TestCase):
    def test_refuses_real_folders(self):
        cfg = config.Config(watched=[], target_root=Path("/nonexistent"))
        for p in [Path.home() / "Downloads", Path.home() / "Documents/College/x.pdf"]:
            with self.subTest(p=p), self.assertRaises(PermissionError):
                config.check_allowed(cfg, p)
        config.check_allowed(cfg, Path.home() / "Documents2")  # a lookalike is fine


if __name__ == "__main__":
    unittest.main()
