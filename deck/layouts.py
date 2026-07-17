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


RENDERERS = {name: render_standard for name in content.LAYOUTS}
