FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/risk-manager/risk.py /app/service/risk.py
CMD ["python", "/app/service/risk.py"]