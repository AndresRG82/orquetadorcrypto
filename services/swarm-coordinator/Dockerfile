FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir httpx

COPY shared/ /app/shared/
COPY services/swarm-coordinator/ /app/services/swarm-coordinator/

CMD ["python", "/app/services/swarm-coordinator/main.py"]
