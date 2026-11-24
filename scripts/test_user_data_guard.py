# -*- coding: utf-8 -*-
"""
ユーザーデータ保護ガードのテスト（2026-11-24 の上書き事故の再発防止）
既存ファイルが読めない（破損・権限エラー）とき、読み込み→保存の流れで
元ファイルを空データで上書きしないことを確認する。
実行: docker exec class-information-bot python scripts/test_user_data_guard.py
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = []

def check(case, ok, detail=""):
    results.append(ok)
    print(f"[{'期待どおり' if ok else 'NG'}] {case}" + (f": {detail}" if detail else ""))

import utils

tmp = tempfile.mkdtemp()
utils.BASE_DIR = tmp
UID = 313131

path = os.path.join(tmp, f"user_{UID}.json")
original = {"classes_by_term": {"前期": [{"subject": "大事な授業"}], "後期": []}, "exam_schedules_by_term": {"前期": [], "後期": []}}
with open(path, "w", encoding="utf-8") as f:
    json.dump(original, f, ensure_ascii=False)

# 正常系: 読んで保存しても中身が残る
data = utils.load_user_data(UID)
data["mail_seen_ids"] = ["x"]
utils.save_user_data(UID, data)
after = json.load(open(path, encoding="utf-8"))
check("正常時は保存できる", after.get("mail_seen_ids") == ["x"] and after["classes_by_term"]["前期"][0]["subject"] == "大事な授業")

# 異常系: ファイルが壊れている状態で「読む→保存」しても上書きしない
corrupt = "{ this is not json !!!"
with open(path, "w", encoding="utf-8") as f:
    f.write(corrupt)
data = utils.load_user_data(UID)  # 読めず空が返る想定
data["mail_seen_ids"] = ["y"]
utils.save_user_data(UID, data)
content = open(path, encoding="utf-8").read()
check("読めないファイルは上書きしない", content == corrupt, "上書きされた" if content != corrupt else "")

# 新規ユーザー（ファイルなし）は保存できる
UID2 = 323232
data2 = utils.load_user_data(UID2)
data2["mail_seen_ids"] = ["z"]
utils.save_user_data(UID2, data2)
p2 = os.path.join(tmp, f"user_{UID2}.json")
check("新規ユーザーは保存できる", os.path.exists(p2) and json.load(open(p2, encoding="utf-8")).get("mail_seen_ids") == ["z"])

print()
failed = results.count(False)
print(f"結果: {len(results)}件中 {failed}件NG / {len(results) - failed}件は期待どおり")
sys.exit(1 if failed else 0)
