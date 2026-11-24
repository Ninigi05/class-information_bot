# -*- coding: utf-8 -*-
"""
フェーズ3c「悪用・連打対策」のテスト
対象:
  - レート制限の IP 判定: cf-connecting-ip を信じるのは身内（ループバック/プライベート）からの接続だけ。
    外部の接続元がヘッダーを付けても信じない
  - Web ログインに回数制限が付いている
  - 重い Discord コマンド（/mail fetch、/web applykey）にクールダウンが付いている
実行: docker exec class-information-bot python scripts/test_phase3c.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = []

def check(case, ok, detail=""):
    results.append(ok)
    print(f"[{'期待どおり' if ok else 'NG'}] {case}" + (f": {detail}" if detail else ""))

import web_api_integration as wai

def fake_request(peer, cf=None):
    headers = {}
    if cf:
        headers["cf-connecting-ip"] = cf
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))

check(
    "外部からの接続では cf-connecting-ip を信じない",
    wai._client_ip(fake_request("203.0.113.5", cf="1.2.3.4")) == "203.0.113.5",
    wai._client_ip(fake_request("203.0.113.5", cf="1.2.3.4")),
)
check(
    "トンネル経由（プライベートIP）では cf-connecting-ip を使う",
    wai._client_ip(fake_request("172.18.0.5", cf="1.2.3.4")) == "1.2.3.4",
)
check(
    "ループバックでは cf-connecting-ip を使う",
    wai._client_ip(fake_request("127.0.0.1", cf="1.2.3.4")) == "1.2.3.4",
)
check(
    "ヘッダーが無ければ接続元IPを使う",
    wai._client_ip(fake_request("172.18.0.5")) == "172.18.0.5",
)

login_routes = [r for r in wai.web_app.routes if getattr(r, "path", "") == "/api/auth/login"]
deps = login_routes[0].dependencies if login_routes else []
check("ログインに回数制限が付いている", len(login_routes) == 1 and len(deps) >= 1, f"routes={len(login_routes)} deps={len(deps)}")

# クールダウンは「2回連続で実行チェックすると2回目に CommandOnCooldown になる」ことで確認する
import asyncio
from types import SimpleNamespace as _SN
from discord import app_commands as _ac


def _cooldown_works(cmd) -> bool:
    async def _run():
        from datetime import datetime, timezone
        fake = _SN(
            user=_SN(id=987654321), guild=None, channel=_SN(id=1),
            created_at=datetime.now(timezone.utc),
        )
        passed = 0
        for _ in range(2):
            try:
                for chk in cmd.checks:
                    await chk(fake)
                passed += 1
            except _ac.CommandOnCooldown:
                return passed == 1
            except Exception:
                return False
        return False

    return asyncio.run(_run())


sys.path.insert(0, os.path.join(os.getcwd(), "cogs"))
import importlib.util
spec = importlib.util.spec_from_file_location("mail_cog_mod", os.path.join("cogs", "mail_cog.py"))
mail_cog_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail_cog_mod)
check("/mail fetch にクールダウン", _cooldown_works(mail_cog_mod.MailCog.mail_fetch))

import main as bot_main
check("/web applykey にクールダウン", _cooldown_works(bot_main.web_applykey))

print()
failed = results.count(False)
print(f"結果: {len(results)}件中 {failed}件NG / {len(results) - failed}件は期待どおり")
sys.exit(1 if failed else 0)
