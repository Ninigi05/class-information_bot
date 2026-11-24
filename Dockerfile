FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# matplotlib / japanize-matplotlib で使う最小フォント類
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    openssl \
    tzdata \
    fonts-ipafont-gothic \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

# NOTE: 非root化は見送り中。Docker Desktop のバインドマウントでは uid の扱いが
# 通常の Linux と異なり、データファイル（リポジトリ直下の JSON 群）へのアクセスが
# 不安定になるため。データ専用ボリュームへ移行するフェーズで再挑戦する。

CMD ["python", "main.py"]
