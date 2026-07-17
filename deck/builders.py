"""Generic shape helpers. Every helper sets shape.name for testability."""
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

from deck import theme


def add_blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def tb(slide, x, y, w, h, text, size=None, color=None, bold=False,
       align=PP_ALIGN.LEFT, name=None):
    shape = slide.shapes.add_textbox(x, y, w, h)
    if name:
        shape.name = name
    tf = shape.text_frame
    tf.word_wrap = True
    lines = text.split("\n")
    tf.text = lines[0]
    for line in lines[1:]:
        tf.add_paragraph().text = line
    for para in tf.paragraphs:
        para.alignment = align
        for run in para.runs or [para.add_run()]:
            run.font.name = theme.FONT
            run.font.size = size or theme.BODY_SIZE
            run.font.bold = bold
            run.font.color.rgb = color or theme.DARK_TEXT
    return shape


def box(slide, x, y, w, h, text, fill, text_color=None, size=None, bold=False,
        name="box", shape_type=MSO_SHAPE.ROUNDED_RECTANGLE):
    shape = slide.shapes.add_shape(shape_type, x, y, w, h)
    shape.name = name
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    tf = shape.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    lines = text.split("\n")
    tf.text = lines[0]
    for line in lines[1:]:
        tf.add_paragraph().text = line
    for para in tf.paragraphs:
        para.alignment = PP_ALIGN.CENTER
        for run in para.runs or [para.add_run()]:
            run.font.name = theme.FONT
            run.font.size = size or theme.SMALL_SIZE
            run.font.bold = bold
            run.font.color.rgb = text_color or theme.WHITE
    return shape


def add_title(slide, spec):
    tb(slide, Inches(0.5), Inches(0.25), Inches(12.3), Inches(1.0),
       spec["title"], size=theme.TITLE_SIZE, color=theme.DEEP_BLUE, bold=True,
       name="title")


def add_killer(slide, text):
    shape = box(slide, Inches(0.5), Inches(6.15), Inches(12.3), Inches(0.75),
                text, theme.ORANGE, size=theme.KILLER_SIZE, bold=True,
                name="killer")
    return shape


def add_bullets(slide, items, x, y, w, h, size=None):
    return tb(slide, x, y, w, h, "\n".join("•  " + i for i in items),
              size=size or theme.BODY_SIZE, name="bullets")


def add_footer(slide, spec):
    text = f"{theme.PRODUCT_NAME}  ·  {spec['n']}/18"
    if spec["disclaimer"]:
        text += f"\n{theme.DISCLAIMER_VN}  ({theme.DISCLAIMER_EN})"
    tb(slide, Inches(0.5), Inches(7.0), Inches(12.3), Inches(0.45),
       text, size=theme.FOOTER_SIZE, color=theme.GRAY, name="footer")


def add_placeholder(slide, x, y, w, h, label):
    shape = box(slide, x, y, w, h, label, theme.LIGHT_GRAY,
                text_color=theme.GRAY, name="screenshot_placeholder",
                shape_type=MSO_SHAPE.RECTANGLE)
    return shape
