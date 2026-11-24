# -*- coding: utf-8 -*-
"""
フェーズ2のテスト
対象:
  - CORS: github.io の任意サイトからは許可されず、自ドメイン(localhost含む)からは許可される
  - 認証の二枚組え: 短い証は約1日で切れる / 長い証(リフレッシュ)の発行・検証・ローテーション・無効化
  - /api/auth/refresh と /api/auth/logout エンドポイント（クッキー経由）
  - 未使用コードの削除: run.py と web/app.py は無いこと、web/api/exams.py は残っていること（使用中のため）
  - docs/ フォルダが無いこと
実行: docker exec class-information-bot python scripts/test_phase2.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = []

def check(case, ok, detail=""):
    results.append(ok)
    print(f"[{'期待どおり' if ok else 'NG'}] {case}" + (f": {detail}" if detail else ""))

# ---------- CORS ----------
from fastapi.testclient import TestClient
import web_api_integration

client = TestClient(web_api_integration.web_app, raise_server_exceptions=False)

r = client.get("/api/auth/me", headers={"Origin": "https://evil.github.io"})
acao = r.headers.get("access-control-allow-origin")
check("CORS 他人の github.io は許可されない", acao != "https://evil.github.io", f"allow-origin={acao}")

r = client.get("/api/auth/me", headers={"Origin": "http://localhost:8000"})
acao = r.headers.get("access-control-allow-origin")
check("CORS localhost は許可される", acao == "http://localhost:8000", f"allow-origin={acao}")

# ---------- 短い証の寿命 ----------
from jose import jwt as jose_jwt
import web.auth as auth
from web.config import SECRET_KEY, ALGORITHM

token = auth.create_access_token(424242, "tester")
payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
lifetime_h = (payload["exp"] - time.time()) / 3600
check("短い証の寿命は約1日", 20 <= lifetime_h <= 25, f"残り約{lifetime_h:.1f}時間")
check("短い証に type=access が入る", payload.get("type") == "access", f"type={payload.get('type')}")

# ---------- 長い証（リフレッシュ） ----------
tmp = tempfile.mkdtemp()
auth.REFRESH_TOKENS_FILE = os.path.join(tmp, "refresh_tokens.json")

raw = auth.issue_refresh_token(424242, "tester")
rec = auth.verify_refresh_token(raw)
check("長い証を発行して検証できる", bool(rec) and rec.get("user_id") == 424242)

new_raw, rec2 = auth.rotate_refresh_token(raw)
check("ローテーションで新しい長い証が得られる", bool(new_raw) and new_raw != raw)
check("ローテーション後の古い長い証は使えない", auth.verify_refresh_token(raw) is None)
check("使い回した古い証では再発行できない", auth.rotate_refresh_token(raw) is None)
check("新しい長い証は検証できる", bool(auth.verify_refresh_token(new_raw)))

auth.revoke_refresh_token(new_raw)
check("無効化した長い証は使えない", auth.verify_refresh_token(new_raw) is None)
check("存在しない長い証は検証できない", auth.verify_refresh_token("no-such-token") is None)

# ---------- refresh / logout エンドポイント ----------
raw2 = auth.issue_refresh_token(424242, "tester")
r = client.post("/api/auth/refresh")
check("クッキーなしの再発行は401", r.status_code == 401, f"status={r.status_code}")

r = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={raw2}"})
check("クッキーで再発行できる", r.status_code == 200, f"status={r.status_code}")
if r.status_code == 200:
    data = r.json()
    check("再発行で新しい短い証が返る", bool(data.get("access_token")))
    set_cookie = r.headers.get("set-cookie", "")
    check("再発行で新しいクッキーが付く", "refresh_token=" in set_cookie and "HttpOnly" in set_cookie, set_cookie[:60])
    r_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    check("新しい短い証で /api/auth/me が通る", r_me.status_code == 200, f"status={r_me.status_code}")
    r_old = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={raw2}"})
    check("使い回した古いクッキーでは再発行できない", r_old.status_code == 401, f"status={r_old.status_code}")
    # ローテーション後の新しいクッキーを抜き出してログアウト確認
    new_cookie_val = set_cookie.split("refresh_token=")[1].split(";")[0]
    r_lo = client.post("/api/auth/logout", headers={"Cookie": f"refresh_token={new_cookie_val}"})
    check("ログアウトできる", r_lo.status_code == 200, f"status={r_lo.status_code}")
    r_after = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={new_cookie_val}"})
    check("ログアウト後は再発行できない", r_after.status_code == 401, f"status={r_after.status_code}")

# ---------- ファイル整理 ----------
check("run.py は削除されている", not os.path.exists("run.py"))
check("web/app.py は削除されている", not os.path.exists("web/app.py"))
check("web/api/exams.py は残っている（使用中）", os.path.exists("web/api/exams.py"))
check("docs/ は撤去されている", not os.path.exists("docs"))

print()
failed = results.count(False)
print(f"結果: {len(results)}件中 {failed}件NG / {len(results) - failed}件は期待どおり")
sys.exit(1 if failed else 0)
