FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY services/sentiment/ services/sentiment/
CMD ["python", "-m", "services.sentiment.main"]
