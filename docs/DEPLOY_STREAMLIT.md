# Streamlit Cloud デプロイ手順（加藤氏向け・確定版）

対象リポジトリ: https://github.com/projectdiamond0914-art/amazon-label-v2 （**PRIVATE**）
デプロイ対象ファイル: `app.py`
所要時間: **5〜10分**

---

## 前提確認（事前チェック）

| 確認項目 | コマンド | 期待値 |
|---------|----------|--------|
| GitHub CLI認証 | `gh auth status` | `Logged in to github.com account projectdiamond0914-art` |
| リポジトリ可視性 | `gh repo view projectdiamond0914-art/amazon-label-v2 --json visibility` | `"visibility":"PRIVATE"` |
| 最新コミット | `git log --oneline -1` | 最新のStreamlit関連コミット |
| ローカル動作 | `source .venv/bin/activate && python tests/test_edge_cases.py` | 全10件合格 |
| E2E動作 | `source .venv/bin/activate && python tests/test_e2e_playwright.py` | 11ステップ成功 |

---

## 手順

### ステップ1: Streamlit Community Cloud にサインイン

1. https://share.streamlit.io/ を開く
2. 「**Continue with GitHub**」→ GitHubアカウント（`projectdiamond0914-art`）でログイン
3. 初回のみ: 「Authorize Streamlit」画面
   - **⚠️ private リポジトリへのアクセス許可が必須**
   - 「All repositories」または「Only select repositories → amazon-label-v2」を選択
   - デフォルトの「Only public repositories」のままだと**デプロイできない**

### ステップ2: 新規アプリ作成

1. 右上の「**Create app**」→「**Deploy a public app from GitHub**」
2. 以下を入力：
   - **Repository**: `projectdiamond0914-art/amazon-label-v2`
     → 表示されない場合はステップ1のOAuth設定を再確認
   - **Branch**: `main`
   - **Main file path**: `app.py`
   - **App URL (optional)**: 例 `amazon-label-granreve`
     → `https://amazon-label-granreve.streamlit.app` になる
3. 「**Advanced settings**」を展開：
   - **Python version**: **3.12** を選択（Streamlit Cloud側のdropdown）
   - **Secrets** 欄に以下を貼り付け（省略時も動くが推奨）：
     ```toml
     LICENSE_CSV_URL = "https://docs.google.com/spreadsheets/d/1bLl13Hzeygikzs-yGiLqFV1L6Ne27F3QGB3rsoy6A7U/export?format=csv&gid=1754325196"
     ```
4. 「**Deploy**」ボタンを押す

### ステップ3: デプロイ完了を待つ

- 初回は 3〜5分（依存関係インストール）
- ログが流れ終わって「Your app is live!」が出ればOK
- エラー時は「Manage app」→「Logs」でスタックトレースを確認 → スクショで共有

### ステップ4: 加藤氏自身での動作確認（**納品前必須**）

**下記を加藤氏自身の手で1回実行してから、メル太郎様に納品する**。

1. デプロイURLにアクセス
2. ログイン画面が表示されることを確認
3. 以下でログイン：
   - メール: ＜メル太郎様の既存メール（secrets/meltaro_credentials.txt 参照）＞
   - キー: ＜メル太郎様の既存キー（同上）＞
4. 処理画面が表示されたら、以下のサンプルPDFをアップロード：
   - `sample_data/realistic_amazon_labels.pdf`（既にGitHubに含まれる）
   - または加藤氏手持ちのAmazon実ラベルPDF
5. 「PDFを処理する」→ 処理完了
6. ダウンロードしたPDFをAdobe Acrobat等で開き、**「新品」の右横に「Made in China」が挿入されていること**を目視確認
7. 加藤氏手持ちの実PDFでも同じように動くことを確認できれば**本番運用OK**

---

## トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| Deploy画面でリポジトリが出ない | private repo のOAuth権限不足 | https://github.com/settings/installations → Streamlit → Configure → Repository access に `amazon-label-v2` を追加 |
| `ModuleNotFoundError: No module named 'xxx'` | `requirements.txt` 不足 | ローカルで`pip freeze \| grep xxx`、足りなければPR |
| Python 3.12以外が選択された | 無指定時のデフォルト変動 | Advanced settingsで明示的に3.12を選択し直しRedeploy |
| ログインできない | スプシの`有効`列が`FALSE` | Googleスプレッドシート側で`TRUE`に更新 |
| 「このPDFはPDF形式ではない」 | 非PDFファイルをアップ | 正しいPDFを選び直す（このエラーは**想定内挙動**） |
| 処理後PDFに何も挿入されていない | 「新品」テキストが画像化されている | 画像PDFはOCR未実装のため対象外 |
| 動かなくなった（謎） | Streamlit Cloudのクォータ | 「Reboot app」（Manage app画面）で解消することが多い |

---

## 自動再デプロイ

- `main` ブランチに push されると**自動で再デプロイ**が走る
- デプロイ中は旧バージョンが動き続けるのでダウンタイムなし
- `secrets.toml` を変えた場合のみ Streamlit Cloud 側で手動Reboot

---

## ローカル開発

```bash
cd ~/Desktop/amazon-label-v2
source .venv/bin/activate
streamlit run app.py
# → http://localhost:8501
```

テスト:
```bash
python tests/test_pipeline.py        # コアロジック 3件
python tests/test_edge_cases.py      # エッジケース 10件
python tests/test_e2e_playwright.py  # E2E（別ターミナルでstreamlit起動中）
```

## プライベートリポジトリを保ったままの安全性

- コード内には機密情報なし（`LICENSE_CSV_URL` は「ウェブに公開」されたGoogle Sheetsなので公開URL）
- `docs/` にはメル太郎様のメール・キーが記載されている → **だからprivateのまま保持**
- 将来publicにする場合は: 履歴からクレデンシャル除去（`git filter-repo`）→ visibility変更
- 現状privateのまま運用で**セキュリティ・運用性ともに最適解**
