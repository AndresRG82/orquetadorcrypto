FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/dashboard/api.py /app/service/api.py
CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]