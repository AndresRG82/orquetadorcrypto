#!/bin/sh
if [ "$MOCK_MODE" = "true" ]; then
    echo "Starting MOCK market scanner..."
    exec python /app/service/mock_scanner.py
else
    echo "Starting REAL market scanner..."
    exec python /app/service/scanner.py
fi
