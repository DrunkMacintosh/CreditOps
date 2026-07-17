from pptx.util import Inches


def test_theme_constants():
    from deck import theme

    assert theme.PRODUCT_NAME == "SHB CreditOps EvidenceGraph"
    assert theme.FONT == "Segoe UI"
    assert theme.SLIDE_W == Inches(13.333)
    assert theme.SLIDE_H == Inches(7.5)
    assert "dữ liệu tổng hợp" in theme.DISCLAIMER_VN
    assert "synthetic" in theme.DISCLAIMER_EN
