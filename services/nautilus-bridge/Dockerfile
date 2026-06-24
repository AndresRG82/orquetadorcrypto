FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir nautilus_trader[backtest]

COPY shared/ /app/shared/
COPY services/nautilus-bridge/ /app/services/nautilus-bridge/

CMD ["python", "/app/services/nautilus-bridge/main.py"]
