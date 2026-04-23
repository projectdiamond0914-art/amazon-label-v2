"""
Amazon商品ラベル FNSKU別テキスト挿入ツール v2.0 — Web版（Streamlit）

機能:
- PDF内の「新品」テキスト右横にカスタムテキストを挿入
- FNSKUごとに3モード切替（Made in China / + 追加文言 / 追加文言のみ）
- ライセンスキー制（Googleスプレッドシート連携）
- 複数PDF一括処理・ZIP一括ダウンロード対応
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from io import BytesIO

import streamlit as st
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


# ========== 設定 ==========
APP_NAME = "Amazon商品ラベル FNSKU別テキスト挿入ツール"
APP_VERSION = "2.0.0-web"
FONT_ASCII = "Helvetica"
FONT_JP = "HeiseiKakuGo-W5"  # CID日本語フォント（ReportLab標準同梱）
FONT_SIZE = 6
RIGHT_OFFSET_PT = 5

# 日本語フォントを起動時に1回だけ登録（多重登録防止）
_JP_FONT_REGISTERED = False


def _ensure_jp_font():
    global _JP_FONT_REGISTERED
    if not _JP_FONT_REGISTERED:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(FONT_JP))
            _JP_FONT_REGISTERED = True
        except Exception:
            # 失敗しても Helvetica フォールバックで続行
            pass


def _contains_non_ascii(s: str) -> bool:
    return any(ord(c) > 127 for c in s)

# ライセンス検証用Googleスプレッドシート（CSV公開URL）
# Streamlit Cloud では st.secrets["LICENSE_CSV_URL"] が優先される
DEFAULT_LICENSE_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1bLl13Hzeygikzs-yGiLqFV1L6Ne27F3QGB3rsoy6A7U/export?format=csv&gid=1754325196"
)

# FNSKU: X + 9文字の英数字（Amazon標準）
FNSKU_PATTERN = re.compile(r"\bX[A-Z0-9]{9}\b")

# モード定義
MODE_CHINA_ONLY = "china_only"
MODE_CHINA_WITH_EXTRA = "china_with_extra"
MODE_EXTRA_ONLY = "extra_only"

MODE_LABELS = {
    MODE_CHINA_ONLY: "Made in China のみ",
    MODE_CHINA_WITH_EXTRA: "Made in China + 追加文言",
    MODE_EXTRA_ONLY: "追加文言のみ（Made in Chinaなし）",
}


# ========== ライセンス検証 ==========

def get_license_csv_url() -> str:
    """Streamlit secrets または定数からCSV URLを取得"""
    try:
        return st.secrets.get("LICENSE_CSV_URL", DEFAULT_LICENSE_CSV_URL)
    except Exception:
        return DEFAULT_LICENSE_CSV_URL


def verify_license_online(email: str, license_key: str, csv_url: str) -> tuple[bool, str]:
    """
    スプシCSV公開URLから取得して、メール+キーの有効性をチェック。
    返り値: (有効か, エラーメッセージ)
    """
    if not csv_url:
        return False, "ライセンスサーバーが未設定です。管理者にお問い合わせください。"

    try:
        req = urllib.request.Request(
            csv_url, headers={"User-Agent": "AmazonLabelTool/2.0-web"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        return False, f"ライセンスサーバーへ接続できません: {e}"
    except Exception as e:
        return False, f"検証エラー: {e}"

    reader = csv.reader(raw.splitlines())
    rows = list(reader)
    if not rows:
        return False, "ライセンスデータが空です。"

    header = [h.strip() for h in rows[0]]

    def find_col(keywords: list[str]) -> int:
        for i, h in enumerate(header):
            hl = h.lower()
            if any(k in hl or k in h for k in keywords):
                return i
        return -1

    col_email = find_col(["メール", "email", "mail"])
    col_key = find_col(["ライセンス", "license", "キー", "key"])
    col_active = find_col(["有効", "active", "status", "ステータス"])

    if col_email < 0 or col_key < 0 or col_active < 0:
        return False, "ライセンスデータの形式が不正です（列が見つかりません）。"

    for row in rows[1:]:
        if len(row) <= max(col_email, col_key, col_active):
            continue
        if row[col_email].strip().lower() == email.strip().lower():
            if row[col_key].strip() != license_key.strip():
                return False, "メールアドレスとライセンスキーが一致しません。"
            active_val = row[col_active].strip().upper()
            if active_val in ("TRUE", "✅", "有効", "OK", "1", "YES"):
                return True, ""
            else:
                return False, "ライセンスが停止されています。管理者にお問い合わせください。"

    return False, "メールアドレスが登録されていません。"


# ========== PDF処理 ==========

def find_labels_in_page(page) -> list[dict]:
    """ページ内のラベルを検出。FNSKUと「新品」座標を対応付け。"""
    fnsku_positions: list[tuple[str, float, float]] = []
    shinpin_positions: list[tuple[float, float, float]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        if not text:
            return
        x, y = tm[4], tm[5]
        match = FNSKU_PATTERN.search(text)
        if match:
            fnsku_positions.append((match.group(), x, y))
        if "新品" in text:
            shinpin_positions.append((x, y, font_size))

    page.extract_text(visitor_text=visitor)

    labels = []
    for sx, sy, sfs in shinpin_positions:
        closest_fnsku = None
        closest_dist = float("inf")
        for fnsku, fx, fy in fnsku_positions:
            dist = ((fx - sx) ** 2 + (fy - sy) ** 2) ** 0.5
            if dist < closest_dist:
                closest_dist = dist
                closest_fnsku = fnsku
        labels.append({
            "fnsku": closest_fnsku,
            "shinpin_pos": (sx, sy, sfs),
        })

    return labels


def get_text_for_fnsku(
    fnsku: str | None, rules: dict, default_mode: str, default_extra: str
) -> str:
    """FNSKUに応じて挿入するテキストを決定"""
    rule = rules.get("rules", {}).get(fnsku) if fnsku else None

    if rule:
        mode = rule.get("mode", default_mode)
        extra = rule.get("extra_text", default_extra)
    else:
        mode = default_mode
        extra = default_extra

    if mode == MODE_CHINA_ONLY:
        return "Made in China"
    elif mode == MODE_CHINA_WITH_EXTRA:
        return f"Made in China {extra}".strip() if extra else "Made in China"
    elif mode == MODE_EXTRA_ONLY:
        return extra
    return ""


def create_overlay(
    width, height, label_items, font_size, right_offset
) -> BytesIO:
    """
    複数ラベルのテキストをオーバーレイ生成。
    挿入文字列がASCIIならHelvetica、非ASCII（日本語等）が含まれれば自動で
    CID日本語フォントに切り替える。
    """
    _ensure_jp_font()

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFillColorRGB(0, 0, 0)

    for item in label_items:
        text = item.get("text")
        if not text:
            continue
        sx, sy, sfs = item["shinpin_pos"]
        text_x = sx + (sfs * 2) + right_offset
        text_y = sy
        font = FONT_JP if (_JP_FONT_REGISTERED and _contains_non_ascii(text)) else FONT_ASCII
        c.setFont(font, font_size)
        c.drawString(text_x, text_y, text)

    c.save()
    buf.seek(0)
    return buf


class PDFProcessingError(Exception):
    """ユーザー向けに分かりやすく説明できるPDF処理エラー"""
    pass


def process_pdf_bytes(
    input_bytes: bytes, rules: dict, default_mode: str, default_extra: str
) -> tuple[bytes, int, int, set[str]]:
    """
    PDFをバイト列で受けて、処理後のPDFバイト列を返す。
    返り値: (output_bytes, ページ数, ラベル数, 検出FNSKU一覧)

    ユーザー向けに分かりやすいエラーを PDFProcessingError で投げる：
      - 空ファイル
      - 不正なPDF形式
      - 暗号化（パスワード保護）PDF
      - ページ0件のPDF
    """
    if not input_bytes:
        raise PDFProcessingError("ファイルが空です。別のPDFをお試しください。")

    # マジックナンバー簡易チェック
    if not input_bytes.lstrip().startswith(b"%PDF-"):
        raise PDFProcessingError(
            "このファイルはPDF形式ではないようです。"
            "Amazonからダウンロードしたラベル用PDFをアップロードしてください。"
        )

    try:
        reader = PdfReader(BytesIO(input_bytes))
    except Exception as e:
        raise PDFProcessingError(
            f"PDFを開けませんでした。ファイルが破損している可能性があります。（詳細: {e}）"
        ) from e

    # 暗号化PDF（パスワード保護）
    if getattr(reader, "is_encrypted", False):
        # 空パスワードで復号試行
        try:
            if reader.decrypt("") == 0:
                raise PDFProcessingError(
                    "このPDFはパスワード保護されています。"
                    "保護を外してから再度アップロードしてください。"
                )
        except PDFProcessingError:
            raise
        except Exception:
            raise PDFProcessingError(
                "このPDFはパスワード保護されています。"
                "保護を外してから再度アップロードしてください。"
            )

    if len(reader.pages) == 0:
        raise PDFProcessingError("このPDFにはページがありません。別のファイルをお試しください。")

    writer = PdfWriter()
    total_labels = 0
    detected_fnskus: set[str] = set()

    for page_idx, page in enumerate(reader.pages, start=1):
        try:
            media_box = page.mediabox
            width = float(media_box.width)
            height = float(media_box.height)

            labels = find_labels_in_page(page)
            total_labels += len(labels)

            label_items = []
            for lbl in labels:
                if lbl["fnsku"]:
                    detected_fnskus.add(lbl["fnsku"])
                text = get_text_for_fnsku(
                    lbl["fnsku"], rules, default_mode, default_extra
                )
                label_items.append({
                    "shinpin_pos": lbl["shinpin_pos"],
                    "text": text,
                })

            if label_items:
                overlay_buf = create_overlay(
                    width, height, label_items,
                    FONT_SIZE, RIGHT_OFFSET_PT
                )
                overlay_reader = PdfReader(overlay_buf)
                overlay_page = overlay_reader.pages[0]
                page.merge_page(overlay_page)

            writer.add_page(page)
        except Exception as e:
            raise PDFProcessingError(
                f"{page_idx}ページ目の処理中にエラーが発生しました。（詳細: {e}）"
            ) from e

    try:
        out_buf = BytesIO()
        writer.write(out_buf)
        out_buf.seek(0)
    except Exception as e:
        raise PDFProcessingError(
            f"処理結果の書き出し中にエラーが発生しました。（詳細: {e}）"
        ) from e

    return out_buf.getvalue(), len(reader.pages), total_labels, detected_fnskus


# ========== Streamlit UI ==========

def init_session_state():
    st.session_state.setdefault("licensed", False)
    st.session_state.setdefault("user_email", "")
    st.session_state.setdefault("default_mode", MODE_CHINA_ONLY)
    st.session_state.setdefault("default_extra", "")
    st.session_state.setdefault("rules_json_text", '{"rules": {}}')


def render_login():
    """ログイン画面"""
    st.title("🔐 ログイン")
    st.markdown(
        "管理者から発行されたメールアドレスとライセンスキーを入力してください。"
    )

    with st.form("login_form"):
        email = st.text_input(
            "メールアドレス",
            placeholder="example@example.com",
            help="ご登録いただいているメールアドレス",
        )
        license_key = st.text_input(
            "ライセンスキー",
            placeholder="XXXXXXXX-XXXX-XXXX-XXXX",
            help="管理者から発行されたキー",
            type="password",
        )
        submitted = st.form_submit_button("ログイン", type="primary", use_container_width=True)

    if submitted:
        if not email or not license_key:
            st.error("メールアドレスとライセンスキーを入力してください。")
            return

        with st.spinner("ライセンスを確認中..."):
            ok, err = verify_license_online(
                email.strip(), license_key.strip(), get_license_csv_url()
            )

        if ok:
            st.session_state["licensed"] = True
            st.session_state["user_email"] = email.strip()
            st.success("ログインに成功しました。")
            st.rerun()
        else:
            st.error(f"ログインに失敗しました：{err}")


def render_sidebar():
    """サイドバー：設定とログアウト"""
    with st.sidebar:
        st.markdown(f"**バージョン**: {APP_VERSION}")
        st.markdown(f"**ログイン中**: {st.session_state['user_email']}")
        if st.button("ログアウト", use_container_width=True):
            st.session_state["licensed"] = False
            st.session_state["user_email"] = ""
            st.rerun()

        st.divider()
        st.subheader("⚙️ デフォルト設定")

        mode_label_to_key = {v: k for k, v in MODE_LABELS.items()}
        current_label = MODE_LABELS[st.session_state["default_mode"]]
        selected_label = st.radio(
            "挿入モード",
            options=list(MODE_LABELS.values()),
            index=list(MODE_LABELS.values()).index(current_label),
            help="FNSKU個別ルールがないラベルに適用されるモード",
        )
        st.session_state["default_mode"] = mode_label_to_key[selected_label]

        if st.session_state["default_mode"] in (MODE_CHINA_WITH_EXTRA, MODE_EXTRA_ONLY):
            st.session_state["default_extra"] = st.text_input(
                "追加文言",
                value=st.session_state["default_extra"],
                placeholder="例: Imported by XYZ",
            )
        else:
            st.session_state["default_extra"] = ""

        with st.expander("🧩 FNSKU個別ルール（上級者向け）"):
            st.caption(
                "特定のFNSKUのみ別モード・別文言を適用したい場合に設定。"
                "JSON形式で編集してください。"
            )
            st.session_state["rules_json_text"] = st.text_area(
                "ルールJSON",
                value=st.session_state["rules_json_text"],
                height=160,
                help=(
                    '例: {"rules": {"X01ABCDEFG": '
                    '{"mode": "china_with_extra", "extra_text": "by ABC"}}}'
                ),
            )
            try:
                json.loads(st.session_state["rules_json_text"])
                st.success("JSON形式は正しいです。")
            except json.JSONDecodeError as e:
                st.error(f"JSONエラー: {e}")


def render_processor():
    """PDF処理メイン画面"""
    st.title("📦 Amazon商品ラベル FNSKU別テキスト挿入ツール")
    st.caption(
        "「新品」の右横に「Made in China」や任意の文言を一括挿入します。"
        "複数PDFの同時処理にも対応。"
    )

    st.markdown("### ① PDFをアップロード")
    uploaded_files = st.file_uploader(
        "PDFファイルを選択（複数可・ドラッグ&ドロップOK）",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info("処理したいPDFをドラッグ&ドロップ、または「Browse files」から選択してください。")
        return

    st.markdown(f"**{len(uploaded_files)}件** のPDFがアップロードされました。")

    # ルールJSONパース
    try:
        rules = json.loads(st.session_state["rules_json_text"])
    except json.JSONDecodeError:
        st.warning("FNSKU個別ルールのJSONが不正なため、デフォルト設定のみで処理します。")
        rules = {"rules": {}}

    default_mode = st.session_state["default_mode"]
    default_extra = st.session_state["default_extra"]

    st.markdown("### ② 現在の挿入設定")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"- **モード**: {MODE_LABELS[default_mode]}")
    with col2:
        st.markdown(
            f"- **追加文言**: `{default_extra}`" if default_extra else "- **追加文言**: （なし）"
        )

    st.markdown("### ③ 実行")
    if st.button("▶️ PDFを処理する", type="primary", use_container_width=True):
        process_uploaded_files(uploaded_files, rules, default_mode, default_extra)


def process_uploaded_files(uploaded_files, rules, default_mode, default_extra):
    """アップロードされたPDFを順次処理して結果を表示"""
    results = []
    progress = st.progress(0.0, text="処理を開始します...")
    log_area = st.container()

    total = len(uploaded_files)
    for i, uploaded in enumerate(uploaded_files, start=1):
        progress.progress(
            (i - 1) / total, text=f"[{i}/{total}] {uploaded.name} を処理中..."
        )
        try:
            output_bytes, num_pages, num_labels, fnskus = process_pdf_bytes(
                uploaded.getvalue(), rules, default_mode, default_extra
            )
            results.append({
                "name": uploaded.name,
                "output": output_bytes,
                "pages": num_pages,
                "labels": num_labels,
                "fnskus": fnskus,
                "error": None,
            })
            with log_area:
                if num_labels == 0:
                    st.warning(
                        f"⚠️ {uploaded.name} — 処理は完了しましたが、"
                        f"「新品」テキストが検出されませんでした"
                        f"（{num_pages}ページ）。Amazonラベル形式のPDFかご確認ください。"
                    )
                else:
                    st.success(
                        f"✅ {uploaded.name} — {num_pages}ページ / {num_labels}ラベル / {len(fnskus)}種類のFNSKU"
                    )
        except PDFProcessingError as e:
            # ユーザー向けメッセージがそのまま表示できる既知エラー
            results.append({
                "name": uploaded.name, "output": None, "pages": 0,
                "labels": 0, "fnskus": set(), "error": str(e),
            })
            with log_area:
                st.error(f"❌ {uploaded.name} — {e}")
        except Exception as e:
            # 想定外エラーは詳細をトレースに残す
            import traceback
            tb = traceback.format_exc()
            results.append({
                "name": uploaded.name, "output": None, "pages": 0,
                "labels": 0, "fnskus": set(), "error": str(e),
            })
            with log_area:
                st.error(
                    f"❌ {uploaded.name} — 予期しないエラーが発生しました。"
                    "お手数ですが管理者にこのメッセージをスクリーンショットでお送りください。"
                )
                with st.expander("エラー詳細（管理者に共有してください）"):
                    st.code(tb)

    progress.progress(1.0, text=f"完了：{total}件を処理しました。")

    render_results(results)


def render_results(results):
    """処理結果のダウンロードUI"""
    success_results = [r for r in results if r["output"] is not None]
    if not success_results:
        st.warning("正常に処理できたファイルはありませんでした。")
        return

    st.markdown("### ④ ダウンロード")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 単一ファイル：直接ダウンロード
    if len(success_results) == 1:
        r = success_results[0]
        out_name = _make_output_filename(r["name"])
        st.download_button(
            label=f"⬇️ {out_name} をダウンロード",
            data=r["output"],
            file_name=out_name,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
        _render_fnsku_detail(r)
        return

    # 複数ファイル：ZIP一括 + 個別
    zip_buf = BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in success_results:
            zf.writestr(_make_output_filename(r["name"]), r["output"])
    zip_buf.seek(0)

    st.download_button(
        label=f"⬇️ すべてをZIPで一括ダウンロード（{len(success_results)}件）",
        data=zip_buf.getvalue(),
        file_name=f"labels_processed_{timestamp}.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True,
    )

    with st.expander("個別にダウンロードする"):
        for r in success_results:
            out_name = _make_output_filename(r["name"])
            st.download_button(
                label=out_name,
                data=r["output"],
                file_name=out_name,
                mime="application/pdf",
                key=f"dl_{out_name}",
            )

    # FNSKU詳細
    with st.expander("検出FNSKU一覧"):
        for r in success_results:
            _render_fnsku_detail(r)


def _render_fnsku_detail(r: dict):
    st.markdown(
        f"**{r['name']}** — {r['pages']}ページ / {r['labels']}ラベル / {len(r['fnskus'])}種類"
    )
    if r["fnskus"]:
        st.code("\n".join(sorted(r["fnskus"])))
    else:
        st.caption("（FNSKUは検出されませんでした）")


def _make_output_filename(input_name: str) -> str:
    """入力ファイル名から出力ファイル名を生成"""
    if input_name.lower().endswith(".pdf"):
        base = input_name[:-4]
    else:
        base = input_name
    return f"{base}_中国表記追加.pdf"


# ========== エントリポイント ==========

def main():
    st.set_page_config(
        page_title=APP_NAME,
        page_icon="📦",
        layout="centered",
    )

    init_session_state()

    if not st.session_state["licensed"]:
        render_login()
    else:
        render_sidebar()
        render_processor()


if __name__ == "__main__":
    main()
