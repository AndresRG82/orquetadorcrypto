FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir httpx redis asyncpg python-dotenv pydantic
COPY shared/ /app/shared/
COPY services/monitoring/main.py /app/service/main.py
CMD ["python", "/app/service/main.py"]
