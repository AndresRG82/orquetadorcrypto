FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY services/strategy-router/ services/strategy-router/
CMD ["python", "-m", "services.strategy-router.main"]
