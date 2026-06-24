FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev libssl-dev build-essential && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 300 -r requirements.txt
COPY shared/ shared/
COPY services/backtesting/ services/backtesting/
CMD ["python", "-m", "services.backtesting.main"]
