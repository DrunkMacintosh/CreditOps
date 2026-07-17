"""Assemble the deck: python -m deck.build"""
import os

from pptx import Presentation

from deck import content, layouts, theme


def build_deck(out_path="deck/output/deck.pptx"):
    prs = Presentation()
    prs.slide_width = theme.SLIDE_W
    prs.slide_height = theme.SLIDE_H
    for spec in content.SLIDES:
        slide = layouts.RENDERERS[spec["layout"]](prs, spec)
        if spec.get("notes"):
            slide.notes_slide.notes_text_frame.text = spec["notes"]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    prs.save(out_path)
    return out_path


if __name__ == "__main__":
    print(build_deck())
