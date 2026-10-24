import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from sloth import coldstart
from sloth.registry import Registry, Subject

SNIPPETS = [{"name": f"file{i}.pdf", "text": "..."} for i in range(1, 6)]


def proposal(name, files, aliases=(), keywords=("k",)):
    return {"name": name, "aliases": list(aliases), "description": "", "keywords": list(keywords), "files": list(files)}


def run_review(proposals, registry, answers):
    it = iter(answers)
    with redirect_stdout(io.StringIO()):
        return coldstart.review(proposals, SNIPPETS, registry, ask=lambda prompt: next(it))


class CleanProposals(unittest.TestCase):
    def test_drops_bad_file_numbers_and_empty_subjects(self):
        out = coldstart.clean_proposals([
            proposal("FLA", [1, 2, 99, 0]),
            proposal("Guesswork", []),
            proposal("", [3]),
        ], n_files=5)
        self.assertEqual([(p["name"], p["files"]) for p in out], [("FLA", [1, 2])])

    def test_merges_duplicates_and_sorts_by_evidence(self):
        out = coldstart.clean_proposals([
            proposal("OS", [5]),
            proposal("Discrete Maths", [1], aliases=["DM"]),
            proposal("discrete maths", [2, 3], aliases=["DM", "Maths"]),
        ], n_files=5)
        self.assertEqual(out[0]["name"], "Discrete Maths")
        self.assertEqual(out[0]["files"], [1, 2, 3])
        self.assertEqual(out[0]["aliases"], ["DM", "Maths"])


class Review(unittest.TestCase):
    def test_keep_rename_drop(self):
        reg = Registry(Path("/x"))
        staged = run_review([
            proposal("Formal Languages and Automata", [1, 2], aliases=["FLA"]),
            proposal("Operating Systems", [3]),
            proposal("Cooking", [4]),
        ], reg, ["r", "FLA", "k", "d"])
        self.assertEqual([s.name for s in staged.subjects], ["FLA", "Operating Systems"])
        self.assertIn("Formal Languages and Automata", staged.get("FLA").aliases)
        self.assertEqual(reg.subjects, [])  # the original is untouched until saved

    def test_existing_subject_defaults_to_merge(self):
        reg = Registry(Path("/x"), [Subject("FLA", aliases=["Formal Languages and Automata"])])
        staged = run_review([proposal("Formal Languages and Automata", [1], aliases=["TOC"], keywords=["PDA"])],
                            reg, ["", ""])  # Enter, Enter: merge into the suggested subject
        self.assertEqual(len(staged.subjects), 1)
        self.assertIn("TOC", staged.get("FLA").aliases)
        self.assertIn("PDA", staged.get("FLA").keywords)

    def test_merge_two_proposals(self):
        staged = run_review([
            proposal("Discrete Maths", [1, 2]),
            proposal("Graph Theory", [3]),
        ], Registry(Path("/x")), ["k", "m", "1"])
        self.assertEqual([s.name for s in staged.subjects], ["Discrete Maths"])
        self.assertIn("Graph Theory", staged.get("Discrete Maths").aliases)


if __name__ == "__main__":
    unittest.main()
