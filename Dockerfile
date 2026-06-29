FROM python:3.12-slim

RUN apt-get update && apt-get install -y gcc libsqlite3-0 libsqlite3-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --no-binary pyswisseph pyswisseph

COPY . .
CMD ["python3", "bot.py"]
