"""Single source for palette, typography, sizes, and mandated strings.

Palette is provisional SHB-inspired; replace with official brand codes
(design-spec section 6 input) here and only here.
"""
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

ORANGE = RGBColor(0xF7, 0x94, 0x1D)
DEEP_BLUE = RGBColor(0x0B, 0x2D, 0x5B)
MID_BLUE = RGBColor(0x1F, 0x4E, 0x8C)
ICE = RGBColor(0xEA, 0xF1, 0xF9)
ICE_TEXT = RGBColor(0xCA, 0xDC, 0xFC)
SLATE = RGBColor(0x44, 0x50, 0x63)
DARK_TEXT = RGBColor(0x1A, 0x1A, 0x1A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GRAY = RGBColor(0x8A, 0x8A, 0x8A)
LIGHT_GRAY = RGBColor(0xE9, 0xE9, 0xE9)
RED = RGBColor(0xC0, 0x39, 0x2B)
GREEN = RGBColor(0x1E, 0x7A, 0x3C)

FONT = "Segoe UI"

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

TITLE_SIZE = Pt(28)
KILLER_SIZE = Pt(17)
BODY_SIZE = Pt(14)
SMALL_SIZE = Pt(11)
FOOTER_SIZE = Pt(7)

PRODUCT_NAME = "SHB CreditOps EvidenceGraph"

# Canonical wording: shared/synthetic-notice.json (single source of truth).
DISCLAIMER_VN = (
    "Toàn bộ dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống "
    "ngân hàng trong dự án này là dữ liệu tổng hợp, được tạo riêng cho mục "
    "đích trình diễn."
)
DISCLAIMER_EN = (
    "All customer data, policies, documents, and banking-system responses "
    "in this project are synthetic and created solely for demonstration."
)
