#!/usr/bin/env python3
"""Generate the synthetic test_docs/ corpus and its answer key (tests/expected.json).

Everything here is made up: no real notes, receipts or tickets. Documents are
written as flat ODF XML and converted with LibreOffice; the scanned handout,
phone photo and meme are rendered with ImageMagick.

    python3 scripts/make_test_docs.py            # writes ./test_docs
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "test_docs"
KEY = ROOT / "tests" / "expected.json"
FONT = "Adwaita-Sans"

FLA, DM, OS = "FLA", "Discrete Maths", "Operating Systems"

# Each document: target filename, format, content blocks, expected answer.
# Blocks: ("h", text) heading, ("p", text) paragraph, ("li", text) bullet,
#         ("pb", "") page break before the next block.
DOCS: list[dict] = []
SLIDES: list[dict] = []
IMAGES: list[dict] = []


def doc(name, blocks, expect, note=""):
    DOCS.append({"name": name, "blocks": blocks, "expect": expect, "note": note})


def slides(name, deck, expect, note=""):
    SLIDES.append({"name": name, "deck": deck, "expect": expect, "note": note})


def image(name, kind, text, expect, note=""):
    IMAGES.append({"name": name, "kind": kind, "text": text, "expect": expect, "note": note})


def ex(subject, unit, doc_type, is_college=True):
    return {"is_college": is_college, "subject": subject, "unit": unit, "doc_type": doc_type}


NOT_COLLEGE = {"is_college": False, "subject": None, "unit": None, "doc_type": None}

# --- FLA ---------------------------------------------------------------------

doc("fla unit 1.pdf", [
    ("h", "Formal Languages and Automata Theory"),
    ("h", "Unit 1: Finite Automata"),
    ("p", "An alphabet Σ is a finite, non-empty set of symbols. A string is a finite sequence of symbols "
          "from Σ, and a language is any set of strings over Σ. The empty string is written ε."),
    ("h", "1.1 Deterministic Finite Automata (DFA)"),
    ("p", "A DFA is a 5-tuple M = (Q, Σ, δ, q0, F) where Q is a finite set of states, Σ the input alphabet, "
          "δ: Q × Σ → Q the transition function, q0 ∈ Q the start state and F ⊆ Q the set of accepting states. "
          "M accepts w if the extended transition function δ*(q0, w) lands in F."),
    ("p", "Example: the DFA over {0,1} that accepts strings with an even number of 1s needs two states, "
          "q_even (start, accepting) and q_odd. Reading 1 swaps states; reading 0 stays put."),
    ("h", "1.2 Nondeterministic Finite Automata (NFA)"),
    ("p", "An NFA allows δ: Q × (Σ ∪ {ε}) → P(Q). A string is accepted if at least one computation path "
          "ends in an accepting state. Every NFA has an equivalent DFA, built by the subset construction; "
          "in the worst case the DFA has 2^n states."),
    ("h", "1.3 Minimisation"),
    ("p", "Two states are equivalent if no string distinguishes them. The table-filling (Myhill–Nerode) "
          "algorithm marks distinguishable pairs and merges the rest, giving the unique minimal DFA."),
], ex(FLA, 1, "notes"), "subject and unit in filename")

doc("FLA_Unit2_RegularExpressions.pdf", [
    ("h", "FLA — Unit 2: Regular Expressions and Regular Languages"),
    ("p", "Regular expressions over Σ are built from ∅, ε and single symbols using union (+), "
          "concatenation and Kleene star (*). For example (0+1)*01 describes binary strings ending in 01."),
    ("h", "Kleene's theorem"),
    ("p", "A language is regular iff some regular expression describes it iff some finite automaton accepts it. "
          "Thompson's construction turns a regex into an ε-NFA; state elimination or Arden's theorem "
          "(R = Q + RP has solution R = QP*) turns an automaton back into a regex."),
    ("h", "Closure properties"),
    ("li", "Regular languages are closed under union, intersection, complement, concatenation and star."),
    ("li", "They are also closed under reversal, homomorphism and inverse homomorphism."),
    ("p", "Closure properties are often combined with the pumping lemma to prove that a language is not regular."),
], ex(FLA, 2, "notes"), "unit in CamelCase filename")

doc("unit 3.pdf", [
    ("h", "Lecture notes: Context-Free Grammars"),
    ("p", "A context-free grammar is G = (V, T, P, S): variables V, terminals T, productions P of the form "
          "A → α with A ∈ V and α ∈ (V ∪ T)*, and a start symbol S."),
    ("p", "Example: S → aSb | ε generates { a^n b^n : n ≥ 0 }, a language that no finite automaton accepts."),
    ("h", "Derivations and parse trees"),
    ("p", "A leftmost derivation always rewrites the leftmost variable. Each derivation corresponds to a parse "
          "tree whose yield is the derived string. A grammar is ambiguous if some string has two different "
          "parse trees, e.g. E → E + E | E * E | id for the string id + id * id."),
    ("h", "Normal forms"),
    ("p", "Chomsky Normal Form allows only A → BC and A → a. Converting to CNF: remove ε-productions, "
          "unit productions and useless symbols, then break long right-hand sides. Greibach Normal Form "
          "requires every production to start with a terminal: A → aα."),
], ex(FLA, 3, "notes"), "no subject in filename; unit only from filename")

doc("TOC unit IV.pdf", [
    ("h", "Theory of Computation"),
    ("h", "Pushdown Automata"),
    ("p", "A PDA is a 7-tuple (Q, Σ, Γ, δ, q0, Z0, F). It is an ε-NFA with a stack: each move reads an input "
          "symbol or ε, pops the top stack symbol, and pushes a string of stack symbols."),
    ("p", "Acceptance can be by final state or by empty stack; the two are equivalent for nondeterministic PDAs."),
    ("h", "PDA for { w c w^R }"),
    ("p", "Push every symbol of w, switch state on c, then pop and match each symbol of w^R. Accept when the "
          "stack is back to Z0."),
    ("h", "Equivalence with CFGs"),
    ("p", "For every CFG there is a PDA accepting the same language (simulate leftmost derivations on the stack), "
          "and every PDA language is context-free. Deterministic PDAs accept a strict subset: the DCFLs."),
], ex(FLA, 4, "notes"), "alias 'TOC' + roman numeral unit")

doc("notes_final(1).pdf", [
    ("h", "Unit 5: Turing Machines and Decidability"),
    ("p", "A Turing machine is M = (Q, Σ, Γ, δ, q0, B, F) with an infinite tape, a read/write head and "
          "δ: Q × Γ → Q × Γ × {L, R}. It accepts if it enters an accepting state."),
    ("pb", ""),
    ("h", "5.2 Variants"),
    ("p", "Multi-tape, multi-track and nondeterministic Turing machines all recognise exactly the same "
          "languages as the basic single-tape machine; only running time changes."),
    ("pb", ""),
    ("h", "5.3 The Church–Turing thesis"),
    ("p", "Every effectively computable function is computable by a Turing machine."),
    ("pb", ""),
    ("h", "5.4 Undecidability"),
    ("p", "The halting problem HALT = { <M, w> : M halts on w } is undecidable, proved by diagonalisation. "
          "Rice's theorem: every non-trivial property of recursively enumerable languages is undecidable."),
    ("pb", ""),
    ("h", "5.5 Post Correspondence Problem"),
    ("p", "PCP is undecidable and is used to prove that ambiguity of CFGs is undecidable."),
], ex(FLA, 5, "notes"), "generic filename, 5 pages (only 3 should be read)")

doc("unit 2 (1).pdf", [
    ("h", "Formal Languages & Automata — Unit 4 Tutorial Sheet"),
    ("h", "Pushdown Automata"),
    ("li", "Q1. Design a PDA that accepts { a^n b^2n : n ≥ 1 } by empty stack."),
    ("li", "Q2. Convert the grammar S → aSa | bSb | c into an equivalent PDA."),
    ("li", "Q3. Show that { a^n b^n c^n : n ≥ 0 } is not context-free using the pumping lemma for CFLs."),
    ("li", "Q4. Is every language accepted by a DPDA by final state also accepted by empty stack? Justify."),
], ex(FLA, 4, "assignment"), "filename says unit 2, content says unit 4: content should win")

doc("fla assignment 3.docx", [
    ("h", "FLA Assignment 3"),
    ("p", "Topic: Context-free grammars (Unit 3). Submit by 30 October."),
    ("li", "1. Give a CFG for the language of balanced parentheses and draw the parse tree for (()())."),
    ("li", "2. Show that the grammar S → SS | (S) | ε is ambiguous."),
    ("li", "3. Convert S → ASB | ε, A → aAS | a, B → SbS | A | bb into Chomsky Normal Form."),
    ("li", "4. Write a grammar in Greibach Normal Form for { a^n b^n : n ≥ 1 }."),
], ex(FLA, 3, "assignment"), "'assignment 3' must not be read as unit 3; unit comes from content")

# --- Discrete Maths ------------------------------------------------------------

doc("maths unit 1.pdf", [
    ("h", "Discrete Mathematics"),
    ("h", "Unit 1: Sets, Relations and Functions"),
    ("p", "A set is an unordered collection of distinct objects. For sets A and B: A ∪ B, A ∩ B, A − B and the "
          "complement A'. De Morgan's laws: (A ∪ B)' = A' ∩ B' and (A ∩ B)' = A' ∪ B'. |P(A)| = 2^|A|."),
    ("h", "Relations"),
    ("p", "A relation R on A is a subset of A × A. R is reflexive, symmetric, antisymmetric or transitive. "
          "An equivalence relation (reflexive, symmetric, transitive) partitions A into equivalence classes. "
          "A partial order (reflexive, antisymmetric, transitive) can be drawn as a Hasse diagram."),
    ("h", "Functions"),
    ("p", "f: A → B is injective if f(a1) = f(a2) implies a1 = a2, surjective if every b has a preimage, and "
          "bijective if both. Composition: (g ∘ f)(x) = g(f(x))."),
], ex(DM, 1, "notes"), "'maths' as subject alias")

doc("DM Unit-2 Logic.pdf", [
    ("h", "Discrete Mathematics — Unit 2: Mathematical Logic"),
    ("p", "A proposition is a statement that is either true or false. Connectives: ¬, ∧, ∨, → and ↔. "
          "p → q is false only when p is true and q is false; it is equivalent to ¬p ∨ q."),
    ("h", "Tautologies and equivalences"),
    ("p", "A tautology is true under every assignment, e.g. p ∨ ¬p. Useful equivalences: contrapositive "
          "(p → q ≡ ¬q → ¬p), De Morgan, distribution, absorption."),
    ("h", "Predicates and quantifiers"),
    ("p", "∀x P(x) and ∃x P(x). Negation: ¬∀x P(x) ≡ ∃x ¬P(x). Rules of inference: modus ponens, "
          "modus tollens, hypothetical syllogism, universal instantiation."),
], ex(DM, 2, "notes"), "hyphenated 'Unit-2'")

doc("discrete mod 3 combinatorics.pdf", [
    ("h", "Module 3 — Combinatorics"),
    ("p", "Sum rule and product rule. Permutations P(n, r) = n!/(n−r)!, combinations C(n, r) = n!/(r!(n−r)!)."),
    ("h", "Pigeonhole principle"),
    ("p", "If n + 1 objects are placed in n boxes, some box contains at least two. Generalised: some box holds "
          "at least ⌈N/k⌉ objects."),
    ("h", "Inclusion–exclusion"),
    ("p", "|A ∪ B ∪ C| = |A| + |B| + |C| − |A∩B| − |A∩C| − |B∩C| + |A∩B∩C|. Used to count derangements: "
          "D_n = n! Σ (−1)^k / k!."),
    ("h", "Binomial theorem"),
    ("p", "(x + y)^n = Σ C(n, k) x^k y^(n−k). Pascal's identity: C(n, k) = C(n−1, k−1) + C(n−1, k)."),
], ex(DM, 3, "notes"), "'mod 3' as unit")

doc("unit 4.pdf", [
    ("h", "Graph Theory"),
    ("p", "A graph G = (V, E). The degree of a vertex is the number of incident edges; the handshaking lemma says "
          "Σ deg(v) = 2|E|, so every graph has an even number of odd-degree vertices."),
    ("h", "Paths and circuits"),
    ("p", "An Euler circuit uses every edge exactly once; a connected graph has one iff every vertex has even "
          "degree. A Hamiltonian cycle visits every vertex exactly once; no simple test is known."),
    ("h", "Trees"),
    ("p", "A tree is a connected acyclic graph; with n vertices it has n − 1 edges. Spanning trees: Kruskal and "
          "Prim find a minimum spanning tree."),
    ("h", "Colouring and planarity"),
    ("p", "The chromatic number χ(G) is the fewest colours for a proper vertex colouring. Euler's formula for "
          "connected planar graphs: v − e + f = 2."),
], ex(DM, 4, "notes"), "no subject in filename, no unit in content")

doc("ch1 relations practice.docx", [
    ("h", "Practice problems — Relations"),
    ("li", "1. Let A = {1, 2, 3, 4}. Is R = {(1,1), (2,2), (3,3), (4,4), (1,2), (2,1)} an equivalence relation? "
           "List its equivalence classes."),
    ("li", "2. Draw the Hasse diagram of divisibility on {1, 2, 3, 4, 6, 12}."),
    ("li", "3. Find the transitive closure of R = {(a,b), (b,c), (c,d)} using Warshall's algorithm."),
    ("li", "4. How many relations on a set of n elements are reflexive? Symmetric?"),
], ex(DM, 1, "assignment"), "'ch1' as unit, no subject in filename")

doc("document(3).pdf", [
    ("h", "Mid-Semester Examination, October 2026"),
    ("p", "Course: Discrete Mathematics. Time: 90 minutes. Maximum marks: 30. Answer all questions."),
    ("li", "Q1 (Unit 1). Prove that (A − B) ∪ (B − A) = (A ∪ B) − (A ∩ B). [5]"),
    ("li", "Q2 (Unit 1). Show that congruence modulo 5 is an equivalence relation on ℤ. [5]"),
    ("li", "Q3 (Unit 2). Using a truth table, check whether (p → q) ∧ (q → r) → (p → r) is a tautology. [5]"),
    ("li", "Q4 (Unit 2). Negate: 'Every student in the class has a laptop or a tablet.' [5]"),
    ("li", "Q5 (Unit 3). In how many ways can 8 people sit around a round table? [5]"),
    ("li", "Q6 (Unit 3). Prove that among any 13 people, two were born in the same month. [5]"),
], ex(DM, None, "question_paper"), "covers several units, so unit should be null")

doc("DM syllabus.pdf", [
    ("h", "Discrete Mathematics — Course Syllabus (Semester 3)"),
    ("p", "Credits: 4 (3 lecture + 1 tutorial). Prerequisite: none."),
    ("li", "Unit 1: Sets, relations and functions"),
    ("li", "Unit 2: Propositional and predicate logic, proof techniques"),
    ("li", "Unit 3: Combinatorics, recurrence relations"),
    ("li", "Unit 4: Graph theory and trees"),
    ("li", "Unit 5: Algebraic structures: groups, rings, lattices"),
    ("p", "Assessment: mid-semester 30%, assignments 20%, end-semester 50%."),
], ex(DM, None, "syllabus"), "syllabus lists every unit, so unit should be null")

# --- a subject that is not in the registry yet ---------------------------------

doc("os unit 1.pdf", [
    ("h", "Operating Systems — Unit 1: Processes and CPU Scheduling"),
    ("p", "A process is a program in execution, with its own address space, program counter and registers, "
          "tracked by the kernel in a process control block (PCB). States: new, ready, running, waiting, terminated."),
    ("h", "Scheduling algorithms"),
    ("li", "FCFS: simple, but short jobs wait behind long ones (convoy effect)."),
    ("li", "SJF / SRTF: optimal average waiting time, needs burst-time estimates."),
    ("li", "Round Robin: time quantum q; small q means more context switches."),
    ("li", "Priority scheduling: risk of starvation, fixed by ageing."),
], ex(OS, 1, "notes"), "subject not in registry: should be proposed as a new subject")

# --- not college -------------------------------------------------------------

doc("receipt_2026-10-03.pdf", [
    ("h", "FreshMart Supermarket"),
    ("p", "Store #214, MG Road. GSTIN 00AAAAA0000A1Z5 (sample). Date: 03/10/2026 18:42  Bill No: 88412"),
    ("li", "Milk 1L x2 .......... 132.00"),
    ("li", "Bread (brown) ....... 55.00"),
    ("li", "Eggs (12) ........... 96.00"),
    ("li", "Maggi 4-pack ........ 60.00"),
    ("li", "Detergent 1kg ....... 210.00"),
    ("p", "Subtotal 553.00  CGST 2.5% 13.83  SGST 2.5% 13.83  TOTAL ₹580.66. Paid by UPI. Thank you, visit again!"),
], NOT_COLLEGE, "grocery receipt")

doc("e-ticket_PNR4512783901.pdf", [
    ("h", "Electronic Reservation Slip (sample)"),
    ("p", "PNR: 4512783901   Train: 12345 / Example Express   Class: 3A   Quota: General"),
    ("p", "From: New Delhi (NDLS)  To: Chandigarh (CDG)  Date of journey: 31-Oct-2026  Departure 07:40  Arrival 11:05"),
    ("li", "Passenger 1: Adult, 20, M — CNF / B2 / 34 / Lower"),
    ("p", "Fare: ₹745.00  Convenience fee: ₹35.40. Carry a valid photo ID during the journey."),
], NOT_COLLEGE, "train ticket")

doc("flat_unit_3_rent_receipt.pdf", [
    ("h", "Rent Receipt"),
    ("p", "Received with thanks from Mr. A. Kumar the sum of ₹12,000 (Rupees twelve thousand only) towards rent "
          "for Flat / Unit 3, Green Park Apartments, for the month of October 2026."),
    ("p", "Payment mode: bank transfer. Landlord signature: ______________"),
], NOT_COLLEGE, "decoy: 'unit 3' and 'fla(t)' in the name, but it's a rent receipt")

# --- slides ------------------------------------------------------------------

slides("fla unit 1 slides.pptx", [
    ("Finite Automata", ["FLA · Unit 1", "Lecture 2"], "Recap of alphabets and languages before starting."),
    ("DFA: formal definition", ["M = (Q, Σ, δ, q0, F)", "δ: Q × Σ → Q is total", "Accept if δ*(q0, w) ∈ F"],
     "Stress that every state has exactly one transition per symbol."),
    ("Example: even number of 1s", ["States q_even, q_odd", "1 swaps state, 0 keeps it", "q_even is accepting"],
     "Draw the two-state diagram on the board."),
    ("NFA and ε-moves", ["δ: Q × (Σ ∪ {ε}) → P(Q)", "Accept if any path accepts"], ""),
    ("Subset construction", ["DFA states are sets of NFA states", "Worst case 2^n states"],
     "Work through the NFA for strings ending in 01."),
], ex(FLA, 1, "slides"), "pptx with speaker notes")

slides("maths unit 2.pptx", [
    ("Predicate Logic", ["Discrete Mathematics", "Unit 2, Part B"], ""),
    ("Quantifiers", ["∀x P(x): for all x", "∃x P(x): there exists x", "Domain must be stated"],
     "Ask students for everyday examples of 'for all'."),
    ("Negating quantifiers", ["¬∀x P(x) ≡ ∃x ¬P(x)", "¬∃x P(x) ≡ ∀x ¬P(x)"], ""),
    ("Rules of inference", ["Modus ponens", "Modus tollens", "Universal instantiation"],
     "Next lecture: proof by contradiction and induction."),
], ex(DM, 2, "slides"), "spec example name")

slides("fla unit 5.ppt", [
    ("Turing Machines", ["Formal Languages and Automata", "Unit 5"], ""),
    ("Definition", ["Infinite tape, read/write head", "δ: Q × Γ → Q × Γ × {L, R}"], "Compare with a PDA's stack."),
    ("The halting problem", ["HALT is undecidable", "Proof by diagonalisation"], ""),
], ex(FLA, 5, "slides"), "legacy .ppt (needs LibreOffice)")

DOCS.append({
    "name": "dm unit 3 notes.doc",
    "blocks": [
        ("h", "Discrete Mathematics, Unit 3: Recurrence Relations"),
        ("p", "A recurrence defines a_n in terms of earlier terms, e.g. Fibonacci a_n = a_(n−1) + a_(n−2)."),
        ("p", "Linear homogeneous recurrences with constant coefficients are solved with the characteristic "
              "equation. For a_n = 5a_(n−1) − 6a_(n−2): r² − 5r + 6 = 0, so a_n = A·2^n + B·3^n."),
        ("p", "Generating functions turn a recurrence into an algebraic equation in G(x) = Σ a_n x^n."),
    ],
    "expect": ex(DM, 3, "notes"),
    "note": "legacy .doc (needs LibreOffice)",
})

# --- images (OCR) ------------------------------------------------------------

image("scan_0042.pdf", "scan", """Unit 2 - Handout 3
The Pumping Lemma for Regular Languages

If L is regular, there is a constant p (the pumping length)
such that every string w in L with |w| >= p can be split
as w = xyz where:
  1. |xy| <= p
  2. |y| >= 1
  3. x y^i z is in L for every i >= 0

Using it to prove L = { a^n b^n : n >= 0 } is not regular:
Assume L is regular with pumping length p. Take w = a^p b^p.
Since |xy| <= p, y consists only of a's. Pumping with i = 2
gives more a's than b's, which is not in L. Contradiction.

Note: the lemma is a necessary condition only. It can show
that a language is NOT regular, never that it IS regular.

Formal Languages and Automata""", ex(FLA, 2, "notes"), "image-only scanned PDF: needs OCR")

image("IMG_20261012_101533.jpg", "photo", """DISCRETE MATHEMATICS
Unit 4 Handout: Euler and Hamilton

Euler path: uses every EDGE exactly once.
 - Connected graph has an Euler circuit
   iff every vertex has even degree.
 - Euler path (not circuit) iff exactly
   two vertices have odd degree.

Hamiltonian cycle: visits every VERTEX once.
 - Dirac: if n >= 3 and deg(v) >= n/2 for all v,
   G is Hamiltonian.
 - Ore: deg(u) + deg(v) >= n for all
   non-adjacent u, v  =>  Hamiltonian.

Practice: Does K4 have an Euler circuit?""", ex(DM, 4, "notes"), "phone photo of a handout")

image("meme.jpg", "meme", "ME: I'LL START STUDYING AT 9\n\nALSO ME AT 9:01:\nONE MORE EPISODE",
      NOT_COLLEGE, "meme with text")

APPIMAGE = "editor-1.4.2-x86_64.AppImage"


# --- builders ----------------------------------------------------------------

def fodt(blocks) -> str:
    body, page_break = [], False
    for kind, text in blocks:
        if kind == "pb":
            page_break = True
            continue
        style = ' text:style-name="PB"' if page_break else ""
        page_break = False
        t = escape(text)
        if kind == "h":
            body.append(f'<text:h text:outline-level="1"{style}>{t}</text:h>')
        else:
            body.append(f"<text:p{style}>{'• ' if kind == 'li' else ''}{t}</text:p>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
 office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.text">
 <office:automatic-styles>
  <style:style style:name="PB" style:family="paragraph"><style:paragraph-properties fo:break-before="page"/></style:style>
 </office:automatic-styles>
 <office:body><office:text>
  {"".join(body)}
 </office:text></office:body>
</office:document>
"""


def fodp(deck) -> str:
    pages = []
    for i, (title, bullets, notes) in enumerate(deck, start=1):
        items = "".join(f"<text:p>{escape(b)}</text:p>" for b in bullets)
        notes_xml = ""
        if notes:
            notes_xml = (
                '<presentation:notes><draw:frame presentation:class="notes" svg:x="2cm" svg:y="14cm" '
                'svg:width="17cm" svg:height="10cm"><draw:text-box>'
                f"<text:p>{escape(notes)}</text:p></draw:text-box></draw:frame></presentation:notes>"
            )
        pages.append(
            f'<draw:page draw:name="Slide{i}" draw:master-page-name="Default">'
            '<draw:frame presentation:class="title" svg:x="1cm" svg:y="1cm" svg:width="26cm" svg:height="3cm">'
            f"<draw:text-box><text:p>{escape(title)}</text:p></draw:text-box></draw:frame>"
            '<draw:frame presentation:class="outline" svg:x="1cm" svg:y="5cm" svg:width="26cm" svg:height="12cm">'
            f"<draw:text-box>{items}</draw:text-box></draw:frame>{notes_xml}</draw:page>"
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.presentation">
 <office:master-styles><style:master-page style:name="Default"/></office:master-styles>
 <office:body><office:presentation>
  {"".join(pages)}
 </office:presentation></office:body>
</office:document>
"""


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def soffice_convert(sources: list[Path], fmt: str, outdir: Path, profile: Path) -> None:
    run(["soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless",
         "--convert-to", fmt, "--outdir", str(outdir), *map(str, sources)])


def caption(text: str, out: Path, *, width: int, pointsize: int, font: str = FONT,
            fg: str = "black", bg: str = "white", extra: list[str] = ()) -> None:
    run(["magick", "-background", bg, "-fill", fg, "-font", font, "-pointsize", str(pointsize),
         "-size", f"{width}x", f"caption:{text}", "-bordercolor", bg, "-border", "90x110",
         *extra, str(out)])


def main() -> int:
    for tool in ("soffice", "magick"):
        if not shutil.which(tool):
            print(f"{tool} is required to generate test docs", file=sys.stderr)
            return 1
    OUT.mkdir(exist_ok=True)
    expected = {}

    with tempfile.TemporaryDirectory(prefix="sloth-gen-") as tmp:
        tmp = Path(tmp)
        src, conv, profile = tmp / "src", tmp / "conv", tmp / "profile"
        src.mkdir(), conv.mkdir()

        # Write every source as stem i.ext so names with spaces can't confuse soffice.
        by_format: dict[str, list[tuple[Path, str]]] = {}
        for i, d in enumerate(DOCS):
            s = src / f"d{i}.fodt"
            s.write_text(fodt(d["blocks"]), encoding="utf-8")
            fmt = Path(d["name"]).suffix[1:].lower()
            by_format.setdefault({"doc": "doc:MS Word 97"}.get(fmt, fmt), []).append((s, d["name"]))
        for i, d in enumerate(SLIDES):
            s = src / f"s{i}.fodp"
            s.write_text(fodp(d["deck"]), encoding="utf-8")
            fmt = Path(d["name"]).suffix[1:].lower()
            by_format.setdefault({"ppt": "ppt:MS PowerPoint 97"}.get(fmt, fmt), []).append((s, d["name"]))

        for fmt, items in by_format.items():
            soffice_convert([s for s, _ in items], fmt, conv, profile)
            ext = fmt.split(":")[0]
            for s, name in items:
                shutil.copyfile(conv / f"{s.stem}.{ext}", OUT / name)

        for img in IMAGES:
            png = tmp / (Path(img["name"]).stem + ".png")
            if img["kind"] == "scan":
                # A slightly crooked, noisy, grey page, wrapped in a PDF with no text layer.
                caption(img["text"], png, width=1450, pointsize=34, font="Adwaita-Mono",
                        extra=["-rotate", "0.8", "-background", "white", "-flatten",
                               "-gravity", "north", "-extent", "1654x2339",  # A4 at 200 dpi
                               "-attenuate", "0.4", "+noise", "Gaussian", "-colorspace", "Gray"])
                run(["magick", str(png), "-alpha", "off", "-units", "PixelsPerInch", "-density", "200",
                     "-compress", "jpeg", "-quality", "70", str(OUT / img["name"])])
            elif img["kind"] == "photo":
                # Off-white paper, tilted, slightly blurred: like a phone photo of a handout.
                caption(img["text"], png, width=1600, pointsize=42, bg="#f1ecdf",
                        extra=["-rotate", "-2.5", "-background", "#6b6257", "-flatten",
                               "-blur", "0x0.8", "-attenuate", "0.3", "+noise", "Gaussian"])
                run(["magick", str(png), "-quality", "82", str(OUT / img["name"])])
            else:  # meme
                run(["magick", "-size", "900x900", "gradient:#3a7bd5-#00d2ff", "-font", "Adwaita-Sans-Black",
                     "-pointsize", "52", "-fill", "white", "-stroke", "black", "-strokewidth", "3",
                     "-gravity", "center", "-annotate", "+0+0", img["text"], "-quality", "85",
                     str(OUT / img["name"])])
            expected[img["name"]] = {**img["expect"], "note": img["note"]}

    for d in DOCS + SLIDES:
        expected[d["name"]] = {**d["expect"], "note": d["note"]}

    # A fake installer: ELF magic followed by deterministic filler bytes.
    filler = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(2048))
    (OUT / APPIMAGE).write_bytes(b"\x7fELF\x02\x01\x01\x00" + filler)
    expected[APPIMAGE] = {**NOT_COLLEGE, "note": "binary installer: can't be read, must be left alone"}

    KEY.write_text(json.dumps(dict(sorted(expected.items())), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(expected)} files to {OUT}, answer key in {KEY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
