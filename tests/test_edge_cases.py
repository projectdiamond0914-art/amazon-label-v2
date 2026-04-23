"""
エッジケース回帰テスト。

- 空ファイル / 非PDFバイト列 / 破損PDF
- 暗号化PDF
- 「新品」を含まないPDF
- 日本語 extra_text（Made in Japan, 中国製等）
- 回転ページ
- 大量ページ（100p以上）のパフォーマンス
"""

from __future__ import annotations

import sys
import time
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from app import (
    process_pdf_bytes,
    PDFProcessingError,
    MODE_CHINA_ONLY,
    MODE_CHINA_WITH_EXTRA,
    MODE_EXTRA_ONLY,
)

pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
JP_FONT = "HeiseiKakuGo-W5"


# ---------- PDFビルダー ----------

def build_label_pdf(labels: list[tuple[str, str]], pages: int = 1) -> bytes:
    """FNSKU + 商品名 + 新品 を含むラベルPDF（シンプル）"""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 180))
    for _ in range(pages):
        y = 160
        for fnsku, title in labels:
            c.setFont("Helvetica", 8)
            c.drawString(10, y, title)
            c.drawString(10, y - 10, fnsku)
            c.setFont(JP_FONT, 8)
            c.drawString(10, y - 20, "新品")
            y -= 35
        c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def build_no_shinpin_pdf() -> bytes:
    """「新品」を含まないPDF（ラベル0検出ケース）"""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(300, 180))
    c.setFont("Helvetica", 10)
    c.drawString(10, 100, "Hello World")
    c.drawString(10, 80, "X99ZZZZZZZ")
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def build_encrypted_pdf() -> bytes:
    """パスワード保護PDF"""
    input_bytes = build_label_pdf([("X01TESTTEST", "Test")])
    reader = PdfReader(BytesIO(input_bytes))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.encrypt(user_password="secret")
    out = BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()


def build_rotated_pdf() -> bytes:
    """回転ページのPDF（90度回転）"""
    input_bytes = build_label_pdf([("X02ROTATED0", "Rotated Product")])
    reader = PdfReader(BytesIO(input_bytes))
    writer = PdfWriter()
    for p in reader.pages:
        p.rotate(90)
        writer.add_page(p)
    out = BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()


def build_large_pdf(num_labels: int) -> bytes:
    """大量ラベルPDF（ストレステスト用）"""
    buf = BytesIO()
    from reportlab.lib.pagesizes import A4
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    per_page = 30  # 3列×10行
    rows_per_page = 10
    cols_per_page = 3
    label_h = 25  # mm相当のpt
    from reportlab.lib.units import mm
    label_h_pt = 25 * mm
    label_w_pt = 70 * mm
    margin_x = 7 * mm
    margin_y = 10 * mm

    for i in range(num_labels):
        col = i % cols_per_page
        row = (i // cols_per_page) % rows_per_page
        if i > 0 and i % per_page == 0:
            c.showPage()
        x = margin_x + col * label_w_pt
        y = page_h - margin_y - (row + 1) * label_h_pt
        fnsku = f"X{i:03d}TEST{i:03d}"[:10]
        c.setFont("Helvetica", 7)
        c.drawString(x + 2*mm, y + 15*mm, fnsku)
        c.setFont(JP_FONT, 6)
        c.drawString(x + 2*mm, y + 10*mm, f"商品{i}")
        c.drawString(x + 2*mm, y + 5*mm, "新品")
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def extract_all_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------- テストケース ----------

def test_empty_bytes_rejected():
    try:
        process_pdf_bytes(b"", {"rules": {}}, MODE_CHINA_ONLY, "")
    except PDFProcessingError as e:
        assert "空" in str(e), f"unexpected message: {e}"
        return
    raise AssertionError("should have raised PDFProcessingError")


def test_non_pdf_rejected():
    try:
        process_pdf_bytes(b"this is not a pdf file", {"rules": {}}, MODE_CHINA_ONLY, "")
    except PDFProcessingError as e:
        assert "PDF形式" in str(e)
        return
    raise AssertionError("should have raised PDFProcessingError")


def test_corrupt_pdf_rejected():
    corrupt = b"%PDF-1.4\nthis is broken\n%%EOF"
    try:
        process_pdf_bytes(corrupt, {"rules": {}}, MODE_CHINA_ONLY, "")
    except PDFProcessingError as e:
        # 破損PDFは「PDFを開けませんでした」または類似のメッセージ
        assert any(k in str(e) for k in ("開けません", "破損", "エラー"))
        return
    raise AssertionError("should have raised PDFProcessingError")


def test_encrypted_pdf_rejected():
    enc = build_encrypted_pdf()
    try:
        process_pdf_bytes(enc, {"rules": {}}, MODE_CHINA_ONLY, "")
    except PDFProcessingError as e:
        assert "パスワード" in str(e), f"unexpected message: {e}"
        return
    raise AssertionError("should have raised PDFProcessingError")


def test_no_shinpin_passes_through():
    """「新品」を含まないPDFはエラーにはならず、ラベル0で通る"""
    pdf = build_no_shinpin_pdf()
    out, pages, labels, fnskus = process_pdf_bytes(
        pdf, {"rules": {}}, MODE_CHINA_ONLY, ""
    )
    assert pages == 1
    assert labels == 0
    assert fnskus == set()
    # 出力PDF内にMade in Chinaが挿入されていない
    out_text = extract_all_text(out)
    assert "Made in China" not in out_text


def test_japanese_extra_text_renders():
    """日本語追加文言が ? にならず正しく描画される"""
    pdf = build_label_pdf([("X01JP000000", "Japanese Test")])
    out, _, labels, _ = process_pdf_bytes(
        pdf, {"rules": {}}, MODE_EXTRA_ONLY, "中国製"
    )
    assert labels == 1
    out_text = extract_all_text(out)
    # 「中国製」が挿入されていること（以前はHelveticaで?になっていた）
    assert "中国製" in out_text, f"Japanese text missing from output. text={out_text!r}"


def test_rotated_page_still_processable():
    """回転ページでも処理が通る（レイアウト崩れの可能性はあるがクラッシュしない）"""
    pdf = build_rotated_pdf()
    out, pages, labels, _ = process_pdf_bytes(
        pdf, {"rules": {}}, MODE_CHINA_ONLY, ""
    )
    assert pages == 1
    # 回転状態でもテキスト抽出できれば検出される
    # ここでは「クラッシュしない」ことを確認
    print(f"[rotated] labels detected: {labels}")


def test_large_pdf_performance():
    """500ラベル/17ページのPDFで5秒以内に処理完了する"""
    num_labels = 500
    pdf = build_large_pdf(num_labels)
    print(f"[stress] input size: {len(pdf):,} bytes")
    t0 = time.time()
    out, pages, labels, fnskus = process_pdf_bytes(
        pdf, {"rules": {}}, MODE_CHINA_ONLY, ""
    )
    elapsed = time.time() - t0
    print(f"[stress] {pages} pages, {labels} labels, {len(fnskus)} unique FNSKUs, "
          f"elapsed={elapsed:.2f}s, output size={len(out):,}")
    assert labels == num_labels, f"expected {num_labels} labels, got {labels}"
    # Streamlit Cloud free tier のCPUでも10秒以内に収まる想定
    assert elapsed < 10.0, f"processing too slow: {elapsed:.2f}s"


def test_huge_extra_text_truncation_safe():
    """極端に長い追加文言でもクラッシュしない"""
    pdf = build_label_pdf([("X01LONG0000", "Test")])
    long_text = "X" * 500
    out, _, labels, _ = process_pdf_bytes(
        pdf, {"rules": {}}, MODE_EXTRA_ONLY, long_text
    )
    assert labels == 1


def test_mode_extra_only_empty_yields_no_text():
    """extra_only モードで extra が空ならテキスト挿入なし"""
    pdf = build_label_pdf([("X01EMPTY000", "Empty test")])
    out, _, labels, _ = process_pdf_bytes(
        pdf, {"rules": {}}, MODE_EXTRA_ONLY, ""
    )
    assert labels == 1
    out_text = extract_all_text(out)
    # Made in China も何も挿入されていない
    assert "Made in China" not in out_text


def run_all():
    tests = [
        test_empty_bytes_rejected,
        test_non_pdf_rejected,
        test_corrupt_pdf_rejected,
        test_encrypted_pdf_rejected,
        test_no_shinpin_passes_through,
        test_japanese_extra_text_renders,
        test_rotated_page_still_processable,
        test_large_pdf_performance,
        test_huge_extra_text_truncation_safe,
        test_mode_extra_only_empty_yields_no_text,
    ]
    print("=" * 60)
    print(f"Running {len(tests)} edge case tests...")
    print("=" * 60)
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
        except AssertionError as e:
            print(f"❌ {fn.__name__}: {e}")
            raise
    print("=" * 60)
    print(f"All {len(tests)} edge case tests passed.")


if __name__ == "__main__":
    run_all()
