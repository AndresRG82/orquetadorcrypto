FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/qwen-analyzer/analyzer.py /app/service/analyzer.py
COPY services/qwen-analyzer/feedback.py /app/service/feedback.py
CMD ["python", "-m", "uvicorn", "service.analyzer:app", "--host", "0.0.0.0", "--port", "8000"]