FROM python:3.12-slim

RUN apt-get update && apt-get install -y gcc libsqlite3-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && pip install pyswisseph --no-binary pyswisseph

COPY . .
CMD ["python3", "bot.py"]
