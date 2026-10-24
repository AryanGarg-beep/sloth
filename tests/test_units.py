import unittest

from sloth.units import unit_from_filename, units_in_text


class UnitFromFilename(unittest.TestCase):
    def test_finds_units(self):
        cases = {
            "fla unit 1.pdf": 1,
            "FLA_Unit2_RegularExpressions.pdf": 2,
            "unit 3.pdf": 3,
            "TOC unit IV.pdf": 4,
            "DM Unit-2 Logic.pdf": 2,
            "discrete mod 3 combinatorics.pdf": 3,
            "ch1 relations practice.docx": 1,
            "U-3 notes.pdf": 3,
            "u5.pdf": 5,
            "chapter ii.pdf": 2,
            "Module-V.pdf": 5,
            "Chemistry Unit X.pdf": 10,
            "unit 2 (1).pdf": 2,
        }
        for name, unit in cases.items():
            with self.subTest(name=name):
                self.assertEqual(unit_from_filename(name), unit)

    def test_ignores_lookalikes(self):
        for name in ["notes_final(1).pdf", "fla assignment 3.docx", "document(3).pdf",
                     "IMG_20261012_101533.jpg", "e-ticket_PNR4512783901.pdf", "menu 2.pdf",
                     "Modi speech.pdf", "ch 2026 report.pdf", "unit 123.pdf", "lecture5.pdf"]:
            with self.subTest(name=name):
                self.assertIsNone(unit_from_filename(name))


class UnitsInText(unittest.TestCase):
    def test_distinct_units(self):
        self.assertEqual(units_in_text("Q1 (Unit 1) ... Q3 (Unit 2) ... Q4 (Unit 2)"), {1, 2})
        self.assertEqual(units_in_text("Module III - Graphs"), {3})

    def test_number_glued_to_next_word(self):
        self.assertEqual(units_in_text("Module 2Health of the Body - I"), {2})
        self.assertEqual(units_in_text("unit ivory tower"), set())

    def test_abbreviations_dont_count_in_prose(self):
        self.assertEqual(units_in_text("see u 2 and ch 3 and mod 4"), set())


if __name__ == "__main__":
    unittest.main()
