FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/paper-trading/engine.py /app/service/engine.py
COPY services/paper-trading/portfolio.py /app/service/portfolio.py
CMD ["python", "/app/service/engine.py"]