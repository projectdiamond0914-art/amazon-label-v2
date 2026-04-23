"""
Playwrightによる完全E2Eテスト。

localhost:8765 で起動中のStreamlitアプリに対して：
1. ログイン画面表示を確認
2. メル太郎様の実キーでログイン
3. ダッシュボード（ログイン後UI）表示確認
4. PDFファイルをアップロード
5. 処理ボタンクリック
6. ダウンロードボタンが現れることを確認

起動は別プロセス（tests/_run_streamlit.sh）で行う前提。
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright, expect

URL = "http://localhost:8765"
SAMPLE_PDF = Path(__file__).resolve().parent.parent / "sample_data" / "realistic_amazon_labels.pdf"
# 実キーは環境変数で注入（~/amazon-label-v2-secrets/meltaro_credentials.txt 参照）
# 例: TEST_EMAIL=xxx@example.com TEST_KEY=XXXX-XXXX python tests/test_e2e_playwright.py
TEST_EMAIL = os.environ.get("TEST_EMAIL", "example@example.com")
TEST_KEY = os.environ.get("TEST_KEY", "XXXXXXXX-XXXX-XXXX-XXXX")


def run_e2e():
    assert SAMPLE_PDF.exists(), f"sample PDF missing: {SAMPLE_PDF}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        print(f"[1] Navigating to {URL}")
        page.goto(URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)  # Streamlit初回レンダリング待ち

        print("[2] Checking login screen visible")
        page.wait_for_selector("text=ログイン", timeout=15000)
        print("    ✅ Login screen rendered")

        # スクリーンショット保存（デバッグ用）
        page.screenshot(path="sample_data/e2e_01_login.png")

        print(f"[3] Filling credentials: {TEST_EMAIL} / [redacted]")
        # メール入力
        email_input = page.get_by_placeholder("example@example.com")
        email_input.fill(TEST_EMAIL)
        # キー入力
        key_input = page.get_by_placeholder("XXXXXXXX-XXXX-XXXX-XXXX")
        key_input.fill(TEST_KEY)

        print("[4] Clicking ログイン button")
        page.get_by_role("button", name="ログイン").click()

        print("[5] Waiting for login success and processor screen")
        # 成功メッセージまたは処理画面の見出し
        page.wait_for_selector(
            "text=Amazon商品ラベル FNSKU別テキスト挿入ツール",
            timeout=20000
        )
        page.wait_for_selector("text=PDFをアップロード", timeout=10000)
        print("    ✅ Processor screen rendered")
        page.screenshot(path="sample_data/e2e_02_after_login.png")

        print(f"[6] Uploading sample PDF ({SAMPLE_PDF.name})")
        file_input = page.locator('input[type="file"]')
        file_input.set_input_files(str(SAMPLE_PDF))

        # アップロード反映待ち
        page.wait_for_selector("text=1件", timeout=10000)
        time.sleep(1)
        print("    ✅ File uploaded")
        page.screenshot(path="sample_data/e2e_03_after_upload.png")

        print("[7] Clicking 処理する button")
        process_btn = page.get_by_role("button", name="▶️ PDFを処理する")
        process_btn.click()

        print("[8] Waiting for processing completion")
        # 完了メッセージ（ラベル60件）が表示されるのを待つ
        page.wait_for_selector("text=60ラベル", timeout=30000)
        print("    ✅ Processing completed")
        page.screenshot(path="sample_data/e2e_04_after_process.png")

        print("[9] Verifying download button appears")
        import re
        # ダウンロードボタン（単一ファイルなので⬇️ 付き）
        download_btn = page.get_by_role("button", name=re.compile("ダウンロード")).first
        expect(download_btn).to_be_visible(timeout=10000)
        print("    ✅ Download button visible")

        print("[10] Triggering download")
        with page.expect_download(timeout=15000) as dl_info:
            download_btn.click()
        download = dl_info.value
        saved_path = Path("sample_data") / f"e2e_downloaded_{download.suggested_filename}"
        download.save_as(str(saved_path))
        size = saved_path.stat().st_size
        print(f"    ✅ Downloaded: {saved_path.name} ({size:,} bytes)")
        assert size > 10000, f"downloaded file too small: {size} bytes"

        # ダウンロードPDFの中身も確認
        from pypdf import PdfReader
        from io import BytesIO
        reader = PdfReader(str(saved_path))
        all_text = "\n".join(p.extract_text() or "" for p in reader.pages)
        mic_count = all_text.count("Made in China")
        print(f"    ✅ Downloaded PDF contains {mic_count} 'Made in China' (expected 60)")
        assert mic_count == 60, f"expected 60, got {mic_count}"

        print("[11] Testing logout")
        logout_btn = page.get_by_role("button", name="ログアウト")
        logout_btn.click()
        time.sleep(2)
        page.wait_for_selector("text=🔐 ログイン", timeout=10000)
        print("    ✅ Returned to login screen after logout")
        page.screenshot(path="sample_data/e2e_05_after_logout.png")

        browser.close()
        print("\n" + "=" * 60)
        print("E2E TEST PASSED — 全フロー成功")
        print("=" * 60)


if __name__ == "__main__":
    run_e2e()
