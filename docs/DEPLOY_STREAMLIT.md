# Streamlit Cloud デプロイ手順（加藤氏向け）

対象リポジトリ: https://github.com/projectdiamond0914-art/amazon-label-v2
デプロイ対象ファイル: `app.py`
所要時間: **5〜10分**

---

## 前提

- GitHubへの push は完了済み（コミット `f2dff60`）
- リポジトリが **private の場合**、Streamlit Cloud に GitHub OAuth で「privateリポジトリへのアクセス」を許可する必要がある
- メル太郎様のライセンスキーは Googleスプレッドシートに既に登録済み（`有効=TRUE`）なので、追加の発行作業は不要

---

## 手順

### ステップ1: Streamlit Cloud にサインイン

1. https://share.streamlit.io/ を開く
2. 「Sign in with GitHub」→ GitHubアカウント（`projectdiamond0914-art`）でログイン
3. 初回のみ: 「Authorize Streamlit」でアクセス許可
   - **private リポジトリをデプロイする場合は「All repositories」を選択**（public ならデフォルトでOK）

### ステップ2: 新規アプリ作成

1. 右上の「Create app」→「Deploy a public app from GitHub」
2. 以下を入力：
   - **Repository**: `projectdiamond0914-art/amazon-label-v2`
   - **Branch**: `main`
   - **Main file path**: `app.py`
   - **App URL (optional)**: 例 `amazon-label-granreve`（= `https://amazon-label-granreve.streamlit.app` になる）
     → メル太郎様に渡すURLなので、覚えやすい名前に
3. 「Advanced settings」を展開 →「Secrets」欄に以下を貼り付け：

   ```toml
   LICENSE_CSV_URL = "https://docs.google.com/spreadsheets/d/1bLl13Hzeygikzs-yGiLqFV1L6Ne27F3QGB3rsoy6A7U/export?format=csv&gid=1754325196"
   ```

   > Secretsを設定しなくても `app.py` 内の `DEFAULT_LICENSE_CSV_URL` が使われるので動作はするが、
   > CSV URLを変えたい時のためにSecretsで管理しておくのが推奨。

4. 「Deploy」ボタンを押す

### ステップ3: デプロイ完了を待つ

- 初回は 3〜5分 ほどかかる（依存関係インストール）
- ログが流れ終わって「Your app is live」になればOK
- エラーになったら、ログをスクショして貼ってください（解析します）

### ステップ4: 動作確認

以下のURLでメル太郎様用のテストをする：

```
https://amazon-label-granreve.streamlit.app （例）
```

1. ログイン画面で以下を入力：
   - メール: `<MELTARO_EMAIL_REDACTED>`
   - ライセンスキー: `<MELTARO_KEY_REDACTED>`
2. 「ログイン」→ 処理画面に遷移
3. サンプルPDF（`sample_data/` にある、または手持ちのAmazonラベルPDF）をアップロード
4. 「PDFを処理する」→ 完了後「ダウンロード」で結果PDFを取得
5. 結果PDFで「新品」の右横に「Made in China」が入っていることを確認

---

## 注意事項

- Streamlit Cloud の **無料プラン**：
  - 個人アカウントで1GBメモリ・月間使用時間制限なし・public appのみ3つまで無料
  - メル太郎様1社なら十分
- アプリは **数時間アクセスがないとスリープ**する（再アクセスで30秒程度で復帰）
- `app.py` を GitHub に push すると **自動で再デプロイ**される

## メル太郎様側でやることは？

**インストールは一切不要**。URLを開くだけ。
- 推奨: ブックマーク保存
- ログインは最初に1回だけ（ブラウザを閉じない限りセッション維持）

---

## トラブル時の切り分け

| 症状 | 対処 |
|------|------|
| デプロイ失敗（`ModuleNotFoundError`） | `requirements.txt` に不足パッケージがないか確認 |
| ログインできない | スプシのライセンス行が `有効=TRUE` になっているか確認 |
| PDFアップロードで固まる | 50MB超のPDFは `.streamlit/config.toml` の `maxUploadSize` を上げる |
| メル太郎様の環境でボタンが反応しない | ブラウザのスーパーリロード（Cmd+Shift+R / Ctrl+F5） |

---

## ローカル実行（開発時）

```bash
cd ~/Desktop/amazon-label-v2
source .venv/bin/activate
streamlit run app.py
# → http://localhost:8501 で開く
```

テスト:
```bash
python tests/test_pipeline.py
```
