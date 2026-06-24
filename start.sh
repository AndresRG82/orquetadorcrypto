#!/usr/bin/env bash
set -e

echo "=== CryptoTrader Orquestador ==="
echo ""

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Creado .env desde .env.example"
fi

echo "Verificando Ollama..."
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  Ollama OK"
    if curl -s http://localhost:11434/api/tags | grep -q "qwen3:4b"; then
        echo "  Modelo qwen3:4b disponible"
    else
        echo "  ADVERTENCIA: qwen3:4b no encontrado. Ejecuta: ollama pull qwen3:4b"
    fi
else
    echo "  ADVERTENCIA: Ollama no esta corriendo. Inicialo con: ollama serve"
fi

echo ""
echo "Construyendo contenedores..."
docker compose build

echo ""
echo "Iniciando servicios..."
docker compose up -d

echo ""
echo "Esperando a que los servicios esten listos..."
sleep 10

echo ""
echo "=== Servicios activos ==="
docker compose ps

echo ""
echo "=== Accesos ==="
echo "  Dashboard:  http://localhost:8000"
echo "  Grafana:    http://localhost:3000 (admin/admin)"
echo "  Logs:       docker compose logs -f [servicio]"
echo ""
echo "Para detener:  docker compose down"
echo "Para ver logs: docker compose logs -f"
