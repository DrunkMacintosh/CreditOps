"""Layout renderers. This task: everything renders as 'standard'.
Later tasks replace entries in RENDERERS with specialized functions."""
from pptx.util import Inches

from deck import builders, content, theme


def render_standard(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    if spec.get("bullets"):
        builders.add_bullets(slide, spec["bullets"],
                             Inches(0.7), Inches(1.5), Inches(11.9), Inches(4.4))
    if spec.get("killer"):
        builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide


def render_hook(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    builders.box(slide, Inches(0.7), Inches(2.0), Inches(5.4), Inches(2.2),
                 x["quote"], theme.DEEP_BLUE, size=theme.KILLER_SIZE, name="quote")
    builders.tb(slide, Inches(0.7), Inches(4.3), Inches(5.4), Inches(0.5),
                x["quote_by"], size=theme.SMALL_SIZE, color=theme.GRAY)
    for i, label in enumerate(x["doc_labels"]):
        row, col = divmod(i, 3)
        builders.box(slide,
                     Inches(6.8) + col * Inches(2.1),
                     Inches(1.7) + row * Inches(1.15),
                     Inches(1.95), Inches(1.0),
                     label, theme.LIGHT_GRAY, text_color=theme.DARK_TEXT,
                     name="doc_thumb")
    builders.add_footer(slide, spec)
    return slide


def render_product(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    builders.tb(slide, Inches(0.5), Inches(1.15), Inches(12.3), Inches(0.6),
                x["pitch"], size=theme.KILLER_SIZE, color=theme.DARK_TEXT, bold=True)
    builders.box(slide, Inches(4.4), Inches(3.1), Inches(3.0), Inches(1.3),
                 x["hub"], theme.ORANGE, name="hub", bold=True)
    positions = [(1.0, 2.0), (4.4, 1.85), (7.8, 2.0),
                 (1.0, 4.6), (4.4, 4.75), (7.8, 4.6)]
    for (px, py), role in zip(positions, x["roles"]):
        builders.box(slide, Inches(px), Inches(py), Inches(3.0), Inches(0.85),
                     role, theme.DEEP_BLUE, name="role_box")
    builders.add_placeholder(slide, Inches(11.0), Inches(1.9), Inches(2.0),
                             Inches(3.6), x["screenshot"])
    for i, promise in enumerate(x["promises"]):
        builders.box(slide, Inches(0.5) + i * Inches(4.2), Inches(6.15),
                     Inches(4.0), Inches(0.75), promise, theme.DEEP_BLUE,
                     name="promise", bold=True)
    builders.add_footer(slide, spec)
    return slide


def render_before_after(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    builders.box(slide, Inches(0.6), Inches(1.35), Inches(6.0), Inches(0.5),
                 "TRƯỚC", theme.GRAY, name="col_before", bold=True)
    builders.box(slide, Inches(6.85), Inches(1.35), Inches(6.0), Inches(0.5),
                 "SAU", theme.ORANGE, name="col_after", bold=True)
    builders.add_bullets(slide, x["before"], Inches(0.6), Inches(2.0),
                         Inches(6.0), Inches(3.2), size=theme.SMALL_SIZE)
    builders.add_bullets(slide, x["after"], Inches(6.85), Inches(2.0),
                         Inches(6.0), Inches(3.2), size=theme.SMALL_SIZE)
    builders.box(slide, Inches(0.6), Inches(5.35), Inches(6.0), Inches(0.5),
                 x["before_bar"], theme.RED, name="time_bar", bold=True)
    builders.box(slide, Inches(6.85), Inches(5.35), Inches(2.4), Inches(0.5),
                 x["after_bar"], theme.GREEN, name="time_bar", bold=True)
    builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide


def render_storyboard(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    for i, step in enumerate(x["steps"]):
        row, col = divmod(i, 3)
        builders.box(slide,
                     Inches(0.6) + col * Inches(4.3),
                     Inches(1.5) + row * Inches(2.5),
                     Inches(4.1), Inches(2.3),
                     f"{i + 1}. {step}", theme.DEEP_BLUE, name="story_card")
    builders.add_placeholder(slide, Inches(0.6), Inches(6.4), Inches(12.3),
                             Inches(0.45), x["screenshot"])
    builders.add_footer(slide, spec)
    return slide


RENDERERS = {name: render_standard for name in content.LAYOUTS}
RENDERERS.update({
    "hook": render_hook,
    "product": render_product,
    "before_after": render_before_after,
    "storyboard": render_storyboard,
})
