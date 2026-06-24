FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY services/training-export/ services/training-export/
CMD ["python", "-m", "services.training-export.main"]
