# SHB Pitch Deck Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 18-slide Vietnamese hackathon pitch deck (.pptx) for SHB CreditOps EvidenceGraph exactly as specified in [the approved design spec](../specs/2026-07-17-shb-pitch-deck-design.md).

**Architecture:** A small Python generator (`deck/`) produces the .pptx from structured content data. All copy lives in `deck/content.py` (data only); layout code renders it. Pytest validates structure and spec compliance (disclaimer placement, forbidden claims, input slots); `deck/check_final.py` is the pre-submission gate that fails while any `[...]` input slot remains. The generated deck is fully editable in PowerPoint for visual polish; regeneration never loses copy because copy is data.

**Tech Stack:** Python 3.10+, python-pptx, pytest. Windows / PowerShell commands. No network assets.

## Global Constraints

Copied from the spec — every task implicitly includes these:

- Slide copy is Vietnamese; keep these terms in English: EvidenceGraph, Credit Case Digital Twin, maker–checker, planner–executor, RAG, agent, Case Orchestrator.
- Format 16:9 (13.333 × 7.5 in). Font: Segoe UI (full Vietnamese diacritics on Windows).
- Product name everywhere, exact string: `SHB CreditOps EvidenceGraph`.
- Phrasing rule: "Phòng tín dụng số đầu tiên **cho** SHB" — the deck must never contain "đầu tiên của SHB" or any wording implying SHB approval/endorsement/production readiness.
- Mandatory disclaimer on slides **1, 4, 5, 6, 8, 13** (slides showing case data or screenshots), Vietnamese + canonical English:
  - VN: `Toàn bộ dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống ngân hàng trong dự án là dữ liệu tổng hợp, được tạo riêng cho mục đích trình diễn.`
  - EN: `All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.`
- Numbers are never invented: metric/team/QR values stay as bracketed slots (e.g. `[X%]`, `[Họ tên]`) until real measured data exists. `check_final.py` must fail while any slot remains.
- Palette (provisional SHB-inspired; official codes are a §6 input): orange `F7941D`, deep blue `0B2D5B`. Single source: `deck/theme.py`.
- Git: conventional commits (`feat:`, `test:`, `docs:`), no attribution footer.
- Run everything from the repo root: `C:\Users\Admin\Downloads\AIV\AIV`.

## File Structure

```
conftest.py                  # empty, repo root — puts repo root on sys.path for pytest
.gitignore                   # + deck/output/, __pycache__/, .venv/
deck/
  __init__.py                # empty package marker
  requirements.txt           # python-pptx, pytest
  theme.py                   # palette, fonts, sizes, product name, disclaimer strings
  content.py                 # ALL 18 slide content specs (pure data)
  builders.py                # generic shape/text helpers (named shapes for testability)
  layouts.py                 # one render function per layout type
  build.py                   # assembly entry point: build_deck() -> output path
  check_final.py             # finalization gate: fails while [..] slots remain
  README.md                  # team guide: regenerate, fill inputs, finalize
  tests/
    test_content.py          # content data integrity vs spec
    test_deck.py             # generated pptx structure
    test_compliance.py       # disclaimer placement, forbidden claims, slots
  output/                    # build artifacts (gitignored)
deck/SHB-CreditOps-EvidenceGraph-pitch.pptx   # committed release copy (Task 8)
```

---

### Task 1: Scaffolding and theme

**Files:**
- Create: `conftest.py`, `.gitignore` (modify if exists), `deck/__init__.py`, `deck/requirements.txt`, `deck/theme.py`, `deck/tests/test_theme.py`

**Interfaces:**
- Produces: module `deck.theme` with constants `ORANGE, DEEP_BLUE, DARK_TEXT, WHITE, GRAY, LIGHT_GRAY, RED, GREEN` (RGBColor), `FONT` (str), `SLIDE_W, SLIDE_H` (Emu via Inches), `TITLE_SIZE, KILLER_SIZE, BODY_SIZE, SMALL_SIZE, FOOTER_SIZE` (Pt), `PRODUCT_NAME, DISCLAIMER_VN, DISCLAIMER_EN` (str). All later tasks import from here.

- [ ] **Step 1: Verify Python and install dependencies**

Run: `python --version`
Expected: `Python 3.10` or newer. (If missing, install from python.org, then re-run.)

Create `deck/requirements.txt`:

```text
python-pptx>=0.6.23
pytest>=8.0
```

Run: `python -m pip install -r deck/requirements.txt`
Expected: `Successfully installed ...` (or already satisfied).

- [ ] **Step 2: Create package markers and gitignore**

Create empty files `conftest.py` (repo root) and `deck/__init__.py`.

Create or append to `.gitignore`:

```text
deck/output/
__pycache__/
.venv/
```

- [ ] **Step 3: Write the failing theme test**

Create `deck/tests/test_theme.py`:

```python
from pptx.util import Inches


def test_theme_constants():
    from deck import theme

    assert theme.PRODUCT_NAME == "SHB CreditOps EvidenceGraph"
    assert theme.FONT == "Segoe UI"
    assert theme.SLIDE_W == Inches(13.333)
    assert theme.SLIDE_H == Inches(7.5)
    assert "dữ liệu tổng hợp" in theme.DISCLAIMER_VN
    assert "synthetic" in theme.DISCLAIMER_EN
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest deck/tests/test_theme.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deck.theme'`

- [ ] **Step 5: Implement `deck/theme.py`**

```python
"""Single source for palette, typography, sizes, and mandated strings.

Palette is provisional SHB-inspired; replace with official brand codes
(design-spec section 6 input) here and only here.
"""
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

ORANGE = RGBColor(0xF7, 0x94, 0x1D)
DEEP_BLUE = RGBColor(0x0B, 0x2D, 0x5B)
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
FOOTER_SIZE = Pt(8)

PRODUCT_NAME = "SHB CreditOps EvidenceGraph"

DISCLAIMER_VN = (
    "Toàn bộ dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống "
    "ngân hàng trong dự án là dữ liệu tổng hợp, được tạo riêng cho mục đích trình diễn."
)
DISCLAIMER_EN = (
    "All customer data, policies, documents, and banking-system responses "
    "in this project are synthetic and created solely for demonstration."
)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest deck/tests/test_theme.py -v`
Expected: `1 passed`

- [ ] **Step 7: Commit**

```bash
git add conftest.py .gitignore deck/__init__.py deck/requirements.txt deck/theme.py deck/tests/test_theme.py
git commit -m "feat: scaffold deck generator with SHB theme"
```

---

### Task 2: Content model — all 18 slides as data

**Files:**
- Create: `deck/content.py`, `deck/tests/test_content.py`

**Interfaces:**
- Consumes: `deck.theme.PRODUCT_NAME`
- Produces: `deck.content.SLIDES` — list of 18 dicts, each with keys `n` (int), `layout` (str), `title` (str), optional `killer` (str), optional `bullets` (list[str]), optional `extra` (dict, layout-specific), `disclaimer` (bool), optional `notes` (str, speaker notes). Also `DISCLAIMER_SLIDES = {1, 4, 5, 6, 8, 13}` and `LAYOUTS` (set of valid layout names). Later tasks index `SLIDES[i]` and read `extra` keys exactly as defined here.

- [ ] **Step 1: Write the failing content-integrity test**

Create `deck/tests/test_content.py`:

```python
from deck.content import SLIDES, DISCLAIMER_SLIDES, LAYOUTS


def test_eighteen_slides_numbered_in_order():
    assert [s["n"] for s in SLIDES] == list(range(1, 19))


def test_required_fields():
    for s in SLIDES:
        assert s["title"].strip(), f"slide {s['n']} missing title"
        assert s["layout"] in LAYOUTS, f"slide {s['n']} bad layout {s['layout']}"
        assert isinstance(s["disclaimer"], bool)


def test_disclaimer_flags_match_spec():
    flagged = {s["n"] for s in SLIDES if s["disclaimer"]}
    assert flagged == DISCLAIMER_SLIDES == {1, 4, 5, 6, 8, 13}


def test_input_slots_only_where_expected():
    # Slots [..] are deliberate data slots (spec 3.3): metrics 13/15, team 17,
    # QR 18, screenshot labels 4/6/8. Nowhere else.
    import re

    def slot_texts(s):
        chunks = [s["title"], s.get("killer", "")] + s.get("bullets", [])
        for v in (s.get("extra") or {}).values():
            chunks.append(str(v))
        return re.findall(r"\[[^\]]+\]", " ".join(chunks))

    with_slots = {s["n"] for s in SLIDES if slot_texts(s)}
    assert with_slots == {4, 6, 8, 13, 15, 17, 18}


def test_phrasing_rule_cho_shb():
    all_text = str(SLIDES)
    assert "đầu tiên cho SHB" in all_text
    assert "đầu tiên của SHB" not in all_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest deck/tests/test_content.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deck.content'`

- [ ] **Step 3: Implement `deck/content.py`**

This file is pure data — the entire deck copy, transcribed from spec §4. Create it exactly as follows:

```python
"""All slide content for the SHB CreditOps EvidenceGraph pitch deck.

Pure data. Copy is final Vietnamese on-slide text per the design spec
(docs/superpowers/specs/2026-07-17-shb-pitch-deck-design.md).
Bracketed [..] strings are deliberate input slots (spec section 6) and must
survive until real data replaces them; deck/check_final.py gates on them.
"""

DISCLAIMER_SLIDES = {1, 4, 5, 6, 8, 13}

LAYOUTS = {
    "hook", "standard", "curve", "product", "before_after", "storyboard",
    "pipeline", "provenance", "grounding", "architecture", "compare_table",
    "criteria", "validation", "axes", "roadmap", "team", "closing",
}

SLIDES = [
    {
        "n": 1,
        "layout": "hook",
        "title": "Một câu hỏi đơn giản. Hàng chục tài liệu. Năm bàn làm việc.",
        "extra": {
            "quote": "“Tôi cần 2 tỷ nhập nguyên liệu cho đơn hàng Tết — "
                     "ngân hàng cần gì để cho tôi vay?”",
            "quote_by": "Giám đốc Công ty TNHH Thực phẩm Minh An (dữ liệu tổng hợp)",
            "doc_labels": ["ĐKKD", "Điều lệ", "CCCD người ĐDPL", "BCTC 2024",
                           "BCTC 2025", "Sao kê ngân hàng", "Tờ khai VAT",
                           "HĐ mua nguyên liệu", "HĐ bán hàng", "Kế hoạch tồn kho",
                           "Hồ sơ TSBĐ", "Nghị quyết bổ nhiệm"],
        },
        "disclaimer": True,
        "notes": "Mở đầu: SME tín dụng chậm không phải ở quyết định, mà ở khâu chuẩn bị.",
    },
    {
        "n": 2,
        "layout": "standard",
        "title": "Ngân hàng không thiếu dữ liệu. Ngân hàng thiếu bộ máy kiểm chứng dữ liệu.",
        "bullets": [
            "Tài liệu — có. Chính sách — có. Chuyên gia — có.",
            "Kiểm tra đầy đủ hồ sơ: làm thủ công",
            "Mâu thuẫn doanh thu ↔ sao kê: phát hiện muộn",
            "Trích dẫn chính sách: tra cứu bằng tay",
            "Tờ trình: lắp ghép copy-paste",
            "Dấu vết kiểm toán: dựng lại sau cùng",
        ],
        "killer": "Khoảng trống không nằm ở thông tin — mà ở việc kiểm chứng "
                  "và kết nối thông tin.",
        "disclaimer": False,
    },
    {
        "n": 3,
        "layout": "curve",
        "title": "AI ngân hàng đang dừng ở trả lời câu hỏi. Nghiệp vụ cần AI làm việc.",
        "extra": {
            "stages": ["Chatbot Q&A", "RAG trả lời có nguồn", "Đội ngũ agent làm việc thật"],
            "gap": "KHOẢNG CÁCH TIN CẬY",
        },
        "bullets": [
            "Chuyên môn tín dụng nằm trong số ít chuyên gia khó nhân rộng",
            "Xu hướng 2026: AI lập kế hoạch, phối hợp, dùng công cụ, hành động",
            "Nhưng tín dụng không thể tin AI thiếu bằng chứng, kiểm toán và phân quyền",
        ],
        "killer": "Đúng lúc ngân hàng cần AI làm việc thật, chatbot chỉ có thể nói.",
        "disclaimer": False,
    },
    {
        "n": 4,
        "layout": "product",
        "title": "SHB CreditOps EvidenceGraph — Phòng tín dụng số đầu tiên cho SHB",
        "extra": {
            "pitch": "Một đội ngũ chuyên gia AI biến chồng tài liệu rời rạc thành hồ sơ "
                     "tín dụng có bằng chứng — để con người ra quyết định.",
            "hub": "Credit Case Digital Twin\n(hồ sơ số có phiên bản)",
            "roles": ["Điều phối hồ sơ (Case Orchestrator)",
                      "Tiếp nhận nhu cầu (Intake)",
                      "Thẩm định tín dụng (Underwriting)",
                      "Pháp lý – Tuân thủ – TSBĐ",
                      "Kiểm soát rủi ro độc lập",
                      "Vận hành tín dụng"],
            "promises": ["Một đội ngũ — không phải một chatbot",
                         "Mọi kết luận đều có bằng chứng",
                         "Con người quyết định"],
            "screenshot": "[ẢNH MÀN HÌNH DASHBOARD — chèn khi demo sẵn sàng]",
        },
        "disclaimer": True,
    },
    {
        "n": 5,
        "layout": "before_after",
        "title": "Từ xử lý thủ công sang hồ sơ được chuẩn bị sẵn",
        "extra": {
            "before": ["Tài liệu đến qua email / Zalo",
                       "Cán bộ phân loại, gõ lại số liệu",
                       "Thiếu giấy tờ: phát hiện sau nhiều tuần",
                       "Khách hàng bị hỏi đi hỏi lại",
                       "Thẩm định đọc lại toàn bộ hồ sơ",
                       "Tờ trình lắp ghép copy-paste"],
            "before_bar": "NHIỀU TUẦN",
            "after": ["Tải lên một lần — agent phân loại, trích xuất kèm độ tin cậy",
                      "Cán bộ xác nhận từng tài liệu",
                      "Thiếu sót & mâu thuẫn hiện ngay, kèm đề xuất bổ sung (người duyệt)",
                      "Thẩm định + Pháp lý chạy song song trên cùng hồ sơ",
                      "Phản biện độc lập maker–checker",
                      "Tờ trình kèm trích dẫn bấm-để-xem"],
            "after_bar": "VÀI NGÀY",
        },
        "killer": "Trước: con người phục vụ hồ sơ. Sau: hồ sơ được chuẩn bị "
                  "để con người quyết định.",
        "disclaimer": True,
    },
    {
        "n": 6,
        "layout": "storyboard",
        "title": "Một hồ sơ hoàn chỉnh, trong một cuộc trình diễn",
        "extra": {
            "steps": [
                "Tạo hồ sơ, tải ~20 tài liệu của Minh An",
                "Agent phân loại & trích xuất — cán bộ xác nhận từng tài liệu",
                "Bắt mâu thuẫn: doanh thu BCTC ≠ sao kê → khoảng trống BLOCKING "
                "→ người duyệt yêu cầu bổ sung",
                "Thẩm định + Pháp lý song song; công cụ tính toán xác định",
                "Kiểm soát rủi ro chất vấn: nguồn trả nợ tập trung một người mua "
                "— maker phản hồi, đối thoại được lưu vết",
                "Tờ trình dự thảo — mỗi con số bấm về nguồn; gate chờ con người quyết định",
            ],
            "screenshot": "[ẢNH MÀN HÌNH TỪNG BƯỚC — chèn khi demo sẵn sàng]",
        },
        "disclaimer": True,
        "notes": "Slide này là preview 60 giây; demo trực tiếp chạy đúng hồ sơ này.",
    },
    {
        "n": 7,
        "layout": "pipeline",
        "title": "Hệ thống không trò chuyện về hồ sơ. Hệ thống xử lý hồ sơ.",
        "extra": {
            "steps": ["1. Tiếp nhận nhu cầu", "2. Số hoá & phân loại",
                      "3. Trích xuất dữ kiện", "4. Cán bộ xác nhận",
                      "5. Khoảng trống & xung đột", "6. Phân tích song song",
                      "7. Phản biện độc lập", "8. Tờ trình & gate phê duyệt"],
        },
        "killer": "Chat không phải là hồ sơ. Hồ sơ là Credit Case Digital Twin — "
                  "có phiên bản, có bằng chứng, có kiểm toán.",
        "disclaimer": False,
    },
    {
        "n": 8,
        "layout": "provenance",
        "title": "Mọi kết luận đều được truy vết — không phỏng đoán.",
        "extra": {
            "chain": ["Phiên bản tài liệu", "Trang / vùng", "Dữ kiện trích xuất",
                      "Tính toán xác định / trích dẫn chính sách", "Nhận định (finding)",
                      "Phản biện độc lập", "Phê duyệt của con người"],
            "gap_panel": ["Vấn đề & bằng chứng hiện có", "Thông tin còn thiếu",
                          "Tài liệu đề xuất + lý do", "Cơ sở chính sách",
                          "Mức độ: BLOCKING / CONDITIONAL / CLARIFICATION",
                          "Tác vụ bị ảnh hưởng", "Trạng thái phê duyệt"],
            "screenshot": "[VÍ DỤ MINH AN: con số vốn lưu động truy về nguồn — "
                          "chèn khi demo sẵn sàng]",
        },
        "disclaimer": True,
    },
    {
        "n": 9,
        "layout": "grounding",
        "title": "Không có con số bịa. Không có chính sách tưởng tượng. "
                 "Không có kết luận thiếu nguồn.",
        "extra": {
            "sources": ["Tài liệu gốc (bất biến, có phiên bản)",
                        "Dữ kiện đã được cán bộ xác nhận",
                        "Công cụ tính toán xác định",
                        "Kho chính sách có phiên bản + trích dẫn chính xác",
                        "Tra cứu KYC/AML mô phỏng, có kiểm soát",
                        "Nhật ký kiểm toán chỉ-ghi-thêm"],
            "layer": "LỚP BẰNG CHỨNG",
            "out": "Phản hồi của agent — kèm nguồn",
            "abstain": "Thiếu nguồn → từ chối trả lời → chuyển kiểm tra thủ công",
        },
        "bullets": [
            "Fail-closed: không có nguồn thì không kết luận",
            "Tài liệu tải lên là dữ liệu, không phải mệnh lệnh — "
            "chống chỉ thị ẩn (prompt injection)",
        ],
        "disclaimer": False,
    },
    {
        "n": 10,
        "layout": "architecture",
        "title": "Kiến trúc cho độ chính xác, chủ quyền dữ liệu và khả năng mở rộng",
        "extra": {
            "bands": [
                "Vercel — giao diện tiếng Việt (Next.js)",
                "Cloud Run — FastAPI + creditops-worker · máy trạng thái xác định · "
                "cổng mô hình trung lập",
                "Supabase — Postgres (Digital Twin + EvidenceGraph) · Queues · "
                "Storage riêng tư · pgvector",
                "FPT AI Factory — suy luận có quản lý (ứng viên: Qwen3-30B-A3B, "
                "SaoLa3.1-medium, FPT.AI-KIE v1.7, Table-Parsing v1.1, "
                "Qwen2.5-VL, e5-large / Vietnamese_Embedding)",
            ],
            "trust": ["Frontend không bao giờ gọi mô hình",
                      "Mô hình không bao giờ sở hữu trạng thái hồ sơ",
                      "Mọi đầu ra qua kiểm tra schema trước khi chạm vào hồ sơ"],
            "note": "Tên mô hình là ứng viên — chốt sau benchmark tiếng Việt.",
        },
        "killer": "Mô hình có thể thay. Kiến trúc ra quyết định thì không.",
        "disclaimer": False,
    },
    {
        "n": 11,
        "layout": "compare_table",
        "title": "Chatbot tìm câu trả lời. Chúng tôi chuẩn bị quyết định.",
        "extra": {
            "cols": ["Năng lực", "Quy trình thủ công", "Chatbot RAG đơn",
                     "Multi-agent demo thông thường", "SHB CreditOps EvidenceGraph"],
            "rows": [
                ["Đọc & trích xuất tài liệu tiếng Việt (KIE, bảng biểu)",
                 "Thủ công", "—", "Một phần", "✓"],
                ["Phát hiện thiếu sót & mâu thuẫn giữa tài liệu",
                 "Muộn", "—", "Không ổn định", "✓"],
                ["Truy vết bằng chứng cho từng kết luận",
                 "Rời rạc", "Nguồn chung chung", "Hiếm", "✓"],
                ["Phân tách maker–checker (thẩm định ≠ phản biện)",
                 "✓ (chậm)", "—", "—", "✓"],
                ["Gate phê duyệt của con người trong workflow",
                 "✓", "—", "Hiếm", "✓"],
                ["Dashboard truy vết agent & kiểm toán",
                 "—", "—", "Một phần", "✓"],
                ["Chống chỉ thị ẩn trong tài liệu (prompt injection)",
                 "N/A", "Yếu", "Yếu", "Thiết kế sẵn"],
            ],
        },
        "disclaimer": False,
    },
    {
        "n": 12,
        "layout": "criteria",
        "title": "Đề bài yêu cầu — chúng tôi xây đúng, rồi đi xa hơn một tầng tin cậy.",
        "extra": {
            "rows": [
                ["≥2–3 chuyên gia số (Credit, Legal/Compliance, Operations)",
                 "6 vai trò chuyên trách — bao gồm đúng 3 vai trò đề bài nêu"],
                ["Điều phối planner–executor",
                 "Case Orchestrator phân rã & định tuyến; executor có giới hạn"],
                ["Dùng công cụ thật (API, dữ liệu, hành động)",
                 "KIE / trích xuất bảng, máy tính xác định, tra cứu mô phỏng, tạo tờ trình"],
                ["RAG chuyên ngành cho từng agent",
                 "RAG bằng chứng hồ sơ + RAG chính sách kèm trích dẫn chính xác"],
                ["Dashboard truy vết agent, trạng thái, quyết định",
                 "Giao diện truy vết / kiểm toán của sản phẩm"],
                ["So sánh với chatbot đơn agent",
                 "Đo lường đối đầu — xem slide Kiểm chứng"],
            ],
            "benefits": ["GenAI: từ trả lời → làm việc",
                         "Một hệ thống đại diện nhiều phòng ban",
                         "Giảm phụ thuộc chuyên gia cá nhân — vẫn giữ kiểm soát",
                         "Nền tảng tự động hoá quy trình đầu-cuối"],
        },
        "disclaimer": False,
    },
    {
        "n": 13,
        "layout": "validation",
        "title": "Chúng tôi không chỉ demo. Chúng tôi đo.",
        "bullets": [
            "[N] hồ sơ SME tổng hợp — 6 kịch bản: đủ / thiếu tài liệu / mâu thuẫn "
            "/ ngoại lệ chính sách / scan kém / cần xử lý tay",
            "Ground-truth gắn nhãn trước; đối đầu chatbot RAG đơn dùng CÙNG mô hình nền",
        ],
        "extra": {
            "metrics": [
                ["Độ phủ trích dẫn (kết luận có nguồn đúng)", "[X%]", "so với chatbot [Y%]"],
                ["Phát hiện khoảng trống (recall / precision)", "[X%]", ""],
                ["Tính toán qua công cụ xác định", "mục tiêu 100%", ""],
                ["Tỷ lệ khẳng định thiếu nguồn", "[X%]", "so với chatbot [Y%]"],
                ["Tuân thủ gate phê duyệt của con người", "0 lần vượt", "trên mọi lần chạy"],
                ["Thời gian chuẩn bị hồ sơ đầu-cuối", "[X giờ] → [Y phút]", "so với thủ công"],
            ],
        },
        "killer": "Câu hỏi không phải là demo có đẹp không — mà là hệ thống có đáng tin "
                  "để đứng cạnh một quyết định tín dụng không.",
        "disclaimer": True,
    },
    {
        "n": 14,
        "layout": "axes",
        "title": "Nghiệp vụ mới — cùng một bộ máy bằng chứng.",
        "bullets": [
            "Không gì trong bộ máy bị đóng cứng vào vốn lưu động: mở rộng = thêm "
            "schema tài liệu, kho chính sách, chỉ dẫn vai trò, công cụ — "
            "không phải kiến trúc mới",
            "Cổng mô hình trung lập: nâng cấp mô hình không phải xây lại ứng dụng",
        ],
        "extra": {
            "axes": [
                ["Sâu hơn trong vòng đời (kế hoạch)",
                 "Giai đoạn 7–14: thông báo, hợp đồng, TSBĐ, điều kiện giải ngân, "
                 "agent giám sát & thu hồi"],
                ["Nhiều sản phẩm tín dụng hơn (kế hoạch)",
                 "Vay trung dài hạn, bảo lãnh, tài trợ thương mại / LC, bán lẻ"],
                ["Nghiệp vụ ngân hàng khác (kế hoạch)",
                 "Rà soát KYC, chuẩn bị kiểm toán nội bộ, xử lý khiếu nại — "
                 "cùng mẫu hình: digital twin + chuyên gia giới hạn + gate con người"],
            ],
        },
        "disclaimer": False,
    },
    {
        "n": 15,
        "layout": "standard",
        "title": "Hồ sơ tốt hơn → quyết định nhanh hơn → vốn đến doanh nghiệp sớm hơn.",
        "bullets": [
            "Thời gian ra quyết định SME: từ nhiều tuần hướng tới vài ngày",
            "Ít vòng bổ sung tài liệu — thiếu sót bắt ngay từ tiếp nhận, "
            "một yêu cầu gộp duy nhất",
            "Chính sách áp dụng nhất quán giữa các chi nhánh",
            "Giảm phụ thuộc chuyên gia thâm niên khan hiếm",
            "Dấu vết kiểm toán sinh ra trong lúc làm việc — không phải dựng lại sau",
            "Chỉ tiêu (điền số đo thực): giảm [X%] thời gian chuẩn bị hồ sơ · "
            "giảm [X] vòng bổ sung · tăng [X%] công suất mỗi cán bộ",
        ],
        "killer": "Tín dụng SME là ưu tiên tăng trưởng — nút thắt là năng lực chuẩn bị "
                  "hồ sơ, và hệ thống này chính là năng lực đó.",
        "disclaimer": False,
    },
    {
        "n": 16,
        "layout": "roadmap",
        "title": "Từ nguyên mẫu hackathon đến trợ thủ tín dụng triển khai được",
        "extra": {
            "milestones": [
                ["Hackathon", "Demo hoạt động: intake + agent phối hợp + dashboard, "
                              "dữ liệu tổng hợp, trên đúng kiến trúc đích"],
                ["+1 tháng", "Benchmark tiếng Việt, chốt endpoint mô hình; "
                             "phủ đủ 6 vai trò"],
                ["+3 tháng", "Pilot shadow-mode với một đội tín dụng SHB "
                             "(cần phê duyệt dữ liệu & quản trị)"],
                ["+6 tháng", "Biên tích hợp LOS/ACAS có kiểm soát; "
                             "mở rộng chuẩn bị sau phê duyệt"],
                ["Tương lai", "Agent giám sát, tiếp nhận đa kênh, tài liệu đa phương thức"],
            ],
            "note": "Các giai đoạn sau hackathon thực hiện theo quản trị, "
                    "chấp thuận an ninh và cư trú dữ liệu của SHB.",
        },
        "disclaimer": False,
    },
    {
        "n": 17,
        "layout": "team",
        "title": "Đội ngũ xây AI đáng tin cho ngân hàng",
        "extra": {
            "members": [
                ["[Họ tên]", "Kỹ sư AI / LLM", "[Đóng góp trong demo]", "[Thế mạnh]"],
                ["[Họ tên]", "Kỹ sư backend / dữ liệu", "[Đóng góp trong demo]", "[Thế mạnh]"],
                ["[Họ tên]", "Sản phẩm & thiết kế", "[Đóng góp trong demo]", "[Thế mạnh]"],
                ["[Họ tên]", "Chuyên môn nghiệp vụ ngân hàng", "[Đóng góp trong demo]", "[Thế mạnh]"],
                ["[Họ tên]", "Trưởng nhóm đánh giá / kiểm chứng", "[Đóng góp trong demo]", "[Thế mạnh]"],
            ],
        },
        "killer": "Chúng tôi kết hợp kỹ thuật AI, tư duy sản phẩm và hiểu biết "
                  "nghiệp vụ tín dụng.",
        "disclaimer": False,
    },
    {
        "n": 18,
        "layout": "closing",
        "title": "Biến chồng tài liệu rời rạc thành quyết định tín dụng có bằng chứng.",
        "extra": {
            "ctas": ["Quét QR — demo trực tiếp hồ sơ Minh An",
                     "Đưa hệ thống một hồ sơ tổng hợp mới ngay tại chỗ",
                     "Đồng hành pilot cùng một đội tín dụng SHB"],
            "qr": "[QR DEMO — chèn liên kết khi demo sẵn sàng]",
        },
        "killer": "Không phải thêm một chatbot — mà là một đội ngũ chuyên gia số "
                  "có thể kiểm chứng, cho nghiệp vụ cốt lõi nhất của ngân hàng.",
        "disclaimer": False,
    },
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest deck/tests/test_content.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add deck/content.py deck/tests/test_content.py
git commit -m "feat: add full Vietnamese slide content model"
```

---

### Task 3: Builders and walking-skeleton build

**Files:**
- Create: `deck/builders.py`, `deck/layouts.py`, `deck/build.py`, `deck/tests/test_deck.py`

**Interfaces:**
- Consumes: `deck.theme.*`, `deck.content.SLIDES`
- Produces:
  - `deck.builders`: `add_blank(prs)`, `tb(slide, x, y, w, h, text, size=None, color=None, bold=False, align=PP_ALIGN.LEFT, name=None)` → textbox shape (size/color default to `BODY_SIZE`/`DARK_TEXT`); `box(slide, x, y, w, h, text, fill, text_color=None, size=None, bold=False, name="box", shape_type=MSO_SHAPE.ROUNDED_RECTANGLE)` → autoshape (text_color/size default to `WHITE`/`SMALL_SIZE`); `add_title(slide, spec)`; `add_killer(slide, text)`; `add_footer(slide, spec)`; `add_bullets(slide, items, x, y, w, h, size=BODY_SIZE)`; `add_placeholder(slide, x, y, w, h, label)` (gray frame, `shape.name = "screenshot_placeholder"`). All coordinates `pptx.util.Inches`.
  - `deck.layouts`: `RENDERERS: dict[str, callable]` mapping every layout name in `content.LAYOUTS` to `render(prs, spec)`. In this task every name maps to `render_standard`.
  - `deck.build`: `build_deck(out_path: str = "deck/output/deck.pptx") -> str` (returns saved path); runnable as `python -m deck.build`.

- [ ] **Step 1: Write the failing structure test**

Create `deck/tests/test_deck.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest deck/tests/test_deck.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deck.build'`

- [ ] **Step 3: Implement `deck/builders.py`**

```python
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
```

- [ ] **Step 4: Implement `deck/layouts.py` (walking skeleton)**

```python
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
```

- [ ] **Step 5: Implement `deck/build.py`**

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest deck/tests -v`
Expected: all pass (theme 1, content 5, deck 5).

- [ ] **Step 7: Visual smoke check**

Run: `python -m deck.build`
Expected output: `deck/output/deck.pptx`. Open the file (PowerPoint or LibreOffice) and confirm: 18 slides, Vietnamese titles render with correct diacritics, orange killer bands, footers.

- [ ] **Step 8: Commit**

```bash
git add deck/builders.py deck/layouts.py deck/build.py deck/tests/test_deck.py
git commit -m "feat: walking-skeleton deck build with all 18 slides"
```

---

### Task 4: Compliance tests and finalization gate

**Files:**
- Create: `deck/tests/test_compliance.py`, `deck/check_final.py`

**Interfaces:**
- Consumes: `build_deck()`, `slide_text()` pattern from Task 3, `theme.DISCLAIMER_VN`
- Produces: `python deck/check_final.py <pptx>` → exit 0 when no `[..]` slots remain, exit 1 listing each remaining slot with its slide number. Compliance pytest suite that Tasks 5–8 must keep green.

- [ ] **Step 1: Write the compliance tests**

Create `deck/tests/test_compliance.py`:

```python
import re

import pytest
from pptx import Presentation

from deck.content import DISCLAIMER_SLIDES
from deck.theme import DISCLAIMER_VN, DISCLAIMER_EN

FORBIDDEN = [
    "đầu tiên của SHB",
    "được SHB phê duyệt",
    "SHB đã phê duyệt",
    "SHB chứng thực",
    "production-ready",
    "sẵn sàng production",
    "đã được chứng nhận bảo mật",
]


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


def test_disclaimer_on_required_slides(prs):
    for idx, slide in enumerate(prs.slides, start=1):
        if idx in DISCLAIMER_SLIDES:
            text = slide_text(slide)
            assert DISCLAIMER_VN in text, f"slide {idx}: VN disclaimer missing"
            assert DISCLAIMER_EN in text, f"slide {idx}: EN disclaimer missing"


def test_no_forbidden_claims(prs):
    for idx, slide in enumerate(prs.slides, start=1):
        text = slide_text(slide).lower()
        for phrase in FORBIDDEN:
            assert phrase.lower() not in text, f"slide {idx}: forbidden '{phrase}'"


def test_input_slots_still_present_until_filled(prs):
    # Guards against someone silently deleting the slots instead of filling them.
    slides = list(prs.slides)
    for idx in (13, 15, 17):
        assert re.search(r"\[[^\]]+\]", slide_text(slides[idx - 1])), \
            f"slide {idx}: expected input slots"
```

- [ ] **Step 2: Run tests to verify current state**

Run: `python -m pytest deck/tests/test_compliance.py -v`
Expected: `3 passed` (content already carries disclaimer flags and slots; this suite now locks the invariants for all later layout work).

- [ ] **Step 3: Implement `deck/check_final.py`**

```python
"""Finalization gate: fails while any [..] input slot remains in the deck.

Usage: python deck/check_final.py [path-to-pptx]
Run before submission. Exit 0 = ready; exit 1 = slots remain (listed).
"""
import re
import sys

from pptx import Presentation


def remaining_slots(path):
    prs = Presentation(path)
    found = []
    for idx, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                texts.append(shape.text_frame.text)
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        texts.append(cell.text)
        for token in re.findall(r"\[[^\]]+\]", "\n".join(texts)):
            found.append((idx, token))
    return found


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "deck/output/deck.pptx"
    slots = remaining_slots(path)
    if not slots:
        print(f"OK: {path} contains no remaining input slots.")
        return 0
    print(f"NOT FINAL: {len(slots)} input slot(s) remain in {path}:")
    for idx, token in slots:
        print(f"  slide {idx}: {token}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verify the gate fails on the current deck**

Run: `python -m deck.build; python deck/check_final.py`
Expected: `NOT FINAL: ... slot(s) remain` and exit code 1 (check with `echo $LASTEXITCODE` → `1`). This is correct behavior until the §6 inputs are filled.

- [ ] **Step 5: Commit**

```bash
git add deck/tests/test_compliance.py deck/check_final.py
git commit -m "test: compliance suite and finalization gate for input slots"
```

---

### Task 5: Specialized layouts — hook (1), product (4), before/after (5), storyboard (6)

**Files:**
- Modify: `deck/layouts.py` (add four renderers, update `RENDERERS`)
- Test: `deck/tests/test_deck.py` (append layout tests)

**Interfaces:**
- Consumes: `builders.*` exactly as defined in Task 3; `extra` keys from Task 2 (`quote, quote_by, doc_labels`; `pitch, hub, roles, promises, screenshot`; `before, before_bar, after, after_bar`; `steps, screenshot`).
- Produces: shapes named `doc_thumb` (slide 1), `role_box` and `hub` (slide 4), `col_before`/`col_after`/`time_bar` (slide 5), `story_card` (slide 6), `screenshot_placeholder` (slides 4 and 6).

- [ ] **Step 1: Write the failing layout tests**

Append to `deck/tests/test_deck.py`:

```python
def names(slide):
    return [s.name for s in slide.shapes]


def test_hook_slide_has_doc_wall(prs):
    slide = list(prs.slides)[0]
    assert names(slide).count("doc_thumb") == 12
    assert "Minh An" in slide_text(slide)


def test_product_slide_roles_and_placeholder(prs):
    slide = list(prs.slides)[3]
    assert names(slide).count("role_box") == 6
    assert "hub" in names(slide)
    assert "screenshot_placeholder" in names(slide)


def test_before_after_columns(prs):
    slide = list(prs.slides)[4]
    ns = names(slide)
    assert "col_before" in ns and "col_after" in ns
    assert ns.count("time_bar") == 2
    assert "NHIỀU TUẦN" in slide_text(slide) and "VÀI NGÀY" in slide_text(slide)


def test_storyboard_six_cards(prs):
    slide = list(prs.slides)[5]
    assert names(slide).count("story_card") == 6
    assert "screenshot_placeholder" in names(slide)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest deck/tests/test_deck.py -v`
Expected: the four new tests FAIL (`doc_thumb` count 0, etc.); earlier tests still pass.

- [ ] **Step 3: Implement the four renderers**

In `deck/layouts.py`, add above `RENDERERS`:

```python
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
```

Then replace the `RENDERERS` line with:

```python
RENDERERS = {name: render_standard for name in content.LAYOUTS}
RENDERERS.update({
    "hook": render_hook,
    "product": render_product,
    "before_after": render_before_after,
    "storyboard": render_storyboard,
})
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `python -m pytest deck/tests -v`
Expected: all pass, including compliance (disclaimer footers still present on 1, 4, 5, 6).

- [ ] **Step 5: Visual check and commit**

Run: `python -m deck.build`, open the file, confirm slides 1/4/5/6 render sanely (overlaps acceptable — polish happens in PowerPoint later).

```bash
git add deck/layouts.py deck/tests/test_deck.py
git commit -m "feat: specialized layouts for hook, product, before-after, storyboard"
```

---

### Task 6: Diagram layouts — curve (3), pipeline (7), provenance (8), grounding (9), architecture (10)

**Files:**
- Modify: `deck/layouts.py`
- Test: `deck/tests/test_deck.py` (append)

**Interfaces:**
- Consumes: `extra` keys from Task 2 (`stages, gap`; `steps`; `chain, gap_panel, screenshot`; `sources, layer, out, abstain`; `bands, trust, note`).
- Produces: shapes named `stage_box`/`gap_box` (3), `chevron` (7), `chain_box` (8), `source_box`/`layer_box`/`abstain_box` (9), `band` (10).

- [ ] **Step 1: Write the failing tests**

Append to `deck/tests/test_deck.py`:

```python
def test_curve_slide(prs):
    slide = list(prs.slides)[2]
    ns = names(slide)
    assert ns.count("stage_box") == 3 and "gap_box" in ns


def test_pipeline_eight_chevrons(prs):
    slide = list(prs.slides)[6]
    assert names(slide).count("chevron") == 8


def test_provenance_chain(prs):
    slide = list(prs.slides)[7]
    ns = names(slide)
    assert ns.count("chain_box") == 7
    assert "screenshot_placeholder" in ns


def test_grounding_flow(prs):
    slide = list(prs.slides)[8]
    ns = names(slide)
    assert ns.count("source_box") == 6
    assert "layer_box" in ns and "abstain_box" in ns


def test_architecture_bands(prs):
    slide = list(prs.slides)[9]
    assert names(slide).count("band") == 4
    assert "Qwen3-30B-A3B" in slide_text(slide)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest deck/tests/test_deck.py -v`
Expected: the five new tests FAIL.

- [ ] **Step 3: Implement the five renderers**

Add to `deck/layouts.py` (before `RENDERERS`), then register each in `RENDERERS.update({...})` alongside the Task 5 entries (`"curve": render_curve, "pipeline": render_pipeline, "provenance": render_provenance, "grounding": render_grounding, "architecture": render_architecture`):

```python
from pptx.enum.shapes import MSO_SHAPE


def render_curve(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    heights = [Inches(1.0), Inches(1.6), Inches(2.2)]
    xs = [Inches(0.8), Inches(4.0), Inches(9.6)]
    ys = [Inches(4.6), Inches(4.0), Inches(3.4)]
    for i, stage in enumerate(x["stages"]):
        builders.box(slide, xs[i], ys[i], Inches(2.9), heights[i],
                     stage, theme.DEEP_BLUE, name="stage_box", bold=True)
    builders.box(slide, Inches(7.1), Inches(3.9), Inches(2.3), Inches(1.2),
                 x["gap"], theme.RED, name="gap_box", bold=True)
    builders.add_bullets(slide, spec["bullets"], Inches(0.8), Inches(1.4),
                         Inches(11.9), Inches(1.7), size=theme.SMALL_SIZE)
    builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide


def render_pipeline(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    for i, step in enumerate(spec["extra"]["steps"]):
        row, col = divmod(i, 4)
        builders.box(slide,
                     Inches(0.5) + col * Inches(3.15),
                     Inches(1.9) + row * Inches(1.9),
                     Inches(3.0), Inches(1.5),
                     step, theme.DEEP_BLUE if row == 0 else theme.ORANGE,
                     name="chevron", shape_type=MSO_SHAPE.CHEVRON)
    builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide


def render_provenance(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    for i, item in enumerate(x["chain"]):
        builders.box(slide, Inches(0.6), Inches(1.35) + i * Inches(0.72),
                     Inches(5.6), Inches(0.6), item, theme.DEEP_BLUE,
                     name="chain_box")
    builders.tb(slide, Inches(6.7), Inches(1.35), Inches(6.1), Inches(0.4),
                "Cấu trúc một bản ghi khoảng trống:", size=theme.SMALL_SIZE,
                color=theme.DEEP_BLUE, bold=True)
    builders.add_bullets(slide, x["gap_panel"], Inches(6.7), Inches(1.8),
                         Inches(6.1), Inches(3.2), size=theme.SMALL_SIZE)
    builders.add_placeholder(slide, Inches(6.7), Inches(5.1), Inches(6.1),
                             Inches(1.3), x["screenshot"])
    builders.add_footer(slide, spec)
    return slide


def render_grounding(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    for i, src in enumerate(x["sources"]):
        row, col = divmod(i, 2)
        builders.box(slide,
                     Inches(0.6) + col * Inches(3.0),
                     Inches(1.6) + row * Inches(1.15),
                     Inches(2.85), Inches(1.0),
                     src, theme.DEEP_BLUE, name="source_box")
    builders.box(slide, Inches(7.0), Inches(2.4), Inches(2.6), Inches(1.6),
                 x["layer"], theme.ORANGE, name="layer_box", bold=True)
    builders.box(slide, Inches(10.1), Inches(2.0), Inches(2.7), Inches(1.0),
                 x["out"], theme.GREEN, name="out_box")
    builders.box(slide, Inches(10.1), Inches(3.4), Inches(2.7), Inches(1.2),
                 x["abstain"], theme.RED, name="abstain_box")
    builders.add_bullets(slide, spec["bullets"], Inches(0.6), Inches(5.2),
                         Inches(12.1), Inches(0.9), size=theme.SMALL_SIZE)
    builders.add_footer(slide, spec)
    return slide


def render_architecture(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    for i, band in enumerate(x["bands"]):
        builders.box(slide, Inches(0.6), Inches(1.4) + i * Inches(1.0),
                     Inches(8.6), Inches(0.85), band, theme.DEEP_BLUE,
                     name="band")
    builders.tb(slide, Inches(9.5), Inches(1.4), Inches(3.3), Inches(0.4),
                "Ranh giới tin cậy:", size=theme.SMALL_SIZE,
                color=theme.DEEP_BLUE, bold=True)
    builders.add_bullets(slide, x["trust"], Inches(9.5), Inches(1.85),
                         Inches(3.3), Inches(2.6), size=theme.SMALL_SIZE)
    builders.tb(slide, Inches(0.6), Inches(5.5), Inches(12.1), Inches(0.4),
                x["note"], size=theme.SMALL_SIZE, color=theme.GRAY)
    builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest deck/tests -v`
Expected: all pass.

- [ ] **Step 5: Visual check and commit**

Run: `python -m deck.build`; open and sanity-check slides 3, 7, 8, 9, 10.

```bash
git add deck/layouts.py deck/tests/test_deck.py
git commit -m "feat: diagram layouts for curve, pipeline, provenance, grounding, architecture"
```

---

### Task 7: Table layouts — differentiation (11), criteria (12), validation (13)

**Files:**
- Modify: `deck/layouts.py`
- Test: `deck/tests/test_deck.py` (append)

**Interfaces:**
- Consumes: `extra` keys from Task 2 (`cols, rows`; `rows, benefits`; `metrics`).
- Produces: one `GraphicFrame` table per slide; frames named `compare_table`, `criteria_table`, `metric_table`.

- [ ] **Step 1: Write the failing tests**

Append to `deck/tests/test_deck.py`:

```python
def get_table(slide, name):
    for shape in slide.shapes:
        if shape.name == name and shape.has_table:
            return shape.table
    return None


def test_compare_table(prs):
    table = get_table(list(prs.slides)[10], "compare_table")
    assert table is not None
    assert len(table.rows) == 8 and len(table.columns) == 5
    assert table.cell(0, 4).text == "SHB CreditOps EvidenceGraph"


def test_criteria_table(prs):
    table = get_table(list(prs.slides)[11], "criteria_table")
    assert table is not None and len(table.rows) == 7
    assert "GenAI" in slide_text(list(prs.slides)[11])


def test_validation_metrics(prs):
    slide = list(prs.slides)[12]
    table = get_table(slide, "metric_table")
    assert table is not None and len(table.rows) == 7
    assert "[X%]" in slide_text(slide)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest deck/tests/test_deck.py -v`
Expected: the three new tests FAIL.

- [ ] **Step 3: Implement the three renderers**

Add to `deck/layouts.py` and register in `RENDERERS.update({...})` (`"compare_table": render_compare_table, "criteria": render_criteria, "validation": render_validation`):

```python
def _style_table(frame, header_fill, body_size):
    table = frame.table
    for r, row in enumerate(table.rows):
        for cell in row.cells:
            cell.fill.solid()
            cell.fill.fore_color.rgb = header_fill if r == 0 else theme.WHITE
            for para in cell.text_frame.paragraphs:
                for run in para.runs or [para.add_run()]:
                    run.font.name = theme.FONT
                    run.font.size = body_size
                    run.font.bold = r == 0
                    run.font.color.rgb = (theme.WHITE if r == 0
                                          else theme.DARK_TEXT)


def _add_table(slide, name, header, rows, x, y, w, h, body_size=None):
    frame = slide.shapes.add_table(len(rows) + 1, len(header), x, y, w, h)
    frame.name = name
    table = frame.table
    for c, text in enumerate(header):
        table.cell(0, c).text = text
    for r, row in enumerate(rows, start=1):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    _style_table(frame, theme.DEEP_BLUE, body_size or theme.SMALL_SIZE)
    return frame


def render_compare_table(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    _add_table(slide, "compare_table", x["cols"], x["rows"],
               Inches(0.5), Inches(1.4), Inches(12.3), Inches(4.9))
    builders.add_footer(slide, spec)
    return slide


def render_criteria(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    _add_table(slide, "criteria_table",
               ["Đề bài yêu cầu", "Chúng tôi có trong demo"], x["rows"],
               Inches(0.5), Inches(1.35), Inches(12.3), Inches(3.9))
    for i, benefit in enumerate(x["benefits"]):
        builders.box(slide, Inches(0.5) + i * Inches(3.15), Inches(5.6),
                     Inches(3.0), Inches(1.0), benefit, theme.ORANGE,
                     name="benefit")
    builders.add_footer(slide, spec)
    return slide


def render_validation(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    builders.add_bullets(slide, spec["bullets"], Inches(0.5), Inches(1.3),
                         Inches(12.3), Inches(1.1), size=theme.SMALL_SIZE)
    _add_table(slide, "metric_table",
               ["Chỉ số", "Kết quả", "Ghi chú"], spec["extra"]["metrics"],
               Inches(0.5), Inches(2.5), Inches(12.3), Inches(3.4))
    builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest deck/tests -v`
Expected: all pass (including `test_input_slots_still_present_until_filled`).

- [ ] **Step 5: Visual check and commit**

Run: `python -m deck.build`; check slides 11–13 tables are legible.

```bash
git add deck/layouts.py deck/tests/test_deck.py
git commit -m "feat: table layouts for differentiation, criteria, validation"
```

---

### Task 8: Final act layouts (14, 16, 17, 18), README, release copy

**Files:**
- Modify: `deck/layouts.py`
- Create: `deck/README.md`
- Test: `deck/tests/test_deck.py` (append)

(Slide 15 stays on the `standard` layout by design — bullets + killer band.)

**Interfaces:**
- Consumes: `extra` keys from Task 2 (`axes`; `milestones, note`; `members`; `ctas, qr`).
- Produces: shapes named `axis_box` (14), `milestone` (16), `member_card` (17), `cta_box` and `qr_placeholder` (18). Release file `deck/SHB-CreditOps-EvidenceGraph-pitch.pptx` committed.

- [ ] **Step 1: Write the failing tests**

Append to `deck/tests/test_deck.py`:

```python
def test_axes_slide(prs):
    assert names(list(prs.slides)[13]).count("axis_box") == 3


def test_roadmap_milestones(prs):
    assert names(list(prs.slides)[15]).count("milestone") == 5


def test_team_cards(prs):
    slide = list(prs.slides)[16]
    assert names(slide).count("member_card") == 5
    assert "[Họ tên]" in slide_text(slide)


def test_closing_slide(prs):
    slide = list(prs.slides)[17]
    ns = names(slide)
    assert ns.count("cta_box") == 3 and "qr_placeholder" in ns
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest deck/tests/test_deck.py -v`
Expected: the four new tests FAIL.

- [ ] **Step 3: Implement the four renderers**

Add to `deck/layouts.py` and register (`"axes": render_axes, "roadmap": render_roadmap, "team": render_team, "closing": render_closing`):

```python
def render_axes(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    for i, (head, body) in enumerate(spec["extra"]["axes"]):
        builders.box(slide, Inches(0.5) + i * Inches(4.3), Inches(1.5),
                     Inches(4.1), Inches(3.0), f"{head}\n\n{body}",
                     theme.DEEP_BLUE, name="axis_box")
    builders.add_bullets(slide, spec["bullets"], Inches(0.5), Inches(4.8),
                         Inches(12.3), Inches(1.2), size=theme.SMALL_SIZE)
    builders.add_footer(slide, spec)
    return slide


def render_roadmap(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    for i, (when, what) in enumerate(x["milestones"]):
        fill = theme.ORANGE if i == 0 else theme.DEEP_BLUE
        builders.box(slide, Inches(0.4) + i * Inches(2.6), Inches(2.2),
                     Inches(2.45), Inches(2.6), f"{when}\n\n{what}", fill,
                     name="milestone")
    builders.tb(slide, Inches(0.4), Inches(5.3), Inches(12.5), Inches(0.6),
                x["note"], size=theme.SMALL_SIZE, color=theme.GRAY)
    builders.add_footer(slide, spec)
    return slide


def render_team(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    for i, (name_, role, built, strength) in enumerate(spec["extra"]["members"]):
        builders.box(slide, Inches(0.4) + i * Inches(2.6), Inches(1.6),
                     Inches(2.45), Inches(3.4),
                     f"{name_}\n{role}\n\n{built}\n{strength}",
                     theme.DEEP_BLUE, name="member_card")
    builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide


def render_closing(prs, spec):
    slide = builders.add_blank(prs)
    builders.add_title(slide, spec)
    x = spec["extra"]
    for i, cta in enumerate(x["ctas"]):
        builders.box(slide, Inches(0.6), Inches(1.8) + i * Inches(1.1),
                     Inches(7.6), Inches(0.95), cta, theme.DEEP_BLUE,
                     name="cta_box")
    qr = builders.add_placeholder(slide, Inches(8.7), Inches(1.8), Inches(4.0),
                                  Inches(3.2), x["qr"])
    qr.name = "qr_placeholder"
    builders.add_killer(slide, spec["killer"])
    builders.add_footer(slide, spec)
    return slide
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest deck/tests -v`
Expected: all pass — every slide now has its specialized layout except 2 and 15 (standard by design).

- [ ] **Step 5: Write `deck/README.md`**

```markdown
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
```

- [ ] **Step 6: Produce and commit the release copy**

Run:

```powershell
python -m deck.build
Copy-Item deck/output/deck.pptx deck/SHB-CreditOps-EvidenceGraph-pitch.pptx
```

Open `deck/SHB-CreditOps-EvidenceGraph-pitch.pptx` and do a full visual pass of all 18 slides (diacritics, overlaps, legibility). Fix any layout coordinate issues in `deck/layouts.py`, rebuild, re-copy.

```bash
git add deck/layouts.py deck/tests/test_deck.py deck/README.md deck/SHB-CreditOps-EvidenceGraph-pitch.pptx
git commit -m "feat: complete deck layouts, team README, and release pptx"
```

- [ ] **Step 7: Final verification**

Run: `python -m pytest deck/tests -v`
Expected: all pass.

Run: `python deck/check_final.py deck/SHB-CreditOps-EvidenceGraph-pitch.pptx`
Expected: `NOT FINAL` with the slot list — correct until the team fills §6 inputs; the README documents the finalization flow.

---

## Plan Self-Review (completed)

- **Spec coverage:** all 18 slides have a task (1, 4, 5, 6 → Task 5; 3, 7–10 → Task 6; 11–13 → Task 7; 14, 16–18 → Task 8; 2, 15 → standard layout in Task 3); §3.3 disclaimer/claims/slots → Task 4; §5 checklist → Task 4 tests + README; §6 inputs → slots + README + gate.
- **Placeholder scan:** the `[..]` strings inside `content.py` are the spec's deliberate input slots, enforced by tests — not plan placeholders. No TBDs remain.
- **Type consistency:** builder signatures defined once in Task 3 and consumed unchanged in Tasks 5–8; `extra` keys in Task 2 match each renderer's reads; test helper names (`names`, `slide_text`, `get_table`) defined before first use.
