"""
緊急用 CLI: 修正版ラベルマッチングで PDF を処理する。
ライセンスチェック等は飛ばし、入力PDFと設定だけで実行する。

使い方:
    python3 process_cli.py <input.pdf> <output.pdf>
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# main.py の関数を直接呼ぶ
from main import process_pdf  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    input_pdf = Path(sys.argv[1])
    output_pdf = Path(sys.argv[2])
    if not input_pdf.exists():
        print(f"[ERR] 入力PDFが見つかりません: {input_pdf}")
        return 1

    # 設定ロード: config.production.json があればそれを優先
    cfg_path = ROOT / "config.production.json"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.sample.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    rules = cfg.get("rules", cfg) if isinstance(cfg, dict) else {"rules": {}}
    default_mode = cfg.get("default_mode", "china_only")
    default_extra = cfg.get("default_extra", "")

    print(f"[INFO] 入力: {input_pdf}")
    print(f"[INFO] 出力: {output_pdf}")
    print(f"[INFO] default_mode={default_mode!r}, default_extra={default_extra!r}")

    pages, labels, fnskus = process_pdf(
        str(input_pdf), str(output_pdf),
        rules if isinstance(rules, dict) and "rules" in rules else {"rules": rules},
        default_mode, default_extra,
    )
    print(f"[OK] {pages}ページ / {labels}ラベル処理 / 検出FNSKU={len(fnskus)}")
    print(f"[OK] 出力PDF: {output_pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
