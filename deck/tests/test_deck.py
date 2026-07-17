import pytest
from pptx import Presentation
from pptx.util import Inches

from deck.content import SLIDES


@pytest.fixture(scope="session")
def prs():
    from deck.build import build_deck
    return Presentation(build_deck("deck/output/test_deck.pptx"))


def slide_text(slide):
    parts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            parts.append(shape.text_frame.text)
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    parts.append(cell.text)
    return "\n".join(parts)


def test_sixteen_by_nine(prs):
    assert prs.slide_width == Inches(13.333)
    assert prs.slide_height == Inches(7.5)


def test_eighteen_slides(prs):
    assert len(list(prs.slides)) == 18


def test_every_title_present(prs):
    for spec, slide in zip(SLIDES, prs.slides):
        assert spec["title"] in slide_text(slide), f"slide {spec['n']} title missing"


def test_every_footer_has_product_name(prs):
    from deck.theme import PRODUCT_NAME
    for spec, slide in zip(SLIDES, prs.slides):
        assert PRODUCT_NAME in slide_text(slide), f"slide {spec['n']} footer missing"


def test_speaker_notes_written(prs):
    slides = list(prs.slides)
    assert "chuẩn bị" in slides[0].notes_slide.notes_text_frame.text
