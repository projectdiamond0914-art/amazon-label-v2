# Amazon商品ラベル FNSKU別テキスト挿入ツール v2.0

Amazon FBA商品ラベル（PDF）に「Made in China」や任意のテキストをFNSKU別に自動挿入するWindows用ツール。

---

## 主な機能

- **PDF内の「新品」文字右横** にカスタムテキストを自動挿入
- **FNSKUごとに3モードを切替**:
  1. `Made in China` のみ入れる
  2. `Made in China` ＋ 任意の追加文言
  3. 追加文言のみ（Made in China は入れない）
- **ライセンスキー制**：初回登録後にキー発行、管理者側で利用停止/再開可能
- **複数PDF一括処理**：元のPDFは残したまま `○○_labeled.pdf` として保存

---

## セットアップ（エンドユーザー向け）

### 1. zipを解凍
`AmazonLabelTool_v2.0.zip` をデスクトップに解凍します。

### 2. ライセンスキーを取得
下記の登録フォームにアクセスし、氏名・電話番号・メールアドレスを入力してください。
入力されたメールアドレス宛にライセンスキーが自動で送信されます。

> 登録フォームURL: **（販売元から案内）**

### 3. ツールを起動
解凍したフォルダ内の `AmazonLabelTool.exe` をダブルクリック。

### 4. ライセンス認証
初回起動時にライセンス認証画面が表示されます。
- メールアドレス（登録時と同じもの）
- ライセンスキー（メールで届いたもの）
を入力して「認証」をクリック。

### 5. 使い方
1. メイン画面で「PDFを選択して処理」をクリック
2. 処理したいPDFを選択（複数選択可）
3. 元のPDFと同じフォルダに `○○_labeled.pdf` として保存されます

### FNSKU別ルール設定（任意）
「FNSKU別ルール設定」ボタンから、FNSKUごとに挿入テキストを個別設定できます。
未設定のFNSKUは「既定モード」に従います。

---

## ディレクトリ構成

```
amazon-label-v2/
├── src/
│   └── main.py                  # メインスクリプト
├── gas/
│   └── license_manager.gs       # Google Apps Script（ライセンス自動発行）
├── .github/
│   └── workflows/
│       └── build-windows-exe.yml  # Windows exe ビルド用
├── docs/
│   └── operation_manual.md      # 販売元向け運用手順書
├── requirements.txt
├── config.sample.json
└── README.md
```

---

## 開発者向け

### ローカル実行（macOS / Linux / Windows）

```bash
# 仮想環境作成
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 依存インストール
pip install -r requirements.txt

# 実行
python src/main.py
```

### Windows exe ビルド

GitHub Actions を使って自動ビルド：

1. このリポジトリをGitHubにプッシュ
2. `Actions` タブから `Build Windows EXE` を手動実行（`workflow_dispatch`）
3. 完了後、`Artifacts` から `AmazonLabelTool-Windows.zip` をダウンロード

---

## ライセンス管理（販売元向け）

詳細は `docs/operation_manual.md` を参照してください。

### 利用停止/再開
1. Googleスプレッドシートを開く
2. 該当ユーザーの「有効」列のチェックボックスを切替
   - ✅ → 利用可能
   - ☐ → 停止（ツール起動不可）

---

## 設定ファイル

`config.json`（ツールと同じフォルダに配置）

| キー | 説明 |
|------|------|
| `license_csv_url` | GoogleスプレッドシートのCSV公開URL |
| `default_mode` | 既定モード（`china_only` / `china_with_extra` / `extra_only`） |
| `default_extra_text` | 既定の追加文言 |

---

## バージョン

- **v2.0.0** (2026-04-19) - 初回リリース（FNSKU別3モード + ライセンスキー制）
