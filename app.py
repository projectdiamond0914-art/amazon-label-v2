"""
Amazon商品ラベル FNSKU別テキスト挿入ツール v2.0 — Web版（Streamlit）

機能:
- PDF内の「新品」テキスト右横にカスタムテキストを挿入
- FNSKUごとに個別ルールを設定（表形式UI）
- ライセンスキー制（Googleスプレッドシート連携 + 都度再検証）
- 複数PDF一括処理・ZIP一括ダウンロード対応
- 設定のブラウザ永続保存（localStorage）
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from streamlit_local_storage import LocalStorage


# ========== 設定 ==========
APP_NAME = "Amazon商品ラベル FNSKU別テキスト挿入ツール"
APP_VERSION = "2.1.0-web"
FONT_ASCII = "Helvetica"
FONT_JP = "HeiseiKakuGo-W5"  # CID日本語フォント（ReportLab標準同梱）
FONT_SIZE = 6
RIGHT_OFFSET_PT = 5

# ライセンス再検証のキャッシュ有効期間（秒）
# 処理実行時は強制的に最新を取得するためTTLを無視する
VERIFY_CACHE_TTL_SEC = 10

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
DEFAULT_LICENSE_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1bLl13Hzeygikzs-yGiLqFV1L6Ne27F3QGB3rsoy6A7U/export?format=csv&gid=1754325196"
)

# FNSKU: X + 9文字の英数字（Amazon標準）
FNSKU_PATTERN = re.compile(r"\bX[A-Z0-9]{9}\b")

# モード定義（内部処理用）
MODE_CHINA_ONLY = "china_only"
MODE_CHINA_WITH_EXTRA = "china_with_extra"
MODE_EXTRA_ONLY = "extra_only"
MODE_NONE = "none"  # 何も挿入しない


# ========== ライセンス検証 ==========

def get_license_csv_url() -> str:
    """Streamlit secrets または定数からCSV URLを取得（キャッシュバスティング付き）"""
    try:
        base = st.secrets.get("LICENSE_CSV_URL", DEFAULT_LICENSE_CSV_URL)
    except Exception:
        base = DEFAULT_LICENSE_CSV_URL
    # 中間CDNの念のための対策：毎回ユニークなパラメータを付与
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}_cb={int(time.time() * 1000)}"


def verify_license_online(email: str, license_key: str, csv_url: str) -> tuple[bool, str]:
    """
    スプシCSV公開URLから取得して、メール+キーの有効性をチェック。
    返り値: (有効か, エラーメッセージ)
    """
    if not csv_url:
        return False, "ライセンスサーバーが未設定です。管理者にお問い合わせください。"

    try:
        req = urllib.request.Request(
            csv_url,
            headers={
                "User-Agent": "AmazonLabelTool/2.1-web",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
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


def reverify_current_license(force_fresh: bool = False) -> tuple[bool, str]:
    """
    セッションに保存されたメール/キーでライセンスを再検証する。
    force_fresh=True の場合はキャッシュを無視して必ずAPIを叩く（処理実行時用）。
    それ以外は VERIFY_CACHE_TTL_SEC 秒間キャッシュを使う（UI描画時用）。
    """
    email = st.session_state.get("user_email", "")
    key = st.session_state.get("license_key", "")
    if not email or not key:
        return False, "ログイン情報が取得できません。再度ログインしてください。"

    now = time.time()
    last_at = st.session_state.get("_verify_last_at", 0.0)
    last_result = st.session_state.get("_verify_last_result")

    if (
        not force_fresh
        and last_result is not None
        and (now - last_at) < VERIFY_CACHE_TTL_SEC
    ):
        return last_result

    result = verify_license_online(email, key, get_license_csv_url())
    st.session_state["_verify_last_at"] = now
    st.session_state["_verify_last_result"] = result
    return result


def _clear_session_on_license_fail():
    """ライセンス失効時／ログアウト時のセッションクリア共通処理。

    ブラウザlocalStorage上の保存データはユーザー別キーで管理しているため
    そのまま残すが、メモリ上の session_state は次のログインに引き継がない
    ように完全初期化する（同一ブラウザで別アカウントにログインし直した
    際に前ユーザーのFNSKU/テキストが見える事故を防ぐ）。
    """
    st.session_state["licensed"] = False
    st.session_state["user_email"] = ""
    st.session_state["license_key"] = ""
    st.session_state.pop("_verify_last_at", None)
    st.session_state.pop("_verify_last_result", None)
    # UI 入力値もすべてリセット
    st.session_state["default_mic"] = True
    st.session_state["default_extra"] = ""
    st.session_state["fnsku_rows"] = []
    st.session_state["_settings_loaded"] = False
    st.session_state["processed_results"] = None


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
    elif mode == MODE_NONE:
        return ""
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
    """
    if not input_bytes:
        raise PDFProcessingError("ファイルが空です。別のPDFをお試しください。")

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

    if getattr(reader, "is_encrypted", False):
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


# ========== 設定変換 ==========

def ui_settings_to_rules(
    default_mic: bool, default_extra: str, fnsku_rows: list[dict]
) -> tuple[dict, str, str]:
    """
    新UIの状態（MIC checkbox + 追加テキスト + 表）を
    既存 process_pdf_bytes が使う (rules, default_mode, default_extra) に変換する。

    - MIC ON + 追加空 → "Made in China" のみ（china_only）
    - MIC ON + 追加あり → "Made in China [追加]"（china_with_extra）
    - MIC OFF + 追加あり → "[追加]" のみ（extra_only）
    - MIC OFF + 追加空 → 何も挿入しない（none）
    """
    def pick_mode(mic: bool, extra: str) -> str:
        extra = (extra or "").strip()
        if mic and extra:
            return MODE_CHINA_WITH_EXTRA
        if mic and not extra:
            return MODE_CHINA_ONLY
        if not mic and extra:
            return MODE_EXTRA_ONLY
        return MODE_NONE

    default_mode = pick_mode(default_mic, default_extra)
    default_extra_clean = (default_extra or "").strip()

    rules = {"rules": {}}
    for row in fnsku_rows or []:
        fnsku = (row.get("fnsku") or "").strip().upper()
        if not fnsku:
            continue
        mic = bool(row.get("mic", True))
        extra = (row.get("extra") or "").strip()
        mode = pick_mode(mic, extra)
        rules["rules"][fnsku] = {"mode": mode, "extra_text": extra}

    return rules, default_mode, default_extra_clean


# ========== 永続化（localStorage） ==========

SETTINGS_KEY_PREFIX = "amazon_label_v2_settings_"


def _settings_key_for(email: str) -> str:
    """メール毎に別名で localStorage に保存"""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", email or "anon")
    return f"{SETTINGS_KEY_PREFIX}{safe}"


def load_settings_from_storage(ls: LocalStorage, email: str) -> dict | None:
    """localStorage から設定を読み込む（無ければ None）"""
    try:
        raw = ls.getItem(_settings_key_for(email))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_settings_to_storage(ls: LocalStorage, email: str, data: dict):
    """localStorage に設定を保存"""
    try:
        ls.setItem(_settings_key_for(email), json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def clear_settings_from_storage(ls: LocalStorage, email: str):
    """localStorage から設定を削除"""
    try:
        ls.deleteItem(_settings_key_for(email))
    except Exception:
        pass


# ========== Streamlit UI ==========

def init_session_state():
    st.session_state.setdefault("licensed", False)
    st.session_state.setdefault("user_email", "")
    st.session_state.setdefault("license_key", "")
    st.session_state.setdefault("default_mic", True)
    st.session_state.setdefault("default_extra", "")
    # fnsku_rows: [{"fnsku": str, "mic": bool, "extra": str}, ...]
    st.session_state.setdefault("fnsku_rows", [])
    st.session_state.setdefault("_settings_loaded", False)
    st.session_state.setdefault("processed_results", None)


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
            st.session_state["license_key"] = license_key.strip()
            st.session_state["_verify_last_at"] = time.time()
            st.session_state["_verify_last_result"] = (True, "")
            # 次のrerunで設定読み込みが走る
            st.session_state["_settings_loaded"] = False
            st.success("ログインに成功しました。")
            st.rerun()
        else:
            st.error(f"ログインに失敗しました：{err}")


def render_sidebar(ls: LocalStorage):
    """サイドバー：ログイン情報とログアウト、バージョン"""
    with st.sidebar:
        st.markdown(f"**バージョン**: {APP_VERSION}")
        st.markdown(f"**ログイン中**: {st.session_state['user_email']}")
        if st.button("ログアウト", use_container_width=True):
            # ログアウト時に現在の設定を保存してから、セッションを破棄する
            # （プロセス終了直前なので rerun による入力ロスの心配がない）
            try:
                _persist_current_settings(ls, silent=True)
            except Exception:
                pass
            _clear_session_on_license_fail()
            st.rerun()

        st.divider()
        st.caption(
            "💾 設定はこのブラウザに自動保存されます。"
            "同じブラウザで再ログインすれば前回の設定が復元されます。"
        )


def render_processor(ls: LocalStorage):
    """PDF処理メイン画面"""
    st.title(f"📦 {APP_NAME}")
    st.caption(
        "「新品」の右横に「Made in China」や任意の文言を一括挿入します。"
        "複数PDFの同時処理にも対応。"
    )

    # ========== ① 挿入設定 ==========
    st.markdown("## ① 挿入設定")

    with st.container(border=True):
        st.markdown("**デフォルト設定**（FNSKU別の個別設定がないラベルに適用されます）")
        col1, col2 = st.columns([1, 2])
        with col1:
            default_mic = st.checkbox(
                '"Made in China" を入れる',
                value=st.session_state["default_mic"],
                key="default_mic_cb",
                help="オンにすると、対象ラベルに「Made in China」が挿入されます",
            )
        with col2:
            default_extra = st.text_input(
                "追加テキスト（任意）",
                value=st.session_state["default_extra"],
                key="default_extra_ti",
                placeholder="例: iPhone15用 / Imported by XYZ 等",
                help='「Made in China」の後ろに続けて挿入されます。空欄なら「Made in China」のみ',
            )
        # session_state に反映
        st.session_state["default_mic"] = default_mic
        st.session_state["default_extra"] = default_extra

        # プレビュー
        _preview = _preview_text(default_mic, default_extra)
        if _preview:
            st.success(f"デフォルトで挿入される文言: **{_preview}**")
        else:
            st.info("デフォルトでは何も挿入されません（チェックも追加テキストも空）")

    with st.container(border=True):
        st.markdown("**FNSKU別の個別設定**（特定のFNSKUだけ別の文言にしたい場合）")
        st.caption(
            "表に FNSKU・チェック・追加テキストを直接入力してください。"
            "行の追加は表の最下段の「+」から、削除は行左端のチェックで選択 → キーボードの Delete で可能です。"
        )
        st.info(
            "💡 **入力後の確定操作について**　"
            "セルに値を入力したら、**必ず「Enterキー」または「Tabキー」を押して確定**してから、"
            "下の「💾 設定を保存」ボタンを押してください。"
            "確定前に保存ボタンを押すと、入力途中の文字が反映されないことがあります。",
            icon="✏️",
        )

        # 表示用にDataFrameを作る
        rows = st.session_state["fnsku_rows"] or []
        if rows:
            df = pd.DataFrame(rows)
        else:
            df = pd.DataFrame({"fnsku": pd.Series(dtype=str), "mic": pd.Series(dtype=bool), "extra": pd.Series(dtype=str)})

        # カラムが欠けている場合の補完（旧データとの互換）
        for col, default in [("fnsku", ""), ("mic", True), ("extra", "")]:
            if col not in df.columns:
                df[col] = default
        df = df[["fnsku", "mic", "extra"]]

        edited = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "fnsku": st.column_config.TextColumn(
                    "FNSKU",
                    required=False,
                    width="medium",
                    help="Amazon FNSKU（X + 英数字9文字）",
                ),
                "mic": st.column_config.CheckboxColumn(
                    '"Made in China" を入れる',
                    default=True,
                    width="small",
                ),
                "extra": st.column_config.TextColumn(
                    "追加テキスト",
                    default="",
                    width="large",
                    help='空欄ならチェック通りの動作（"Made in China"のみ等）',
                ),
            },
            key="fnsku_editor",
        )
        # DataFrame → list of dicts に戻す
        new_rows = edited.to_dict("records")
        # NaN / None を空文字に正規化
        for r in new_rows:
            r["fnsku"] = (r.get("fnsku") or "").strip() if isinstance(r.get("fnsku"), str) else ""
            r["extra"] = (r.get("extra") or "").strip() if isinstance(r.get("extra"), str) else ""
            r["mic"] = bool(r.get("mic")) if r.get("mic") is not None else True
        st.session_state["fnsku_rows"] = new_rows

        # FNSKU別プレビュー
        non_empty_rows = [r for r in new_rows if r["fnsku"]]
        if non_empty_rows:
            with st.expander(f"📋 FNSKU別の挿入内容プレビュー（{len(non_empty_rows)}件）"):
                preview_df = pd.DataFrame([
                    {
                        "FNSKU": r["fnsku"],
                        "挿入される文言": _preview_text(r["mic"], r["extra"]) or "（何も挿入しない）",
                    }
                    for r in non_empty_rows
                ])
                st.dataframe(preview_df, use_container_width=True, hide_index=True)

    # 設定を保存ボタン（明示的な保存のみ。自動保存は行わない）
    # 理由：毎描画ごとに localStorage へ書き込むと rerun が発生し、
    # 表（data_editor）への入力が 1 回で反映されない症状が出るため、
    # 保存は「明示的なタイミング」に限定する。
    col_save, col_info = st.columns([1, 3])
    with col_save:
        if st.button("💾 設定を保存", type="primary", use_container_width=True):
            _persist_current_settings(ls)
            st.toast("設定をブラウザに保存しました。", icon="✅")
    with col_info:
        st.caption(
            "※ 表・チェック・追加テキストを変更したら、"
            "この「💾 設定を保存」ボタンを押してください。"
            "「▶️ PDFを処理する」を押したときと「ログアウト」のときも自動で保存します。"
        )

    # ========== ② PDFアップロード ==========
    st.markdown("## ② PDFをアップロード")
    uploaded_files = st.file_uploader(
        "PDFファイルを選択（複数可・ドラッグ&ドロップOK）",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info("処理したいPDFをドラッグ&ドロップ、または「Browse files」から選択してください。")
        return

    st.markdown(f"**{len(uploaded_files)}件** のPDFがアップロードされました。")

    # ========== ③ 実行 ==========
    st.markdown("## ③ 実行")
    if st.button("▶️ PDFを処理する", type="primary", use_container_width=True):
        # 処理実行時は必ず最新のライセンス状態を取得（キャッシュ無視）
        with st.spinner("ライセンスを確認中..."):
            ok, err = reverify_current_license(force_fresh=True)
        if not ok:
            st.error(f"ライセンスが無効です：{err}")
            st.warning("再度ログインしてください。ログイン画面へ戻ります。")
            _clear_session_on_license_fail()
            time.sleep(2)
            st.rerun()
            return

        # 処理を開始する直前に現在の設定を localStorage に保存しておく。
        # （実行＝設定が確定したタイミングとみなして安全に書き込む）
        _persist_current_settings(ls, silent=True)

        rules, default_mode, default_extra_clean = ui_settings_to_rules(
            st.session_state["default_mic"],
            st.session_state["default_extra"],
            st.session_state["fnsku_rows"],
        )
        process_uploaded_files(uploaded_files, rules, default_mode, default_extra_clean)

    # 前回処理結果があれば表示（rerunで消えないように session_state に保存）
    if st.session_state.get("processed_results"):
        render_results(st.session_state["processed_results"])


def _preview_text(mic: bool, extra: str) -> str:
    """UI設定から実際に挿入される文言のプレビューを返す"""
    extra = (extra or "").strip()
    if mic and extra:
        return f"Made in China {extra}"
    if mic and not extra:
        return "Made in China"
    if not mic and extra:
        return extra
    return ""


def _persist_current_settings(ls: LocalStorage, silent: bool = False):
    """現在の UI state を localStorage に保存"""
    data = {
        "default_mic": bool(st.session_state.get("default_mic", True)),
        "default_extra": st.session_state.get("default_extra", "") or "",
        "fnsku_rows": st.session_state.get("fnsku_rows", []) or [],
        "saved_at": datetime.now().isoformat(),
        "version": APP_VERSION,
    }
    save_settings_to_storage(ls, st.session_state["user_email"], data)
    if not silent:
        return True
    return True


def _load_settings_if_needed(ls: LocalStorage):
    """ログイン直後に一度だけ localStorage から設定を復元。

    新しいユーザーに保存設定が存在しない場合は、必ずデフォルト値に
    リセットする（前ユーザーのデータが session_state に残ったまま
    表示される事故を防ぐため）。
    """
    if st.session_state.get("_settings_loaded"):
        return
    email = st.session_state.get("user_email", "")
    if not email:
        return
    data = load_settings_from_storage(ls, email)
    if data:
        st.session_state["default_mic"] = bool(data.get("default_mic", True))
        st.session_state["default_extra"] = data.get("default_extra", "") or ""
        rows = data.get("fnsku_rows") or []
        # 正規化
        clean_rows = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            clean_rows.append({
                "fnsku": str(r.get("fnsku") or "").strip(),
                "mic": bool(r.get("mic", True)),
                "extra": str(r.get("extra") or "").strip(),
            })
        st.session_state["fnsku_rows"] = clean_rows
    else:
        # この email には保存設定なし → デフォルト値で初期化
        # （前ユーザーの fnsku_rows がメモリ上に残っているケースを防ぐ）
        st.session_state["default_mic"] = True
        st.session_state["default_extra"] = ""
        st.session_state["fnsku_rows"] = []
    st.session_state["_settings_loaded"] = True


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
            results.append({
                "name": uploaded.name, "output": None, "pages": 0,
                "labels": 0, "fnskus": set(), "error": str(e),
            })
            with log_area:
                st.error(f"❌ {uploaded.name} — {e}")
        except Exception as e:
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

    # set → list に変換して保存（setはJSONに載らないため）
    saveable_results = []
    for r in results:
        r_copy = dict(r)
        r_copy["fnskus"] = sorted(list(r["fnskus"])) if r["fnskus"] else []
        saveable_results.append(r_copy)
    st.session_state["processed_results"] = saveable_results
    # NOTE: render_results はこの直後に main() 側の「前回処理結果」ブロックで呼ばれる。
    # ここで呼ぶと同じ download_button を二重に描画して DuplicateWidgetID になるため、
    # 意図的に呼び出さない。


def render_results(results):
    """処理結果のダウンロードUI"""
    # ダウンロード表示前に再検証（処理後にライセンス停止された場合を検知）
    ok, err = reverify_current_license(force_fresh=False)
    if not ok:
        st.error(f"ライセンスが無効化されたため、ダウンロードを停止します：{err}")
        st.session_state["processed_results"] = None
        _clear_session_on_license_fail()
        time.sleep(2)
        st.rerun()
        return

    success_results = [r for r in results if r["output"] is not None]
    if not success_results:
        st.warning("正常に処理できたファイルはありませんでした。")
        return

    st.markdown("## ④ ダウンロード")

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
            key=f"dl_single_{out_name}",
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
        key=f"dl_zip_{timestamp}",
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
        layout="wide",
    )

    # Streamlit Cloud の右上ツールバー（Share/Fork/GitHub/⋮）を非表示にする。
    # これらはアプリ開発者の GitHub プロフィール等に遷移するボタンで、
    # 業務利用のエンドユーザーには不要なため隠す。
    st.markdown(
        """
        <style>
        /* Streamlit Cloud が埋め込む外側ツールバー（Share, Fork, GitHub, ⋮ など）を
           「見えなくする」が、レイアウトの場所は確保しておく。
           display: none にすると stToolbar の子要素であるサイドバー再表示ボタン
           （stExpandSidebarButton）まで巻き込んで描画されなくなるため、
           visibility: hidden で個別子要素だけを再表示する戦略を取る。*/
        [data-testid="stToolbar"] {
            visibility: hidden !important;
        }
        /* サイドバー折りたたみ時の「»」（再表示）ボタンだけは見えるように戻す。*/
        [data-testid="stToolbar"] [data-testid="stExpandSidebarButton"],
        [data-testid="stToolbar"] [data-testid="stExpandSidebarButton"] * {
            visibility: visible !important;
        }

        [data-testid="stDecoration"] { display: none !important; }
        [data-testid="stStatusWidget"] { display: none !important; }
        .stDeployButton { display: none !important; }
        .viewerBadge_container__r5tak { display: none !important; }
        a[href*="streamlit.io/cloud"] { display: none !important; }
        a[href*="share.streamlit.io"] { display: none !important; }
        #MainMenu { visibility: hidden !important; }
        footer { visibility: hidden !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    init_session_state()

    # LocalStorage インスタンス（毎回生成、コストは低い）
    ls = LocalStorage()

    if not st.session_state["licensed"]:
        render_login()
        return

    # 【重要】メインビュー描画時のライセンス再検証は行わない。
    # ここで毎レンダー reverify_current_license を呼ぶと、キャッシュTTL切れのたびに
    # Google Sheets への HTTP fetch が走り、network I/O 中の postMessage が
    # Streamlit の rerun を誘発する。結果、data_editor で入力中の文字が消える
    # 現象が発生していた。
    # ライセンス再検証は「PDFを処理するボタン押下時」「ダウンロード時」に限定する
    # （当初仕様通り）。ログイン中に管理者が無効化した場合は、次の処理実行時に弾かれる。

    # 設定の初回ロード
    _load_settings_if_needed(ls)

    render_sidebar(ls)
    render_processor(ls)


if __name__ == "__main__":
    main()
