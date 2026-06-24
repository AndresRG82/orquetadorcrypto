FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ /app/shared/
COPY services/market-scanner/scanner.py /app/service/scanner.py
COPY services/market-scanner/mock_scanner.py /app/service/mock_scanner.py
COPY services/market-scanner/entrypoint.sh /app/service/entrypoint.sh
RUN chmod +x /app/service/entrypoint.sh
ENTRYPOINT ["/app/service/entrypoint.sh"]