"""
Amazon FBA ラベルPDFの忠実再現サンプルを生成する。

公知のAmazon FBA商品ラベルフォーマットに基づき：
- Code128バーコード（FNSKUエンコード）
- FNSKU テキスト
- 商品タイトル（日本語）
- 「新品」表記
- A4用紙に 3列×10行 の30ラベル配置（標準ラベルシート）
を生成し、これを `pypdf` で読んだときに visitor_text が期待通り発火するか検証する。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import barcode
from barcode.writer import ImageWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

# 日本語フォント登録（pypdfの visitor_text はフォント種類に依存しない）
pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
JP_FONT = "HeiseiKakuGo-W5"

# Avery 5160互換（日本で多いFBA用ラベルシート：3列×10行）
LABEL_WIDTH = 70 * mm
LABEL_HEIGHT = 25 * mm
MARGIN_LEFT = 7 * mm
MARGIN_TOP = 10 * mm
COLS = 3
ROWS = 10

# サンプルデータ: FNSKU × 商品名
SAMPLES = [
    ("X001ABC001", "ワイヤレスイヤホン Bluetooth5.3 ノイズキャンセリング"),
    ("X002DEF002", "スマホスタンド 折りたたみ式 アルミ合金"),
    ("X003GHI003", "USB-C ハブ 7in1 4K HDMI対応"),
    ("X004JKL004", "モバイルバッテリー 10000mAh 急速充電 PD対応"),
    ("X005MNO005", "LEDデスクライト タッチ式 目に優しい 3段階調光"),
    ("X006PQR006", "ワイヤレス充電器 Qi対応 15W 急速"),
    ("X007STU007", "ノートパソコンスタンド 折りたたみ 高さ調節"),
    ("X008VWX008", "Bluetoothキーボード 日本語配列 薄型"),
    ("X009YZA009", "ワイヤレスマウス 静音 充電式"),
    ("X010BCD010", "HDMIケーブル 2m 4K 60Hz 対応"),
]


def make_barcode_png(code: str) -> BytesIO:
    """FNSKU を Code128 バーコード画像（PNG）にする"""
    Code128 = barcode.get_barcode_class("code128")
    bc = Code128(code, writer=ImageWriter())
    buf = BytesIO()
    bc.write(buf, options={
        "module_width": 0.25,
        "module_height": 8.0,
        "font_size": 0,       # バーコード自体には数字を付けない
        "text_distance": 1,
        "write_text": False,
        "quiet_zone": 1,
    })
    buf.seek(0)
    return buf


def draw_label(c: canvas.Canvas, x: float, y: float, fnsku: str, title: str):
    """1ラベル分を描画（Amazon FBA標準レイアウト風）"""
    # 枠（視認用・実物にはない場合もあるが）
    c.setStrokeGray(0.85)
    c.rect(x, y, LABEL_WIDTH, LABEL_HEIGHT)

    # バーコード画像
    bc_buf = make_barcode_png(fnsku)
    from reportlab.lib.utils import ImageReader
    img = ImageReader(bc_buf)
    bc_w = 55 * mm
    bc_h = 10 * mm
    c.drawImage(img, x + 2*mm, y + LABEL_HEIGHT - bc_h - 1*mm,
                width=bc_w, height=bc_h, mask="auto")

    # FNSKU文字
    c.setFont("Helvetica", 7)
    c.setFillGray(0)
    c.drawString(x + 2*mm, y + LABEL_HEIGHT - bc_h - 3.5*mm, fnsku)

    # 商品名（日本語）
    c.setFont(JP_FONT, 6)
    title_trimmed = title if len(title) <= 28 else title[:27] + "…"
    c.drawString(x + 2*mm, y + LABEL_HEIGHT - bc_h - 6.5*mm, title_trimmed)

    # 「新品」（Amazon FBA は "New" または「新品」でコンディション記載）
    c.setFont(JP_FONT, 6)
    c.drawString(x + 2*mm, y + LABEL_HEIGHT - bc_h - 9*mm, "新品")


def build_amazon_like_pdf(output_path: Path, num_pages: int = 2):
    c = canvas.Canvas(str(output_path), pagesize=A4)
    page_w, page_h = A4

    sample_i = 0
    for page_idx in range(num_pages):
        for row in range(ROWS):
            for col in range(COLS):
                x = MARGIN_LEFT + col * LABEL_WIDTH
                y = page_h - MARGIN_TOP - (row + 1) * LABEL_HEIGHT
                fnsku, title = SAMPLES[sample_i % len(SAMPLES)]
                draw_label(c, x, y, fnsku, title)
                sample_i += 1
        c.showPage()

    c.save()
    print(f"Generated realistic Amazon-like label PDF: {output_path}")
    print(f"  pages={num_pages}, labels_per_page={COLS * ROWS}, "
          f"total_labels={num_pages * COLS * ROWS}")


if __name__ == "__main__":
    out_dir = Path(__file__).resolve().parent.parent / "sample_data"
    out_dir.mkdir(exist_ok=True)
    output = out_dir / "realistic_amazon_labels.pdf"
    build_amazon_like_pdf(output, num_pages=2)
