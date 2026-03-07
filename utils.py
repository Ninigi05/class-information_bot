import json
import os

def load_user_data(user_id):
    path = f"user_{user_id}.json"
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_user_data(user_id, data):
    with open(f"user_{user_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

async def send_dm(user, message):
    try:
        dm = await user.create_dm()
        await dm.send(message)
    except Exception as e:
        try:
            uid = user.id
        except Exception:
            uid = "unknown"
        print(f"DM送信失敗: {uid} ({e})")

async def send_long_dm(user, text, chunk_size=1900):
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    try:
        dm = await user.create_dm()
        for chunk in chunks:
            await dm.send(chunk)
    except Exception as e:
        try:
            uid = user.id
        except Exception:
            uid = "unknown"
        print(f"DM送信エラー: {uid} ({e})")