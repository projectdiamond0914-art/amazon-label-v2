"""
合成PDFを使ったコアパイプライン（process_pdf_bytes）の動作確認。

- Amazonラベル風PDFをReportLabで生成（FNSKU + 「新品」テキスト含む）
- app.process_pdf_bytes で処理
- 生成PDFに「Made in China」が挿入されていることを検証
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from app import process_pdf_bytes, MODE_CHINA_ONLY, MODE_CHINA_WITH_EXTRA

# CID日本語フォント（「新品」を描画するために必要）
pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
JP_FONT = "HeiseiKakuGo-W5"


def build_fake_label_pdf() -> bytes:
    """
    Amazonラベルっぽい2ページのPDFを合成。各ページに：
      - FNSKU（例: X01ABCDEFG）
      - 商品名（適当）
      - 「新品」（日本語フォントで描画）
    を配置する。
    """
    buf = BytesIO()
    page_size = (300, 180)  # 幅300pt × 高180pt のラベルサイズ
    c = canvas.Canvas(buf, pagesize=page_size)

    # --- ページ1 ---
    c.setFont("Helvetica", 8)
    c.drawString(10, 160, "Test Product A")
    c.drawString(10, 140, "X01ABCDEFG")  # FNSKU
    c.setFont(JP_FONT, 10)
    c.drawString(10, 120, "新品")  # これを検出して右側にテキストを追加する
    c.showPage()

    # --- ページ2 ---
    c.setFont("Helvetica", 8)
    c.drawString(10, 160, "Test Product B")
    c.drawString(10, 140, "X02FGHIJKL")
    c.setFont(JP_FONT, 10)
    c.drawString(10, 120, "新品")
    c.showPage()

    c.save()
    buf.seek(0)
    return buf.getvalue()


def extract_all_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_china_only_mode():
    input_bytes = build_fake_label_pdf()
    output_bytes, pages, labels, fnskus = process_pdf_bytes(
        input_bytes, rules={"rules": {}}, default_mode=MODE_CHINA_ONLY, default_extra=""
    )

    print(f"[china_only] pages={pages}, labels={labels}, fnskus={sorted(fnskus)}")
    assert pages == 2, f"expected 2 pages, got {pages}"
    assert labels == 2, f"expected 2 labels detected, got {labels}"
    assert fnskus == {"X01ABCDEFG", "X02FGHIJKL"}, f"unexpected FNSKUs: {fnskus}"

    all_text = extract_all_text(output_bytes)
    # 「Made in China」が挿入されていること
    made_in_china_count = all_text.count("Made in China")
    print(f"[china_only] 'Made in China' occurrences in output: {made_in_china_count}")
    assert made_in_china_count >= 2, (
        f"'Made in China' should appear at least 2 times, got {made_in_china_count}"
    )

    # 「新品」は保持されていること
    assert "新品" in all_text, "original '新品' text missing from output"


def test_china_with_extra_mode():
    input_bytes = build_fake_label_pdf()
    output_bytes, pages, labels, _ = process_pdf_bytes(
        input_bytes,
        rules={"rules": {}},
        default_mode=MODE_CHINA_WITH_EXTRA,
        default_extra="Imported by GranreveInc.",
    )
    all_text = extract_all_text(output_bytes)
    print(f"[china_with_extra] output text sample: {all_text[:200]!r}")
    assert "Made in China" in all_text
    assert "Imported by GranreveInc." in all_text


def test_fnsku_rule_override():
    """FNSKU個別ルール: X01ABCDEFG だけ extra_only モードに切り替え"""
    input_bytes = build_fake_label_pdf()
    rules = {
        "rules": {
            "X01ABCDEFG": {"mode": "extra_only", "extra_text": "Special Note A"},
        }
    }
    output_bytes, _, _, _ = process_pdf_bytes(
        input_bytes,
        rules=rules,
        default_mode=MODE_CHINA_ONLY,
        default_extra="",
    )
    all_text = extract_all_text(output_bytes)
    print(f"[rule_override] output text sample: {all_text[:300]!r}")
    assert "Special Note A" in all_text, "FNSKU rule override did not apply"
    # X02FGHIJKL はデフォルト(china_only)なので Made in China が残るはず
    assert "Made in China" in all_text


def run_all():
    print("=" * 60)
    print("Running core PDF pipeline tests...")
    print("=" * 60)
    test_china_only_mode()
    print("✅ test_china_only_mode")
    test_china_with_extra_mode()
    print("✅ test_china_with_extra_mode")
    test_fnsku_rule_override()
    print("✅ test_fnsku_rule_override")
    print("=" * 60)
    print("All tests passed.")


if __name__ == "__main__":
    run_all()
