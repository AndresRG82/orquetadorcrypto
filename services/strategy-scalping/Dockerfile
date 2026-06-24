FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/strategy-scalping/agent.py /app/service/agent.py
CMD ["python", "/app/service/agent.py"]