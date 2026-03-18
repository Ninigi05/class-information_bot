"""
環境チェックスクリプト
起動前に必要な環境とファイルを確認
"""

import os
import sys
import json

def check_env():
    """環境変数チェック"""
    print("\n📋 環境変数チェック")
    print("-" * 40)
    
    required_vars = ["DISCORD_TOKEN"]
    optional_vars = ["GUILD_ID", "API_KEY", "WEB_HOST", "WEB_PORT"]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
            print(f"❌ {var}: 未設定")
        else:
            val = os.getenv(var)
            if var == "DISCORD_TOKEN":
                val = val[:10] + "***"
            print(f"✅ {var}: 設定済み")
    
    for var in optional_vars:
        val = os.getenv(var, "(デフォルト値を使用)")
        status = "✅" if os.getenv(var) else "ℹ️"
        print(f"{status} {var}: {val}")
    
    if missing:
        print(f"\n⚠️  必須環境変数が未設定です: {', '.join(missing)}")
        print("    .env ファイルを確認してください")
        return False
    
    return True


def check_files():
    """必要なファイルチェック"""
    print("\n📁 ファイルチェック")
    print("-" * 40)
    
    required_files = [
        "main.py",
        "utils.py",
        "api_security.py",
        "web_api_integration.py",
    ]
    
    optional_files = [
        "schedule_viewer.html",
        "requirements.txt",
    ]
    
    missing = []
    for file in required_files:
        if os.path.exists(file):
            print(f"✅ {file}")
        else:
            print(f"❌ {file}: 見つかりません")
            missing.append(file)
    
    for file in optional_files:
        if os.path.exists(file):
            print(f"✅ {file}")
        else:
            print(f"⚠️  {file}: 見つかりません")
    
    if missing:
        print(f"\n❌ 必須ファイルが見つかりません: {', '.join(missing)}")
        return False
    
    return True


def check_packages():
    """必要なパッケージチェック"""
    print("\n📦 パッケージチェック")
    print("-" * 40)
    
    required_packages = [
        "discord",
        "fastapi",
        "uvicorn",
        "dotenv",
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package}: インストールされていません")
            missing.append(package)
    
    if missing:
        print(f"\n⚠️  パッケージをインストールしてください:")
        print(f"    pip install {' '.join(missing)}")
        print(f"    または: pip install -r requirements.txt")
        return False
    
    return True


def check_user_data():
    """ユーザーデータファイルチェック"""
    print("\n👤 ユーザーデータチェック")
    print("-" * 40)
    
    user_files = [f for f in os.listdir(".") if f.startswith("user_") and f.endswith(".json")]
    
    if user_files:
        print(f"✅ ユーザーデータファイル: {len(user_files)} 個")
        for file in user_files[:5]:
            try:
                with open(file, "r") as f:
                    data = json.load(f)
                    classes = len(data.get("classes", []))
                    print(f"   - {file}: {classes} 個の授業")
            except:
                pass
    else:
        print("ℹ️  ユーザーデータはまだ登録されていません")
    
    return True


def main():
    print("=" * 40)
    print("Discord授業情報Bot - 環境チェック")
    print("=" * 40)
    
    results = {
        "環境変数": check_env(),
        "ファイル": check_files(),
        "パッケージ": check_packages(),
        "ユーザーデータ": check_user_data(),
    }
    
    print("\n" + "=" * 40)
    print("チェック結果")
    print("=" * 40)
    
    all_ok = all(results.values())
    
    for name, result in results.items():
        status = "✅" if result else "❌"
        print(f"{status} {name}")
    
    print()
    if all_ok:
        print("✅ すべてのチェックが完了しました")
        print("\n起動コマンド:")
        print("  python main.py")
        print("  または: python run.py")
        return 0
    else:
        print("❌ 設定を完了してください")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中断されました")
        sys.exit(1)
