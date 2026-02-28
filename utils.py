import json
import os

def load_user_data(user_id):
    path = f"data/{user_id}.json"
    if not os.path.exists(path):
        return{}
    with open(path,"r",encoding="utf-8") as f:
        return json.load(f)

def save_user_data(user_id, data):
    os.makedirs("data",exist_ok=True)
    with open(f"data/{user_id}.json","w",encoding="utf-8") as f:
        json.dump(data,f,indet=4, ensure_ascii = False)