import json
import unittest
from pathlib import Path

from sloth import classify as C
from sloth.registry import Registry, Subject

REGISTRY = Registry(Path("/nonexistent"), [
    Subject("FLA", aliases=["Formal Languages and Automata"], keywords=["DFA", "PDA"]),
    Subject("Discrete Maths", aliases=["DM", "Maths"], keywords=["sets", "graphs"]),
])


def answer(**kw):
    raw = {"reason": "because", "is_college": True, "subject": "FLA", "new_subject": None,
           "unit_evidence": None, "unit": None, "doc_type": "notes", "confidence": 0.9}
    raw.update(kw)
    return C.validate(raw)


def decide(raw, filename="notes.pdf", text="", threshold=0.7):
    return C.decide(raw, filename, text, REGISTRY, threshold)


class Decide(unittest.TestCase):
    def test_known_subject_by_alias(self):
        out = decide(answer(subject="formal languages and automata"))
        self.assertEqual((out["status"], out["subject"]), (C.SORTED, "FLA"))

    def test_not_college(self):
        self.assertEqual(decide(answer(is_college=False, subject=None))["status"], C.NOT_COLLEGE)

    def test_low_confidence_goes_to_needs_a_look(self):
        out = decide(answer(confidence=0.5))
        self.assertEqual((out["status"], out["subject"]), (C.UNSURE, "FLA"))

    def test_new_subject(self):
        out = decide(answer(subject=None, new_subject={"name": "Operating Systems", "keywords": ["CPU"]}))
        self.assertEqual(out["status"], C.NEW)

    def test_near_duplicate_becomes_question(self):
        for name in ["Formal Language and Automata", "Discrete Mathematic", "fla"]:
            with self.subTest(name=name):
                out = decide(answer(subject=None, new_subject={"name": name, "keywords": []}))
                if name == "fla":  # exact alias/name match is simply that subject
                    self.assertEqual((out["status"], out["subject"]), (C.SORTED, "FLA"))
                else:
                    self.assertEqual(out["status"], C.ASK)

    def test_unknown_subject_name_is_treated_as_proposal(self):
        out = decide(answer(subject="Computer Networks"))
        self.assertEqual((out["status"], out["new_subject"]["name"]), (C.NEW, "Computer Networks"))

    def test_unit_from_filename(self):
        out = decide(answer(), filename="unit 3.pdf", text="Context-free grammars")
        self.assertEqual((out["unit"], out["unit_source"]), (3, "filename"))

    def test_content_unit_wins_and_is_explained(self):
        text = "FLA Unit 4 Tutorial Sheet\nPushdown automata"
        out = decide(answer(unit=4, unit_evidence="Unit 4 Tutorial Sheet"), filename="unit 2 (1).pdf", text=text)
        self.assertEqual((out["unit"], out["unit_source"]), (4, "content"))
        self.assertIn("Filename says unit 2", out["reason"])

    def test_evidence_must_be_in_text(self):
        out = decide(answer(unit=4, unit_evidence="unit IV"), filename="TOC.pdf", text="Pushdown automata")
        self.assertIsNone(out["unit"])

    def test_evidence_matching_ignores_punctuation(self):
        out = decide(answer(unit=1, unit_evidence="FLA • Unit 1"), text="Finite Automata\nFLA · Unit 1")
        self.assertEqual(out["unit"], 1)

    def test_evidence_matches_whole_numbers_only(self):
        out = decide(answer(unit=1, unit_evidence="Unit 1"), text="Unit 12: Something")
        self.assertIsNone(out["unit"])

    def test_evidence_must_name_a_unit(self):
        out = decide(answer(unit=5, unit_evidence="Theme 5"), text="Theme 5 Guide: real-time agents")
        self.assertIsNone(out["unit"])
        out = decide(answer(unit=3, unit_evidence="Module III"), text="Module III - Graphs")
        self.assertEqual(out["unit"], 3)

    def test_multi_unit_document_has_no_unit(self):
        text = "Mid-semester exam\nCourse: DM\nQ1 (Unit 1) ...\nQ3 (Unit 2) ..."
        out = decide(answer(subject="DM", unit=1, unit_evidence="Unit 1", doc_type="question_paper"), text=text)
        self.assertIsNone(out["unit"])


class Validate(unittest.TestCase):
    def test_rejects_bad_types(self):
        with self.assertRaises(ValueError):
            C.validate({**answer(), "is_college": "yes"})
        with self.assertRaises(ValueError):
            C.validate({k: v for k, v in answer().items() if k != "confidence"})

    def test_normalises(self):
        raw = C.validate({**answer(), "confidence": 7, "doc_type": "lecture", "unit": 3.0})
        self.assertEqual((raw["confidence"], raw["doc_type"], raw["unit"]), (1.0, "other", 3))


class FakeOllama:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, model, messages, schema=None, options=None):
        self.calls += 1
        return self.replies.pop(0), {}


class AskModel(unittest.TestCase):
    extraction = {"text": "Pushdown automata", "method": "pdf-text", "pages_read": 1}

    def test_retries_once_then_succeeds(self):
        good = json.dumps(answer())
        ol = FakeOllama(["{not json", good])
        out = C.ask_model("x.pdf", self.extraction, REGISTRY, ol, "m")
        self.assertEqual((ol.calls, out["attempts"], out["error"]), (2, 2, None))

    def test_gives_up_after_two_bad_replies(self):
        ol = FakeOllama(["{not json", '{"is_college": "maybe"}'])
        out = C.ask_model("x.pdf", self.extraction, REGISTRY, ol, "m")
        self.assertIsNone(out["raw"])
        self.assertEqual(ol.calls, 2)

    def test_no_text_caps_confidence(self):
        ol = FakeOllama([json.dumps(answer(confidence=0.99))])
        out = C.ask_model("unit 3.pdf", {**self.extraction, "text": ""}, REGISTRY, ol, "m")
        self.assertLessEqual(out["raw"]["confidence"], 0.5)


if __name__ == "__main__":
    unittest.main()
