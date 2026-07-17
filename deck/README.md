# Pitch Deck Generator — SHB CreditOps EvidenceGraph

Generates the 18-slide Vietnamese hackathon deck from
`docs/superpowers/specs/2026-07-17-shb-pitch-deck-design.md`.

## Regenerate

    python -m pip install -r deck/requirements.txt
    python -m deck.build          # writes deck/output/deck.pptx
    python -m pytest deck/tests   # structure + compliance checks

All copy lives in `deck/content.py`; colors/fonts in `deck/theme.py`.
Edit those, rebuild, and visual polish in PowerPoint is never lost copy.

## Before submitting (spec §5/§6)

1. Fill inputs in `deck/content.py`: team members (slide 17), measured
   validation numbers (13, 15), QR link (18). Numbers must come from real
   runs — never invented.
2. Confirm official SHB brand colors in `deck/theme.py`.
3. Rebuild, test, and refresh the release copy:

       python -m deck.build
       python -m pytest deck/tests
       python deck/qa_visual.py deck/output/deck.pptx
       Copy-Item deck/output/deck.pptx deck/SHB-CreditOps-EvidenceGraph-pitch.pptx

   `qa_visual.py` flags text that overflows its box (longer real names or
   metric values can overflow where placeholders fit) and overlapping shapes.

4. In PowerPoint, replace the screenshot placeholders (slides 4, 6, 8) and
   the QR placeholder (slide 18) with real captures — edit ONLY the release
   file `deck/SHB-CreditOps-EvidenceGraph-pitch.pptx`. Rebuilding overwrites
   `deck/output/` but never touches the release file.
5. Run the gate on the release file — it must print OK before submission:

       python deck/check_final.py deck/SHB-CreditOps-EvidenceGraph-pitch.pptx

6. If the demo slips, reframe slides 6 and 13 per spec §2 before presenting.
