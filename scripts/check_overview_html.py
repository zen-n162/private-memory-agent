#!/usr/bin/env python3

"""Validate the self-contained Japanese overview HTML."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERVIEW = ROOT / "docs" / "overview_ja.html"
REQUIRED_SNIPPETS = (
    '<html lang="ja">',
    "Private Memory Agent",
    "このアプリでできること",
    "データソース",
    "AI エージェント構成",
    "処理の流れ",
    "モデルの役割",
    "証拠に基づく回答ポリシー",
    "プライバシーとセキュリティ方針",
    "回答例",
    "開発ロードマップ",
    "開発者向けコマンド",
    "制限事項",
    "更新履歴",
)
FORBIDDEN_SNIPPETS = (
    "TODO",
    "cdn.jsdelivr",
    "unpkg.com",
    "fonts.googleapis.com",
    "<script",
)


def main() -> int:
    if not OVERVIEW.exists():
        print("overview_ja.html is missing")
        return 1

    text = OVERVIEW.read_text(encoding="utf-8")
    missing = [snippet for snippet in REQUIRED_SNIPPETS if snippet not in text]
    forbidden = [snippet for snippet in FORBIDDEN_SNIPPETS if snippet in text]

    if missing:
        print("overview_ja.html is missing required snippets:")
        for snippet in missing:
            print(f"- {snippet}")
        return 1

    if forbidden:
        print("overview_ja.html contains forbidden snippets:")
        for snippet in forbidden:
            print(f"- {snippet}")
        return 1

    print("overview_ja.html check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
