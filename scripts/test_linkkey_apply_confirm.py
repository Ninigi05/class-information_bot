# -*- coding: utf-8 -*-
"""
フェーズ1「連携キーの適用前確認」のテスト

対象: main.py に追加する次の部品（Discord 本体なしで検証できる形にする）
  - _build_apply_diff_lines(old, new, payload) -> list[str]
      変更がなければ空リスト。Gmail コード付き payload では警告行を必ず含める。
  - _user_data_digest(data) -> str
      同じ内容なら同じ値、内容が変われば違う値。
  - _stash_pending_link_apply(user_id, payload, base_digest) -> token (str)
    _get_pending_link_apply(user_id) -> dict | None（期限切れは None）
    _discard_pending_link_apply(user_id) -> None
    _pending_apply_is_fresh(pending, current_data) -> bool
  - _save_apply_backup(user_id, data, base_dir=None) -> ファイル名
    _restore_apply_backup(user_id, filename, base_dir=None) -> dict | None
      バックアップは1ユーザー直近3世代まで。

実行: docker exec class-information-bot python scripts/test_linkkey_apply_confirm.py
終了コード: 全件期待どおりなら 0、そうでなければ 1
実ユーザーのファイルには触らない（バックアップ系は一時ディレクトリで実行）。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as bot_main

results = []


def check(case: str, ok: bool, detail: str = ""):
    results.append(ok)
    print(f"[{'期待どおり' if ok else 'NG'}] {case}" + (f": {detail}" if detail else ""))


def base_data() -> dict:
    return {
        "classes_by_term": {
            "前期": [
                {"day": 0, "period": "1", "subject": "前期の授業", "room": "A101", "has_exam": True},
            ],
            "後期": [
                {"day": 0, "period": "2", "subject": "放射化学", "room": "17-201", "has_exam": True},
            ],
        },
        "exam_schedules_by_term": {
            "前期": [{"name": "前期試験", "start": "2026-07-20", "end": "2026-07-25", "classes": []}],
            "後期": [{"name": "後期試験", "start": "2027-01-20", "end": "2027-01-25", "classes": []}],
        },
        "class_attendance_count": {"前期": {"0|1|前期の授業": 5}, "後期": {"0|2|放射化学": 7}},
        "class_count_targets": {"後期": 15},
        "notify_settings": {"normal": {"first": 15, "second": 10}, "exam": {"first": 30, "second": 25}},
        "morning_notice_time": "08:00",
        "period_overrides": {},
        "exam_period_overrides": {},
        "day_overrides": [],
        "room_overrides": [],
        "term_start_dates": {},
        "term_ranges": {},
    }


# --- T1: 差分（授業の追加・削除・教室変更が分かる） ---
old = base_data()
new = base_data()
new["classes_by_term"]["後期"] = [
    {"day": 0, "period": "2", "subject": "放射化学", "room": "17-302", "has_exam": True},  # 教室変更
    {"day": 2, "period": "3", "subject": "新しい授業", "room": "C303", "has_exam": True},  # 追加
]
new["classes_by_term"]["前期"] = []  # 削除
lines = bot_main._build_apply_diff_lines(old, new, {"_meta": {"feature": "all", "term": "後期"}})
joined = "\n".join(lines)
check("T1 授業の差分が出る", bool(lines) and "後期" in joined and "新しい授業" in joined, f"{len(lines)}行")
check("T1 教室変更が分かる", "17-302" in joined)
check("T1 前期の削除が分かる", "前期" in joined)

# --- T2: 差分（通知変更と出席カウントのリセット警告） ---
old = base_data()
new = base_data()
new["morning_notice_time"] = "07:50"
lines = bot_main._build_apply_diff_lines(old, new, {"_meta": {"feature": "notify", "term": "後期"}})
check("T2 通知時刻の変更が出る", any("07:50" in ln for ln in lines), " / ".join(lines))

new2 = base_data()
new2["class_attendance_count"]["後期"] = {}
lines2 = bot_main._build_apply_diff_lines(old, new2, {"_meta": {"feature": "notify", "term": "後期"}})
check(
    "T2 出席カウントがリセットされる場合は警告が出る",
    any("出席カウント" in ln for ln in lines2),
    " / ".join(lines2),
)

# --- T3: 差分（Gmail コード付きは警告行） ---
lines = bot_main._build_apply_diff_lines(
    base_data(), base_data(), {"gmail_auth_code": "dummy-code", "_meta": {"feature": "gmail", "term": "後期"}}
)
check("T3 Gmail付きキーは警告行が出る", any("Gmail" in ln for ln in lines), " / ".join(lines))

# --- T4: 変更なしなら差分は空 ---
lines = bot_main._build_apply_diff_lines(base_data(), base_data(), {"_meta": {"feature": "all", "term": "後期"}})
check("T4 変更なしなら差分は空", lines == [], f"{lines}")

# --- T5: digest ---
d1 = bot_main._user_data_digest(base_data())
d2 = bot_main._user_data_digest(base_data())
changed = base_data()
changed["morning_notice_time"] = "07:00"
d3 = bot_main._user_data_digest(changed)
check("T5 同じ内容なら同じdigest", d1 == d2)
check("T5 内容が変わればdigestも変わる", d1 != d3)

# --- T6: 保留（stash / get / discard / 期限切れ / 鮮度） ---
uid = 999999001
bot_main._discard_pending_link_apply(uid)
token = bot_main._stash_pending_link_apply(uid, {"_meta": {"feature": "all"}}, d1)
pending = bot_main._get_pending_link_apply(uid)
check("T6 保留を保存して取り出せる", bool(pending) and pending.get("token") == token)
check("T6 鮮度チェック（同じデータなら fresh）", bot_main._pending_apply_is_fresh(pending, base_data()) is True)
check("T6 鮮度チェック（変わっていたら not fresh）", bot_main._pending_apply_is_fresh(pending, changed) is False)
pending["created_monotonic"] = time.monotonic() - 10000  # 期限切れを装う
check("T6 期限切れの保留は取り出せない", bot_main._get_pending_link_apply(uid) is None)
token2 = bot_main._stash_pending_link_apply(uid, {"_meta": {"feature": "all"}}, d1)
bot_main._discard_pending_link_apply(uid)
check("T6 破棄した保留は取り出せない", bot_main._get_pending_link_apply(uid) is None)

# --- T7: バックアップ（保存・復元・世代管理） ---
with tempfile.TemporaryDirectory() as tmp:
    uid2 = 999999002
    name1 = bot_main._save_apply_backup(uid2, base_data(), base_dir=tmp)
    check("T7 バックアップが作られる", bool(name1) and os.path.exists(os.path.join(tmp, "backups", name1)))
    restored = bot_main._restore_apply_backup(uid2, name1, base_dir=tmp)
    check("T7 バックアップから復元できる", restored is not None and restored.get("morning_notice_time") == "08:00")
    for i in range(3):
        data_i = base_data()
        data_i["morning_notice_time"] = f"07:0{i}"
        bot_main._save_apply_backup(uid2, data_i, base_dir=tmp)
        time.sleep(0.02)
    files = [f for f in os.listdir(os.path.join(tmp, "backups")) if f.startswith(f"user_{uid2}_")]
    check("T7 バックアップは直近3世代まで", len(files) == 3, f"{len(files)}件")
    check(
        "T7 他人のバックアップは復元できない",
        bot_main._restore_apply_backup(999999003, name1, base_dir=tmp) is None,
    )

print()
failed = results.count(False)
print(f"結果: {len(results)}件中 {failed}件NG / {len(results) - failed}件は期待どおり")
sys.exit(1 if failed else 0)
