"""
ラベル位置マッチング v2.0.1 リグレッションテスト
最下行で隣列のFNSKUに誤マッチしないことを保証する。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from main import find_labels_in_page  # type: ignore  # noqa: E402


class _StubPage:
    """page.extract_text(visitor_text=visitor) を再現するスタブ。"""

    def __init__(self, items):
        self._items = items

    def extract_text(self, visitor_text=None):
        for text, x, y, fs in self._items:
            cm = None
            # tm[4]=x, tm[5]=y を満たすダミー行列
            tm = [1, 0, 0, 1, x, y]
            visitor_text(text, cm, tm, None, fs)
        return ""


def _build_grid_items(cols, rows, fnskus, *, shinpin_offset_y=-20, fs=10):
    items = []
    for ri, ry in enumerate(rows):
        for ci, cx in enumerate(cols):
            items.append((fnskus[ci], cx, ry + 15, fs))  # FNSKU上部
            items.append(("新品", cx, ry + shinpin_offset_y, fs))  # 新品下部
    return items


def test_4x10_grid_no_lastrow_offset():
    """10行×4列のグリッドで、最下行も含めて全列が正しくマッチする"""
    fnskus = ["X001BKHD03", "X001B09B49", "X001B09EU5", "X001BKJFZT"]
    cols = [100, 220, 340, 460]
    # 行間 80pt（縦方向）
    rows = list(range(900, 100, -80))

    items = _build_grid_items(cols, rows, fnskus)
    page = _StubPage(items)
    labels = find_labels_in_page(page)

    assert len(labels) == 40, f"40ラベル想定 / 実際 {len(labels)}"

    # 全ラベルがその列の FNSKU と一致しているか
    # ラベル順序は新品が発見された順 = 行優先 × 列優先で並ぶ
    for idx, label in enumerate(labels):
        expected = fnskus[idx % 4]
        assert label["fnsku"] == expected, (
            f"ラベル#{idx} 期待={expected} / 実際={label['fnsku']}"
        )


def test_4x10_grid_tight_rows_no_offset():
    """行間が詰まったレイアウトでも最下行が誤マッチしない"""
    fnskus = ["X000000001", "X000000002", "X000000003", "X000000004"]
    cols = [80, 180, 280, 380]
    rows = list(range(900, 100, -60))  # 行間 60pt（やや詰まり）

    items = _build_grid_items(cols, rows, fnskus, shinpin_offset_y=-12)
    page = _StubPage(items)
    labels = find_labels_in_page(page)

    for idx, label in enumerate(labels):
        expected = fnskus[idx % 4]
        assert label["fnsku"] == expected, (
            f"tight_rows: ラベル#{idx} 期待={expected} / 実際={label['fnsku']}"
        )


def test_single_row_3cols():
    """1行3列でも崩れない"""
    fnskus = ["X0A0A0A0A0", "X0B0B0B0B0", "X0C0C0C0C0"]
    cols = [50, 150, 250]
    rows = [400]

    items = _build_grid_items(cols, rows, fnskus)
    page = _StubPage(items)
    labels = find_labels_in_page(page)

    assert [lab["fnsku"] for lab in labels] == fnskus
