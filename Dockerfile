FROM python:3.12-slim

ENV PYTHONIOENCODING=utf-8
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y build-essential libsqlite3-dev libsqlite3-0 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --no-binary pyswisseph pyswisseph

COPY . .
CMD ["python3", "bot.py"]
