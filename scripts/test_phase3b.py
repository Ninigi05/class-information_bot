# -*- coding: utf-8 -*-
"""
フェーズ3b「Gmail トークンの保管（pickle→暗号化JSON）」のテスト
対象:
  - 保存が暗号化JSONで、平文にトークンが出ない / 権限 0600
  - 読み戻しで同じ資格情報が復元できる
  - 違う鍵では復号できない
  - 旧 pickle からの自動移行（pickle が消え、JSON ができる）
  - mail_service 側の存在確認・一覧が新形式を見られる
実行: docker exec class-information-bot python scripts/test_phase3b.py
"""
import os
import sys
import stat
import pickle
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = []

def check(case, ok, detail=""):
    results.append(ok)
    print(f"[{'期待どおり' if ok else 'NG'}] {case}" + (f": {detail}" if detail else ""))

from cryptography.fernet import Fernet

KEY1 = Fernet.generate_key().decode()
os.environ["GMAIL_TOKEN_ENCRYPTION_KEY"] = KEY1

import token_store
import mail_service
from google.oauth2.credentials import Credentials

tmp = tempfile.mkdtemp()
token_store.TOKEN_DIR = tmp
mail_service.TOKEN_DIR = tmp

INFO = {
    "token": "dummy-access-token",
    "refresh_token": "dummy-refresh-token-SECRET",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "dummy-client-id",
    "client_secret": "dummy-client-secret",
    "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
}
creds = Credentials.from_authorized_user_info(dict(INFO))

token_store.save_credentials(424242, creds)
json_path = os.path.join(tmp, "user_424242.json")
check("暗号化JSONが保存される", os.path.exists(json_path))
check("pickleは作られない", not os.path.exists(os.path.join(tmp, "user_424242.pickle")))

raw = open(json_path, encoding="utf-8").read()
check("平文に refresh_token が出ない", "dummy-refresh-token-SECRET" not in raw)
check("平文に access token も出ない", "dummy-access-token" not in raw)

mode = stat.S_IMODE(os.stat(json_path).st_mode)
check("ファイル権限が 0600", mode == 0o600, oct(mode))

loaded = token_store.load_credentials(424242)
check("読み戻しできる", loaded is not None)
check("refresh_token が一致", bool(loaded) and loaded.refresh_token == INFO["refresh_token"])
check("token が一致", bool(loaded) and loaded.token == INFO["token"])

os.environ["GMAIL_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
check("違う鍵では読めない", token_store.load_credentials(424242) is None)
os.environ["GMAIL_TOKEN_ENCRYPTION_KEY"] = KEY1

# 旧 pickle からの移行
legacy_path = os.path.join(tmp, "user_555555.pickle")
with open(legacy_path, "wb") as f:
    pickle.dump(creds, f)
migrated = token_store.load_credentials(555555)
check("旧pickleから読み戻せる（移行）", bool(migrated) and migrated.refresh_token == INFO["refresh_token"])
check("移行でJSONができる", os.path.exists(os.path.join(tmp, "user_555555.json")))
check("移行でpickleが消える", not os.path.exists(legacy_path))

# mail_service 側の見え方
check("has_gmail_token が新形式を見られる", mail_service.has_gmail_token(424242))
check("list_linked_user_ids に新形式が入る", 424242 in mail_service.list_linked_user_ids())

# 削除
token_store.delete_credentials(424242)
check("削除でJSONが消える", not os.path.exists(json_path))
check("削除後は読めない", token_store.load_credentials(424242) is None)

print()
failed = results.count(False)
print(f"結果: {len(results)}件中 {failed}件NG / {len(results) - failed}件は期待どおり")
sys.exit(1 if failed else 0)
