import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sloth import moves
from sloth.moves import clash_name, free_name, make_dirs, rename_noreplace, safe_move


class TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def file(self, rel: str, content: str = "x") -> Path:
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p


class ClashNames(TmpDir):
    def test_clash_name(self):
        self.assertEqual(clash_name("notes.pdf", 1), "notes.pdf")
        self.assertEqual(clash_name("notes.pdf", 2), "notes (2).pdf")
        self.assertEqual(clash_name("unit 2 (1).pdf", 3), "unit 2 (1) (3).pdf")
        self.assertEqual(clash_name("README", 2), "README (2)")

    def test_free_name_counts_up(self):
        self.file("d/a.pdf")
        self.file("d/a (2).pdf")
        self.assertEqual(free_name(self.tmp / "d", "a.pdf").name, "a (3).pdf")


class NoOverwrite(TmpDir):
    def test_rename_noreplace_refuses_existing(self):
        src, dst = self.file("src.txt", "new"), self.file("dst.txt", "old")
        with self.assertRaises(FileExistsError):
            rename_noreplace(src, dst)
        self.assertEqual(dst.read_text(), "old")
        self.assertEqual(src.read_text(), "new")

    def test_fallback_without_renameat2_also_refuses(self):
        src, dst = self.file("src.txt", "new"), self.file("dst.txt", "old")
        with mock.patch.object(moves, "_renameat2", None):
            with self.assertRaises(FileExistsError):
                rename_noreplace(src, dst)
            self.assertEqual(dst.read_text(), "old")
            rename_noreplace(src, self.tmp / "free.txt")
        self.assertFalse(src.exists())
        self.assertEqual((self.tmp / "free.txt").read_text(), "new")

    def test_safe_move_never_overwrites(self):
        self.file("target/notes.pdf", "already here")
        src = self.file("inbox/notes.pdf", "incoming")
        final = safe_move(src, self.tmp / "target")
        self.assertEqual(final.name, "notes (2).pdf")
        self.assertEqual(final.read_text(), "incoming")
        self.assertEqual((self.tmp / "target/notes.pdf").read_text(), "already here")
        self.assertFalse(src.exists())

    def test_safe_move_across_filesystems_copies_verifies_then_removes(self):
        self.file("target/notes.pdf", "already here")
        src = self.file("inbox/notes.pdf", "incoming")
        real = moves.rename_noreplace
        calls = {"n": 0}

        def exdev_once(a, b):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(errno.EXDEV, "cross-device")
            return real(a, b)

        with mock.patch.object(moves, "rename_noreplace", exdev_once):
            final = safe_move(src, self.tmp / "target")
        self.assertEqual(final.read_text(), "incoming")
        self.assertEqual((self.tmp / "target/notes.pdf").read_text(), "already here")
        self.assertFalse(src.exists())
        self.assertEqual([p.name for p in (self.tmp / "target").iterdir() if p.name.startswith(".sloth-")], [])

    def test_make_dirs_reports_only_new_dirs(self):
        (self.tmp / "a").mkdir()
        created = make_dirs(self.tmp / "a/b/c")
        self.assertEqual(created, [str(self.tmp / "a/b"), str(self.tmp / "a/b/c")])
        self.assertEqual(make_dirs(self.tmp / "a/b/c"), [])


if __name__ == "__main__":
    unittest.main()
