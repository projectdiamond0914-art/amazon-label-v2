"""
v2.0.3 (pdfplumberベース) のリグレッションテスト。
実際のラベル PDF（reportlab で動的生成）を使って、find_labels_in_page が
全行・全列を正しくマッチさせることを保証する。
"""
import sys
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pdfplumber  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from main import find_labels_in_page  # noqa: E402


def _build_grid_pdf(fnskus_by_row, page_w=595.0, page_h=842.0, cols=4):
    """簡易ラベルシート PDF を生成。同 y に FNSKU と「新品」を並べる。"""
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setFont("HeiseiKakuGo-W5", 9)

    n_rows = len(fnskus_by_row)
    top_margin = 30
    row_h = (page_h - top_margin * 2) / n_rows
    col_w = page_w / cols

    for ri, fnskus in enumerate(fnskus_by_row):
        # ReportLab は左下原点。上から ri 行目の y。
        y = page_h - top_margin - row_h * (ri + 1) + row_h * 0.5
        for ci in range(cols):
            x = ci * col_w + 10
            f = fnskus[ci]
            # 「FNSKU 新品」を同じ x, y に並べる（pdfplumber が同じ行と認識する）
            c.drawString(x, y, f"{f} 新品")
    c.save()
    buf.seek(0)
    return buf


def test_4x10_grid_all_match():
    """4列 × 10行のグリッドで、最下行を含む全 40 ラベルが正しくマッチ"""
    fnskus = ["X001BKHD03", "X001B09B49", "X001B09EU5", "X001BKJFZT"]
    rows = [fnskus] * 10
    buf = _build_grid_pdf(rows)
    with pdfplumber.open(buf) as pdf:
        page = pdf.pages[0]
        labels = find_labels_in_page(page)

    assert len(labels) == 40, f"40ラベル想定 / 実際 {len(labels)}"

    # 上から下、左から右で並べる
    labels.sort(key=lambda l: (-l["shinpin_pos"][1], l["shinpin_pos"][0]))
    for idx, lab in enumerate(labels):
        expected = fnskus[idx % 4]
        assert lab["fnsku"] == expected, (
            f"#{idx}: 期待={expected} / 実際={lab['fnsku']}"
        )


def test_4x10_mixed_fnskus():
    """各行で FNSKU が変わるパターン（メル太郎の Page 2 相当）"""
    rows = [
        ["X001BKJFZT", "X001B02CMR", "X001BSWJQD", "X001BH4U9X"],
        ["X001BKJFZT", "X001B02CMR", "X001BSWJQD", "X001BH4U9X"],
        ["X001BKJFZT", "X001B02CMR", "X001BSWJQD", "X001BH4U9X"],
        ["X001B02CMR", "X001BSWJQD", "X001BSWJQD", "X001BH4U9X"],
        ["X001B02CMR", "X001BSWJQD", "X001BSWJQD", "X001BH4U9X"],
        ["X001B02CMR", "X001BSWJQD", "X001BSWJQD", "X001BH4U9X"],
        ["X001B02CMR", "X001BSWJQD", "X001BSWJQD", "X001BH4U9X"],
        ["X001B02CMR", "X001BSWJQD", "X001BSWJQD", "X001BH4U9X"],
        ["X001B02CMR", "X001BSWJQD", "X001BSWJQD", "X001BH4U9X"],
        ["X001B02CMR", "X001BSWJQD", "X001BSWJQD", "X001BH4U9X"],
    ]
    buf = _build_grid_pdf(rows)
    with pdfplumber.open(buf) as pdf:
        page = pdf.pages[0]
        labels = find_labels_in_page(page)
    labels.sort(key=lambda l: (-l["shinpin_pos"][1], l["shinpin_pos"][0]))
    flat_expected = [f for row in rows for f in row]
    for idx, lab in enumerate(labels):
        assert lab["fnsku"] == flat_expected[idx], (
            f"#{idx}: 期待={flat_expected[idx]} / 実際={lab['fnsku']}"
        )
