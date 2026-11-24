# -*- coding: utf-8 -*-
"""
連携キー取り込みのデータ消失バグ 再現テスト

対象: main.py の _apply_web_payload_to_user_data
実行: bot コンテナ内でのみ動かす（実ユーザーのファイルは読み書きしない。
      すべてメモリ上のダミーデータに対して適用関数を呼ぶだけ）

  docker exec class-information-bot python scripts/repro_linkkey_data_loss.py

判定の読み方:
  - 「期待どおり」= 修正後の正しい挙動になっている
  - 「BUG再現」   = データ消失バグが存在する（修正前は A / A2 / B / C がこれになるはず）
  - D / E は対照テスト（今でも期待どおりに動くはずの、意図された挙動）
終了コード: バグが1件でも再現したら 1、全部期待どおりなら 0
"""
import copy
import os
import sys

# scripts/ から実行しても /app（main.py のある場所）を import できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import _apply_web_payload_to_user_data

TERM1 = "前期"
TERM2 = "後期"


def base_data() -> dict:
    """ダミーユーザーデータ。前期・後期で中身を変えて、混ざったら分かるようにする。"""
    return {
        "classes_by_term": {
            TERM1: [
                {"day": 0, "period": "1", "subject": "前期の授業", "room": "A101", "has_exam": True},
            ],
            TERM2: [
                {"day": 0, "period": "2", "subject": "放射化学", "room": "17-201", "has_exam": True},
                {"day": 3, "period": "5", "subject": "水素エネルギー工学", "room": "B202", "has_exam": True},
            ],
        },
        "exam_schedules_by_term": {
            TERM1: [
                {
                    "name": "前期試験",
                    "start": "2026-07-20",
                    "end": "2026-07-25",
                    "classes": [
                        {"day": 0, "period": "2", "time": None, "subject": "前期の授業", "room": "A101"},
                    ],
                },
            ],
            TERM2: [
                {
                    "name": "後期試験",
                    "start": "2027-01-20",
                    "end": "2027-01-25",
                    "classes": [
                        {"day": 0, "period": "2", "time": None, "subject": "放射化学", "room": "17-201"},
                    ],
                },
            ],
        },
        # 出席カウント: {"曜日|時限|科目": 回数}
        "class_attendance_count": {
            TERM1: {"0|1|前期の授業": 5},
            TERM2: {"0|2|放射化学": 7, "3|5|水素エネルギー工学": 8},
        },
        # 授業回数の目標（学期ごとに1つの数値）
        "class_count_targets": {TERM2: 15},
        "notify_settings": {
            "normal": {"first": 15, "second": 10},
            "exam": {"first": 30, "second": 25},
        },
        "morning_notice_time": "08:00",
        "period_overrides": {},
        "exam_period_overrides": {},
        "day_overrides": [],
        "room_overrides": [],
        "term_start_dates": {},
        "term_ranges": {},
    }


def notify_payload(class_count_targets) -> dict:
    """Web の _build_feature_payload(feature='notify') と同じ形。
    notify キーには class_count_targets が常に含まれるのがポイント。"""
    return {
        "_meta": {"feature": "notify", "term": TERM2},
        "notify": {"normal_first": 20, "normal_second": 10, "morning_time": "08:00"},
        "period_overrides": {},
        "term_start_dates": {},
        "term_ranges": {},
        "class_count_targets": class_count_targets,
    }


results = []


def check(case: str, ok: bool, detail: str):
    results.append(ok)
    mark = "期待どおり" if ok else "BUG再現"
    print(f"[{mark}] {case}: {detail}")


# --- A: 通知設定キー（class_count_targets は空）で出席カウントが消えるか ---
data = _apply_web_payload_to_user_data(base_data(), notify_payload({}))
att = data["class_attendance_count"][TERM2]
check(
    "A 通知キーを適用しても出席カウントは残る",
    att.get("0|2|放射化学") == 7 and att.get("3|5|水素エネルギー工学") == 8,
    f"適用後の後期カウント = {att}（適用前は 放射化学7・水素エネルギー工学8）",
)

# --- A2: 通知設定キー（class_count_targets は今と同値）でも消えるか ---
data = _apply_web_payload_to_user_data(base_data(), notify_payload({TERM2: 15}))
att = data["class_attendance_count"][TERM2]
check(
    "A2 目標が同値なら出席カウントは残る",
    att.get("0|2|放射化学") == 7,
    f"適用後の後期カウント = {att}（目標は 15 のまま変えていない）",
)

# --- B: 後期の all キーで前期の試験時間割が上書きされるか ---
base = base_data()
payload_all_exam = {
    "_meta": {"feature": "all", "term": TERM2},
    # all キーの試験時間割は学期区別のない単一リスト（後期分だけが入っている）
    "exam_schedules": copy.deepcopy(base["exam_schedules_by_term"][TERM2]),
    # 授業は学期別形式で両学期とも現状維持にして、試験だけを切り分けて見る
    "classes_by_term": {
        TERM1: [{"weekday": "月曜日", "period": "1", "subject": "前期の授業", "room": "A101"}],
        TERM2: [
            {"weekday": "月曜日", "period": "2", "subject": "放射化学", "room": "17-201"},
            {"weekday": "木曜日", "period": "5", "subject": "水素エネルギー工学", "room": "B202"},
        ],
    },
}
data = _apply_web_payload_to_user_data(base, payload_all_exam)
names1 = [s["name"] for s in data["exam_schedules_by_term"][TERM1]]
names2 = [s["name"] for s in data["exam_schedules_by_term"][TERM2]]
check(
    "B 後期のallキーで前期の試験は残る",
    names1 == ["前期試験"] and names2 == ["後期試験"],
    f"適用後の試験名 前期={names1} 後期={names2}",
)

# --- C: 後期の all キー（授業が単一リスト形式）で前期の授業が上書きされるか ---
base = base_data()
payload_all_classes = {
    "_meta": {"feature": "all", "term": TERM2},
    # classes_by_term が無い形。後期の新しい授業だけが単一リストで入っている
    "classes": [
        {"weekday": "火曜日", "period": "3", "subject": "新しい後期の授業", "room": "C303"},
    ],
    # 試験は学期別形式で現状維持にして、授業だけを切り分けて見る
    "exam_schedules_by_term": {
        TERM1: [
            {
                "name": "前期試験",
                "start": "2026-07-20",
                "end": "2026-07-25",
                "classes": [
                    {"weekday": "月曜日", "period": "2", "subject": "前期の授業", "room": "A101"},
                ],
            },
        ],
        TERM2: [
            {
                "name": "後期試験",
                "start": "2027-01-20",
                "end": "2027-01-25",
                "classes": [
                    {"weekday": "月曜日", "period": "2", "subject": "放射化学", "room": "17-201"},
                ],
            },
        ],
    },
}
data = _apply_web_payload_to_user_data(base, payload_all_classes)
subs1 = [c["subject"] for c in data["classes_by_term"][TERM1]]
subs2 = [c["subject"] for c in data["classes_by_term"][TERM2]]
check(
    "C 後期のallキーで前期の授業は残る",
    subs1 == ["前期の授業"] and subs2 == ["新しい後期の授業"],
    f"適用後の授業 前期={subs1} 後期={subs2}",
)

# --- D（対照）: 目標回数を本当に変えたら、その学期のカウントはリセットされてよい ---
data = _apply_web_payload_to_user_data(base_data(), notify_payload({TERM2: 10}))
att2 = data["class_attendance_count"][TERM2]
att1 = data["class_attendance_count"][TERM1]
check(
    "D 目標を15→10に変えたら後期はリセット・前期は無傷（意図された挙動）",
    att2 == {} and att1.get("0|1|前期の授業") == 5,
    f"適用後のカウント 前期={att1} 後期={att2}",
)

# --- E（対照）: exam キー（後期指定）は後期の試験だけを変える ---
base = base_data()
payload_exam = {
    "_meta": {"feature": "exam", "term": TERM2},
    "exam_schedules": [
        {
            "name": "後期試験・改訂",
            "start": "2027-01-20",
            "end": "2027-01-26",
            "classes": [
                {"weekday": "月曜日", "period": "2", "subject": "放射化学", "room": "17-201"},
            ],
        },
    ],
    "exam_period_overrides": {},
    "notify": {"exam_first": 30, "exam_second": 25},
}
data = _apply_web_payload_to_user_data(base, payload_exam)
names1 = [s["name"] for s in data["exam_schedules_by_term"][TERM1]]
names2 = [s["name"] for s in data["exam_schedules_by_term"][TERM2]]
check(
    "E examキー（後期）は前期の試験を変えない（意図された挙動）",
    names1 == ["前期試験"] and names2 == ["後期試験・改訂"],
    f"適用後の試験名 前期={names1} 後期={names2}",
)

print()
failed = results.count(False)
print(f"結果: {len(results)}件中 {failed}件でバグ再現 / {len(results) - failed}件は期待どおり")
sys.exit(1 if failed else 0)
