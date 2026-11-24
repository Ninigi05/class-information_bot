# -*- coding: utf-8 -*-
"""
フェーズ3a「休講メールの送信者確認」のテスト
対象:
  - 検索クエリに「補講」が含まれる
  - 送信者アドレスの取り出し（表示名に惑わされない）
  - 公式送信者の判定（許可リスト3アドレス、完全一致・大文字小文字不問）
  - DM表示: 送信者が出る / リスト外には警告が出る
実行: docker exec class-information-bot python scripts/test_phase3a.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = []

def check(case, ok, detail=""):
    results.append(ok)
    print(f"[{'期待どおり' if ok else 'NG'}] {case}" + (f": {detail}" if detail else ""))

import mail_service

check("検索クエリに補講が含まれる", "補講" in mail_service.MAIL_QUERY, mail_service.MAIL_QUERY)

addr, display = mail_service.parse_sender('"近畿大学メール配信専用（返信不可）" <UNIPA_send_only@itp.kindai.ac.jp>')
check("送信者アドレスを取り出せる", addr == "unipa_send_only@itp.kindai.ac.jp", addr)
check("送信者の表示名を取り出せる", "近畿大学" in display, display)

check("公式: UNIPA配信専用", mail_service.is_official_sender('"近畿大学メール配信専用（返信不可）" <UNIPA_send_only@itp.kindai.ac.jp>'))
check("公式: Classroom通知", mail_service.is_official_sender('"武村祐一朗 さん（Classroom）" <no-reply@classroom.google.com>'))
check("公式: KDU Mobile", mail_service.is_official_sender("KDU Mobile <no-reply@kdu.mobi>"))
check("公式: アドレスだけの形式", mail_service.is_official_sender("UNIPA_send_only@itp.kindai.ac.jp"))
check("公式: 大文字小文字は不問", mail_service.is_official_sender("No-Reply@Classroom.Google.COM"))
check("非公式: 個人のGmail", not mail_service.is_official_sender("someone@gmail.com"))
check("非公式: 表示名で公式を名乗る偽装", not mail_service.is_official_sender('"UNIPA_send_only@itp.kindai.ac.jp" <evil@example.com>'))
check("非公式: 似たドメイン", not mail_service.is_official_sender("unipa_send_only@itp.kindai.ac.jp.evil.com"))
check("非公式: 空文字", not mail_service.is_official_sender(""))

# DM表示（mail_cog の整形）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cogs"))
import importlib.util
spec = importlib.util.spec_from_file_location("mail_cog_mod", os.path.join("cogs", "mail_cog.py"))
mail_cog_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail_cog_mod)

official_item = {
    "type": "cancellation", "date": "2026-11-27", "period": "2", "subject": "放射化学",
    "sender": "近畿大学メール配信専用（返信不可） <UNIPA_send_only@itp.kindai.ac.jp>",
    "is_official_sender": True,
}
unofficial_item = {
    "type": "cancellation", "date": "2026-11-28", "period": "1", "subject": "ナゾの授業",
    "sender": "someone@gmail.com", "is_official_sender": False,
}
lines = mail_cog_mod._format_results([official_item])
joined = "\n".join(lines)
check("DMに送信者が表示される", "送信者" in joined and "UNIPA_send_only" in joined, joined.replace("\n", " / "))
check("公式なら警告は出ない", "公式リスト外" not in joined)

lines = mail_cog_mod._format_results([unofficial_item])
joined = "\n".join(lines)
check("リスト外なら警告が出る", "公式リスト外" in joined, joined.replace("\n", " / "))

legacy_item = {"type": "cancellation", "date": "2026-11-29", "period": "3", "subject": "旧形式"}
lines = mail_cog_mod._format_results([legacy_item])
check("送信者情報がない旧形式でも壊れない", len(lines) >= 1)

print()
failed = results.count(False)
print(f"結果: {len(results)}件中 {failed}件NG / {len(results) - failed}件は期待どおり")
sys.exit(1 if failed else 0)
