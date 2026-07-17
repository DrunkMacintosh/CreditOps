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
2. Replace screenshot placeholders (slides 4, 6, 8) with real dashboard
   captures in PowerPoint.
3. Confirm official SHB brand colors in `deck/theme.py`.
4. Rebuild, then run the gate — it must print OK:

       python deck/check_final.py deck/output/deck.pptx

5. If the demo slips, reframe slides 6 and 13 per spec §2 before presenting.
