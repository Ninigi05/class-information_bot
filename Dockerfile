FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# matplotlib / japanize-matplotlib で使う最小フォント類
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    fonts-ipafont-gothic \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

CMD ["python", "main.py"]
