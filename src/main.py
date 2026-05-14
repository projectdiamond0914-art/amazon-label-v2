#!/usr/bin/env python3
"""
Amazon商品ラベル FNSKU別テキスト挿入ツール v2.0

機能:
- PDF内の「新品」テキスト右横にカスタムテキストを挿入
- FNSKUごとに3モードを切替可能:
  1. Made in China のみ
  2. Made in China + 任意の追加文言
  3. 任意文言のみ（Made in China は入れない）
- ライセンスキー制（Googleスプレッドシート連携）
"""

import os
import re
import sys
import json
import uuid
import csv
import urllib.request
import urllib.error
from io import BytesIO
from pathlib import Path
from tkinter import (
    Tk, Toplevel, Frame, Label, Entry, Button, Text, StringVar,
    filedialog, messagebox, ttk, END, LEFT, RIGHT, BOTH, X, Y, BOTTOM, TOP,
    W, E, N, S, NSEW
)
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

# ========== 設定 ==========
APP_NAME = "Amazon商品ラベル FNSKU別テキスト挿入ツール"
APP_VERSION = "2.0.0"
FONT_NAME = "Helvetica"
FONT_SIZE = 6
RIGHT_OFFSET_PT = 5

# データ保存先（exe/スクリプトと同じディレクトリ）
def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR = _get_base_dir()
LICENSE_FILE = BASE_DIR / "license.json"
RULES_FILE = BASE_DIR / "fnsku_rules.json"
CONFIG_FILE = BASE_DIR / "config.json"

# FNSKU: X + 9文字の英数字（Amazon標準）
FNSKU_PATTERN = re.compile(r'\bX[A-Z0-9]{9}\b')

# モード定義
MODE_CHINA_ONLY = "china_only"
MODE_CHINA_WITH_EXTRA = "china_with_extra"
MODE_EXTRA_ONLY = "extra_only"

MODE_LABELS = {
    MODE_CHINA_ONLY: "Made in China のみ",
    MODE_CHINA_WITH_EXTRA: "Made in China + 追加文言",
    MODE_EXTRA_ONLY: "追加文言のみ",
}


# ========== 設定ファイル操作 ==========

def load_config() -> dict:
    """アプリ設定読み込み（スプシURL等）"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "license_csv_url": "",  # スプシのCSV公開URL（管理者が設定）
        "default_mode": MODE_CHINA_ONLY,
        "default_extra_text": "",
    }


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_rules() -> dict:
    """FNSKU別ルール読み込み"""
    if RULES_FILE.exists():
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"rules": {}}


def save_rules(rules: dict) -> None:
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def load_license() -> dict:
    if LICENSE_FILE.exists():
        try:
            with open(LICENSE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_license(data: dict) -> None:
    with open(LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ========== ライセンス検証 ==========

def verify_license_online(email: str, license_key: str, csv_url: str) -> tuple[bool, str]:
    """
    スプシCSV公開URLから取得して、メール+キーの有効性をチェック。
    返り値: (有効か, エラーメッセージ)
    """
    if not csv_url:
        return False, "ライセンスサーバー（スプシURL）が未設定です。管理者にお問い合わせください。"

    try:
        req = urllib.request.Request(csv_url, headers={"User-Agent": "AmazonLabelTool/2.0"})
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

    # ヘッダー解析（日本語/英語対応）
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

    return False, "メールアドレスが登録されていません。まず登録フォームから登録してください。"


# ========== PDF処理 ==========

def find_labels_in_page(page) -> list[dict]:
    """
    ページ内のラベルを検出して、各ラベルのFNSKUと「新品」座標を対応付ける。
    返り値: [{"fnsku": str, "shinpin_pos": (x, y, font_size)}, ...]
    """
    fnsku_positions: list[tuple[str, float, float]] = []
    shinpin_positions: list[tuple[float, float, float]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        if not text:
            return
        x, y = tm[4], tm[5]

        # FNSKU検出
        match = FNSKU_PATTERN.search(text)
        if match:
            fnsku_positions.append((match.group(), x, y))

        # 「新品」検出
        if "新品" in text:
            shinpin_positions.append((x, y, font_size))

    page.extract_text(visitor_text=visitor)

    # 各「新品」位置に対応するFNSKUを割り当てる。
    #
    # v2.0.1 修正（2026-05-15）:
    #   従来は (x, y) のユークリッド距離で「最も近いFNSKU」を選んでいたが、
    #   最下行のラベルにおいて、真上のFNSKUより右隣の列のFNSKUの方が距離が
    #   近くなる場合があり、隣列のテキストが誤って割り当てられる不具合があった。
    #   ラベルはグリッドに整列している前提で、まず「同じ行」のFNSKUに絞り、
    #   その中でX座標が最も近いものを選ぶ実装に変更。
    labels = []
    for sx, sy, sfs in shinpin_positions:
        # 「同じ行」と見なすY方向の許容差（文字サイズ × 4）
        row_tolerance = max(sfs * 4, 12.0)

        # 1) まずは同じ行のFNSKU候補に絞る
        same_row = [
            (fnsku, fx, fy)
            for fnsku, fx, fy in fnsku_positions
            if abs(fy - sy) <= row_tolerance
        ]

        closest_fnsku = None
        if same_row:
            # 同じ行の中で X 距離が最小のものを選ぶ
            closest_fnsku = min(same_row, key=lambda t: abs(t[1] - sx))[0]
        else:
            # フォールバック: 同じ行が見つからない場合は従来通り最近傍
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


def get_text_for_fnsku(fnsku: str | None, rules: dict, default_mode: str, default_extra: str) -> str:
    """FNSKUに応じて挿入するテキストを決定"""
    rule = None
    if fnsku and fnsku in rules.get("rules", {}):
        rule = rules["rules"][fnsku]

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


def create_overlay(width, height, label_items, font_name, font_size, right_offset) -> BytesIO:
    """複数ラベルのテキストをオーバーレイ生成"""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont(font_name, font_size)
    c.setFillColorRGB(0, 0, 0)

    for item in label_items:
        if not item.get("text"):
            continue
        sx, sy, sfs = item["shinpin_pos"]
        text_x = sx + (sfs * 2) + right_offset
        text_y = sy
        c.drawString(text_x, text_y, item["text"])

    c.save()
    buf.seek(0)
    return buf


def process_pdf(input_path: str, output_path: str, rules: dict, default_mode: str, default_extra: str) -> tuple[int, int, set[str]]:
    """PDFを処理。返り値: (ページ数, ラベル数, 検出FNSKU一覧)"""
    reader = PdfReader(input_path)
    writer = PdfWriter()

    total_labels = 0
    detected_fnskus: set[str] = set()

    for page in reader.pages:
        media_box = page.mediabox
        width = float(media_box.width)
        height = float(media_box.height)

        labels = find_labels_in_page(page)
        total_labels += len(labels)

        label_items = []
        for lbl in labels:
            if lbl["fnsku"]:
                detected_fnskus.add(lbl["fnsku"])
            text = get_text_for_fnsku(lbl["fnsku"], rules, default_mode, default_extra)
            label_items.append({
                "shinpin_pos": lbl["shinpin_pos"],
                "text": text,
            })

        if label_items:
            overlay_buf = create_overlay(
                width, height, label_items,
                FONT_NAME, FONT_SIZE, RIGHT_OFFSET_PT
            )
            overlay_reader = PdfReader(overlay_buf)
            overlay_page = overlay_reader.pages[0]
            page.merge_page(overlay_page)

        writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return len(reader.pages), total_labels, detected_fnskus


# ========== GUI: ライセンス登録画面 ==========

class LicenseDialog:
    """初回起動時のライセンス登録画面"""

    def __init__(self, parent, config: dict):
        self.config = config
        self.result: dict | None = None

        self.dialog = Toplevel(parent)
        self.dialog.title(f"{APP_NAME} - ライセンス認証")
        self.dialog.geometry("560x460")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_ui()
        self._center()

    def _center(self):
        self.dialog.update_idletasks()
        w = self.dialog.winfo_width()
        h = self.dialog.winfo_height()
        sw = self.dialog.winfo_screenwidth()
        sh = self.dialog.winfo_screenheight()
        self.dialog.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

    def _build_ui(self):
        pad = {"padx": 16, "pady": 8}

        Label(self.dialog, text="ライセンス認証", font=("", 16, "bold")).pack(pady=(16, 4))
        Label(
            self.dialog,
            text="本ツールのご利用には、メールアドレスとライセンスキーが必要です。",
            fg="#555",
        ).pack(pady=(0, 12))

        # 案内
        info = Frame(self.dialog, bg="#f5f5f5", bd=1, relief="solid")
        info.pack(fill=X, padx=16, pady=8)
        Label(
            info,
            text="① まだ登録していない方は、登録フォームから氏名・電話・メールをご入力ください。\n"
                 "② 登録後、ライセンスキーが記載されたメールが届きます。\n"
                 "③ 届いたメールアドレスとライセンスキーを以下に入力してください。",
            bg="#f5f5f5",
            justify=LEFT,
            anchor=W,
        ).pack(padx=10, pady=8, fill=X)

        # 入力欄
        form = Frame(self.dialog)
        form.pack(fill=X, padx=16, pady=8)

        Label(form, text="メールアドレス:").grid(row=0, column=0, sticky=W, pady=4)
        self.email_var = StringVar()
        Entry(form, textvariable=self.email_var, width=45).grid(row=0, column=1, sticky=W, pady=4)

        Label(form, text="ライセンスキー:").grid(row=1, column=0, sticky=W, pady=4)
        self.key_var = StringVar()
        Entry(form, textvariable=self.key_var, width=45).grid(row=1, column=1, sticky=W, pady=4)

        # ボタン
        btn_frame = Frame(self.dialog)
        btn_frame.pack(fill=X, padx=16, pady=(8, 16))

        Button(
            btn_frame,
            text="登録フォームを開く",
            command=self._open_form,
            width=18,
        ).pack(side=LEFT, padx=4)
        Button(
            btn_frame,
            text="認証する",
            command=self._verify,
            width=14,
            bg="#2196F3",
            fg="white",
        ).pack(side=RIGHT, padx=4)
        Button(
            btn_frame,
            text="キャンセル",
            command=self.dialog.destroy,
            width=12,
        ).pack(side=RIGHT, padx=4)

    def _open_form(self):
        import webbrowser
        url = self.config.get("registration_form_url", "")
        if not url:
            messagebox.showwarning(
                "登録フォーム未設定",
                "登録フォームURLが設定されていません。管理者にお問い合わせください。",
            )
            return
        webbrowser.open(url)

    def _verify(self):
        email = self.email_var.get().strip()
        key = self.key_var.get().strip()
        if not email or not key:
            messagebox.showerror("入力エラー", "メールアドレスとライセンスキーを入力してください。")
            return

        ok, err = verify_license_online(email, key, self.config.get("license_csv_url", ""))
        if ok:
            self.result = {"email": email, "license_key": key}
            messagebox.showinfo("認証成功", "ライセンス認証に成功しました。")
            self.dialog.destroy()
        else:
            messagebox.showerror("認証エラー", err)


# ========== GUI: FNSKUルール編集画面 ==========

class RulesEditor:
    """FNSKUごとのルール編集画面"""

    def __init__(self, parent, rules: dict, config: dict, detected_fnskus: set[str] | None = None):
        self.rules = rules
        self.config = config
        self.detected_fnskus = detected_fnskus or set()

        self.dialog = Toplevel(parent)
        self.dialog.title("FNSKU別ルール設定")
        self.dialog.geometry("760x540")
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        # デフォルト設定
        top = Frame(self.dialog, bd=1, relief="solid")
        top.pack(fill=X, padx=10, pady=10)

        Label(top, text="デフォルト設定（FNSKUごとに指定しないラベルに適用）", font=("", 11, "bold")).pack(anchor=W, padx=10, pady=(8, 4))

        def_frame = Frame(top)
        def_frame.pack(fill=X, padx=10, pady=(0, 10))

        Label(def_frame, text="モード:").grid(row=0, column=0, sticky=W, pady=4)
        self.default_mode_var = StringVar(value=self.config.get("default_mode", MODE_CHINA_ONLY))
        mode_cb = ttk.Combobox(
            def_frame,
            textvariable=self.default_mode_var,
            values=list(MODE_LABELS.values()),
            state="readonly",
            width=30,
        )
        mode_cb.grid(row=0, column=1, sticky=W, padx=8)
        mode_cb.set(MODE_LABELS.get(self.default_mode_var.get(), MODE_LABELS[MODE_CHINA_ONLY]))

        Label(def_frame, text="追加文言:").grid(row=1, column=0, sticky=W, pady=4)
        self.default_extra_var = StringVar(value=self.config.get("default_extra_text", ""))
        Entry(def_frame, textvariable=self.default_extra_var, width=40).grid(row=1, column=1, sticky=W, padx=8, pady=4)

        # FNSKU別リスト
        mid = Frame(self.dialog)
        mid.pack(fill=BOTH, expand=True, padx=10, pady=5)

        Label(mid, text="FNSKU別ルール（個別設定したいラベル）", font=("", 11, "bold")).pack(anchor=W, pady=(4, 4))

        tree_frame = Frame(mid)
        tree_frame.pack(fill=BOTH, expand=True)

        columns = ("fnsku", "mode", "extra")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        self.tree.heading("fnsku", text="FNSKU")
        self.tree.heading("mode", text="モード")
        self.tree.heading("extra", text="追加文言")
        self.tree.column("fnsku", width=140)
        self.tree.column("mode", width=200)
        self.tree.column("extra", width=320)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)

        # 追加/編集エリア
        edit = Frame(self.dialog, bd=1, relief="solid")
        edit.pack(fill=X, padx=10, pady=10)

        Label(edit, text="ルール追加／編集", font=("", 10, "bold")).pack(anchor=W, padx=10, pady=(6, 4))

        ef = Frame(edit)
        ef.pack(fill=X, padx=10, pady=(0, 8))

        Label(ef, text="FNSKU:").grid(row=0, column=0, sticky=W, pady=3)
        self.e_fnsku = StringVar()
        fnsku_combo_values = sorted(self.detected_fnskus)
        self.fnsku_entry = ttk.Combobox(ef, textvariable=self.e_fnsku, values=fnsku_combo_values, width=18)
        self.fnsku_entry.grid(row=0, column=1, sticky=W, padx=6)

        Label(ef, text="モード:").grid(row=0, column=2, sticky=W, pady=3, padx=(16, 0))
        self.e_mode = StringVar(value=MODE_LABELS[MODE_CHINA_ONLY])
        ttk.Combobox(
            ef,
            textvariable=self.e_mode,
            values=list(MODE_LABELS.values()),
            state="readonly",
            width=26,
        ).grid(row=0, column=3, sticky=W, padx=6)

        Label(ef, text="追加文言:").grid(row=1, column=0, sticky=W, pady=3)
        self.e_extra = StringVar()
        Entry(ef, textvariable=self.e_extra, width=60).grid(row=1, column=1, columnspan=3, sticky=W, padx=6, pady=3)

        btn = Frame(edit)
        btn.pack(fill=X, padx=10, pady=(0, 8))
        Button(btn, text="追加／更新", command=self._add_or_update, width=14).pack(side=LEFT, padx=4)
        Button(btn, text="選択項目を編集欄へ", command=self._load_selected, width=18).pack(side=LEFT, padx=4)
        Button(btn, text="選択項目を削除", command=self._delete_selected, width=14).pack(side=LEFT, padx=4)

        # 下部ボタン
        bottom = Frame(self.dialog)
        bottom.pack(fill=X, padx=10, pady=(0, 12))
        Button(bottom, text="保存して閉じる", command=self._save_close, width=16, bg="#2196F3", fg="white").pack(side=RIGHT, padx=4)
        Button(bottom, text="キャンセル", command=self.dialog.destroy, width=12).pack(side=RIGHT, padx=4)

    def _mode_label_to_key(self, label: str) -> str:
        for k, v in MODE_LABELS.items():
            if v == label:
                return k
        return MODE_CHINA_ONLY

    def _refresh_list(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for fnsku, rule in self.rules.get("rules", {}).items():
            self.tree.insert(
                "", END,
                values=(
                    fnsku,
                    MODE_LABELS.get(rule.get("mode", MODE_CHINA_ONLY), "不明"),
                    rule.get("extra_text", ""),
                ),
            )

    def _add_or_update(self):
        fnsku = self.e_fnsku.get().strip().upper()
        if not fnsku:
            messagebox.showerror("エラー", "FNSKUを入力してください。")
            return
        mode = self._mode_label_to_key(self.e_mode.get())
        extra = self.e_extra.get().strip()
        if mode != MODE_CHINA_ONLY and not extra and mode == MODE_CHINA_WITH_EXTRA:
            messagebox.showwarning("確認", "『Made in China + 追加文言』モードですが追加文言が空です。Made in China のみ挿入されます。")
        if mode == MODE_EXTRA_ONLY and not extra:
            messagebox.showerror("エラー", "『追加文言のみ』モードには追加文言が必須です。")
            return

        self.rules.setdefault("rules", {})[fnsku] = {
            "mode": mode,
            "extra_text": extra,
        }
        self._refresh_list()
        self.e_fnsku.set("")
        self.e_extra.set("")

    def _load_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0])["values"]
        self.e_fnsku.set(values[0])
        self.e_mode.set(values[1])
        self.e_extra.set(values[2])

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0])["values"]
        fnsku = values[0]
        if messagebox.askyesno("確認", f"FNSKU「{fnsku}」のルールを削除しますか？"):
            self.rules.get("rules", {}).pop(fnsku, None)
            self._refresh_list()

    def _save_close(self):
        self.config["default_mode"] = self._mode_label_to_key(self.default_mode_var.get())
        self.config["default_extra_text"] = self.default_extra_var.get().strip()
        save_config(self.config)
        save_rules(self.rules)
        messagebox.showinfo("保存完了", "ルールを保存しました。")
        self.dialog.destroy()


# ========== GUI: メイン画面 ==========

class MainApp:
    def __init__(self, root: Tk, license_info: dict):
        self.root = root
        self.license_info = license_info
        self.config = load_config()
        self.rules = load_rules()
        self.detected_fnskus: set[str] = set()

        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("720x520")

        self._build_ui()

    def _build_ui(self):
        # ヘッダー
        header = Frame(self.root, bg="#2196F3", height=60)
        header.pack(fill=X)
        header.pack_propagate(False)
        Label(
            header,
            text=APP_NAME,
            bg="#2196F3",
            fg="white",
            font=("", 14, "bold"),
        ).pack(side=LEFT, padx=16, pady=14)
        Label(
            header,
            text=f"v{APP_VERSION}  |  {self.license_info.get('email','')}",
            bg="#2196F3",
            fg="#e0e0e0",
            font=("", 9),
        ).pack(side=RIGHT, padx=16, pady=14)

        # メイン領域
        main = Frame(self.root)
        main.pack(fill=BOTH, expand=True, padx=16, pady=12)

        # 現在のデフォルト設定表示
        info_box = Frame(main, bd=1, relief="solid", bg="#fafafa")
        info_box.pack(fill=X, pady=(0, 10))

        Label(info_box, text="現在のデフォルト設定", font=("", 11, "bold"), bg="#fafafa").pack(anchor=W, padx=10, pady=(8, 4))

        self.default_info_label = Label(info_box, text="", bg="#fafafa", justify=LEFT, anchor=W)
        self.default_info_label.pack(fill=X, padx=10, pady=(0, 8))
        self._update_info_label()

        # ボタン群
        btn_frame = Frame(main)
        btn_frame.pack(fill=X, pady=8)

        Button(
            btn_frame,
            text="① FNSKU別ルールを設定する",
            command=self._open_rules_editor,
            width=28,
            height=2,
            bg="#4CAF50",
            fg="white",
        ).pack(pady=6)

        Button(
            btn_frame,
            text="② PDFを選択して処理する",
            command=self._process_pdfs,
            width=28,
            height=2,
            bg="#FF9800",
            fg="white",
        ).pack(pady=6)

        # ログ領域
        log_frame = Frame(main, bd=1, relief="solid")
        log_frame.pack(fill=BOTH, expand=True, pady=(10, 0))
        Label(log_frame, text="処理ログ", font=("", 10, "bold")).pack(anchor=W, padx=8, pady=4)
        self.log_text = Text(log_frame, height=12, wrap="word", font=("", 9))
        self.log_text.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))
        self._log("起動しました。まずは「FNSKU別ルール設定」で処理内容を決めてください。\n")

    def _update_info_label(self):
        mode_label = MODE_LABELS.get(self.config.get("default_mode", MODE_CHINA_ONLY), "不明")
        extra = self.config.get("default_extra_text", "")
        rule_count = len(self.rules.get("rules", {}))
        text = (
            f"デフォルトモード: {mode_label}\n"
            f"デフォルト追加文言: {extra or '(なし)'}\n"
            f"個別ルール数: {rule_count} 件"
        )
        self.default_info_label.config(text=text)

    def _log(self, msg: str):
        self.log_text.insert(END, msg)
        self.log_text.see(END)
        self.root.update_idletasks()

    def _open_rules_editor(self):
        # 既にPDF処理を1回していれば、検出FNSKU一覧をコンボボックスに渡す
        editor = RulesEditor(self.root, self.rules, self.config, self.detected_fnskus)
        self.root.wait_window(editor.dialog)
        # 再読込
        self.config = load_config()
        self.rules = load_rules()
        self._update_info_label()

    def _process_pdfs(self):
        # 再検証（停止されていないか確認）
        csv_url = self.config.get("license_csv_url", "")
        if csv_url:
            ok, err = verify_license_online(
                self.license_info["email"],
                self.license_info["license_key"],
                csv_url,
            )
            if not ok:
                messagebox.showerror("ライセンスエラー", err)
                return

        file_paths = filedialog.askopenfilenames(
            title="処理するPDFファイルを選択（複数選択可）",
            filetypes=[("PDFファイル", "*.pdf"), ("すべて", "*.*")],
        )
        if not file_paths:
            return

        self._log(f"\n{'='*50}\n")
        self._log(f"処理開始: {len(file_paths)} ファイル\n")
        self._log(f"{'='*50}\n")

        default_mode = self.config.get("default_mode", MODE_CHINA_ONLY)
        default_extra = self.config.get("default_extra_text", "")

        success = 0
        for file_path in file_paths:
            folder = os.path.dirname(file_path)
            filename = os.path.basename(file_path)
            name, ext = os.path.splitext(filename)
            output_name = f"{name}_labeled{ext}"
            output_path = os.path.join(folder, output_name)

            try:
                self._log(f"処理中: {filename}\n")
                pages, labels, fnskus = process_pdf(
                    file_path, output_path,
                    self.rules, default_mode, default_extra,
                )
                self.detected_fnskus.update(fnskus)
                self._log(f"  ✅ 完了 ({pages}ページ / {labels}ラベル)\n")
                if fnskus:
                    self._log(f"  検出FNSKU: {', '.join(sorted(fnskus))}\n")
                self._log(f"  → {output_name}\n\n")
                success += 1
            except Exception as e:
                self._log(f"  ❌ エラー: {e}\n\n")

        self._log(f"{'='*50}\n")
        self._log(f"完了: {success}/{len(file_paths)} ファイル\n\n")

        if success > 0:
            messagebox.showinfo(
                "完了",
                f"{success}件のPDFを処理しました。\n\n"
                f"元のPDFと同じフォルダに「○○_labeled.pdf」として保存されています。",
            )


# ========== エントリーポイント ==========

def main():
    root = Tk()
    root.withdraw()

    config = load_config()
    license_info = load_license()

    # ライセンス認証が必要
    if not license_info.get("email") or not license_info.get("license_key"):
        dlg = LicenseDialog(root, config)
        root.wait_window(dlg.dialog)
        if not dlg.result:
            root.destroy()
            sys.exit(0)
        license_info = dlg.result
        save_license(license_info)
    else:
        # 既存ライセンスの再検証
        csv_url = config.get("license_csv_url", "")
        if csv_url:
            ok, err = verify_license_online(
                license_info["email"],
                license_info["license_key"],
                csv_url,
            )
            if not ok:
                messagebox.showerror("ライセンスエラー", f"{err}\n\n再度認証してください。")
                # 既存ライセンス削除して再認証
                if LICENSE_FILE.exists():
                    LICENSE_FILE.unlink()
                dlg = LicenseDialog(root, config)
                root.wait_window(dlg.dialog)
                if not dlg.result:
                    root.destroy()
                    sys.exit(0)
                license_info = dlg.result
                save_license(license_info)

    root.deiconify()
    app = MainApp(root, license_info)
    root.mainloop()


if __name__ == "__main__":
    main()
