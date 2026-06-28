#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# run_validacion.sh — Fase 2: Validación de flujo end-to-end
# ─────────────────────────────────────────────────────────────
# Requiere: docker compose, redis-cli, python3
# Uso:
#   bash run_validacion.sh              # steps 1-4 en secuencia
#   bash run_validacion.sh --replay      # solo replay 3x
#   bash run_validacion.sh --risk        # solo risk-manager tests
#   bash run_validacion.sh --record      # solo grabar dataset OHLCV
#   bash run_validacion.sh --baseline    # arrancar baseline sin LLM
# ─────────────────────────────────────────────────────────────

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPLAY_DATA="${ROOT_DIR}/data/replay_ohlcv.json"
SIGNALS_DIR="${ROOT_DIR}/data/replay_signals"
REQUIRED_PY="python3"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"

mkdir -p "${ROOT_DIR}/data" "${SIGNALS_DIR}"

# ── helpers ──────────────────────────────────────────────────

info()  { echo -e "\e[1;34m[INFO]\e[0m $*"; }
ok()    { echo -e "\e[1;32m[OK]\e[0m   $*"; }
fail()  { echo -e "\e[1;31m[FAIL]\e[0m $*"; }

check_prereqs() {
    command -v docker &>/dev/null || { fail "docker not found"; exit 1; }
    command -v python3 &>/dev/null || { fail "python3 not found"; exit 1; }
}

cleanup() {
    info "Limpiando consumidores de prueba..."
    # estos fallan silenciosamente si no existen, que es lo que queremos
    echo 'DEL strategy:latest_signals' | redis-cli --raw 2>/dev/null || true
    echo 'DEL circuit:state' | redis-cli --raw 2>/dev/null || true
    echo 'DEL risk:excluded_symbols' | redis-cli --raw 2>/dev/null || true
    echo 'DEL portfolio:state' | redis-cli --raw 2>/dev/null || true
}

# ── step 1: record dataset ───────────────────────────────────

step_record() {
    info "Step 1/4: Grabando dataset OHLCV desde market:data..."
    check_prereqs

    if [ -f "$REPLAY_DATA" ]; then
        read -p "Dataset ya existe. ¿Sobrescribir? (y/N): " overwrite
        if [ "$overwrite" != "y" ] && [ "$overwrite" != "Y" ]; then
            info "Usando dataset existente: $REPLAY_DATA"
            return 0
        fi
    fi

    info "Asegurando que el sistema esté corriendo..."
    docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q market-scanner || {
        fail "market-scanner no está corriendo. Ejecutá 'docker compose up -d' primero."
        return 1
    }

    duration=${RECORD_DURATION:-300}
    info "Grabando ${duration}s de datos (RECORD_DURATION=$duration)..."
    python3 "${ROOT_DIR}/replay.py" --record "$REPLAY_DATA" --duration "$duration"

    if [ -f "$REPLAY_DATA" ] && [ -s "$REPLAY_DATA" ]; then
        ok "Dataset grabado: $(wc -l < "$REPLAY_DATA") líneas, $(du -h "$REPLAY_DATA" | cut -f1)"
    else
        fail "No se pudo grabar el dataset"
        return 1
    fi
}

# ── step 2: replay 3x ────────────────────────────────────────

step_replay() {
    info "Step 2/4: Replay 3x determinista..."

    if [ ! -f "$REPLAY_DATA" ]; then
        fail "No hay dataset. Ejecutá --record primero."
        return 1
    fi

    export REPLAY_MODE=true
    export REPLAY_DATA

    for run in 1 2 3; do
        info "Replay run #${run}..."

        # Limpiar el stream de indicators para que los consumidores empiecen limpios
        echo 'DEL market:indicators' | redis-cli --raw 2>/dev/null || true

        # Limpiar señales previas
        echo 'DEL strategy:latest_signals' | redis-cli --raw 2>/dev/null || true

        OUT="${SIGNALS_DIR}/run_${run}.json"

        # Iniciar captura de señales en background
        python3 -c "
import asyncio, json, sys
sys.path.insert(0, '${ROOT_DIR}')
from shared.redis_client import RedisClient
async def capture():
    r = await RedisClient.get_instance()
    group = 'replay-verify-${run}'
    consumer = 'verify-${run}-1'
    for stream in ['market:indicators']:
        try: await r.redis.xgroup_create(stream, group, id='0', mkstream=True)
        except: pass
    signals = []
    deadline = asyncio.get_event_loop().time() + 120
    while asyncio.get_event_loop().time() < deadline:
        for stream in ['market:indicators']:
            msgs = await r.read_stream(stream, group, consumer, count=50, block=2000)
            for msg_id, data in msgs:
                signals.append(data)
        await asyncio.sleep(0.1)
    with open('${OUT}', 'w') as f:
        json.dump(signals, f, default=str)
    await r.close()
    print(f'Captured {len(signals)} indicators')
asyncio.run(capture())
" &
        CAPTURE_PID=$!

        # Pequeña pausa para que el captura esté listo
        sleep 2

        # Ejecutar replay
        python3 "${ROOT_DIR}/replay.py" --replay "$REPLAY_DATA" --speed 10 || {
            fail "Replay run #${run} falló"
            kill $CAPTURE_PID 2>/dev/null || true
            return 1
        }

        # Esperar a que termine la captura
        wait $CAPTURE_PID 2>/dev/null || true

        if [ ! -f "$OUT" ] || [ ! -s "$OUT" ]; then
            fail "No se capturaron señales en run #${run}"
            return 1
        fi
        ok "Run #${run} completado: $(wc -l < "$OUT") señales"
    done

    # Comparar runs
    info "Comparando runs 1, 2, 3..."
    if diff <(jq -c '.[] | {symbol, signal, confidence}' "${SIGNALS_DIR}/run_1.json" | sort) \
           <(jq -c '.[] | {symbol, signal, confidence}' "${SIGNALS_DIR}/run_2.json" | sort) \
           && diff <(jq -c '.[] | {symbol, signal, confidence}' "${SIGNALS_DIR}/run_2.json" | sort) \
                   <(jq -c '.[] | {symbol, signal, confidence}' "${SIGNALS_DIR}/run_3.json" | sort); then
        ok "Replay 3x: TODAS LAS SEÑALES IDÉNTICAS ✅"
    else
        fail "Replay 3x: SEÑALES DIFIEREN entre corridas ❌"
        info "Diferencia run_1 vs run_2:"
        diff <(jq -c '.[] | {symbol, signal, confidence}' "${SIGNALS_DIR}/run_1.json" | sort) \
             <(jq -c '.[] | {symbol, signal, confidence}' "${SIGNALS_DIR}/run_2.json" | sort) || true
        return 1
    fi
}

# ── step 3: risk-manager tests ───────────────────────────────

step_risk_tests() {
    info "Step 3/4: Risk-manager validation tests..."
    check_prereqs

    docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q risk-manager || {
        fail "risk-manager no está corriendo. Ejecutá 'docker compose up -d' primero."
        return 1
    }

    python3 "${ROOT_DIR}/scripts/test_risk_manager.py" --all && {
        ok "Risk-manager tests: TODOS PASARON ✅"
    } || {
        fail "Risk-manager tests: ALGUNOS FALLARON ❌"
        return 1
    }
}

# ── step 4: baseline sin LLM ─────────────────────────────────

step_baseline() {
    info "Step 4/4: Baseline sin LLM"
    info "Este paso corre el sistema 48-72h sin Ollama para establecer baseline técnico."
    echo ""
    echo "Para ejecutar:"
    echo "  1. Detener el sistema actual: docker compose down"
    echo "  2. Arrancar sin Ollama (o con host inválido):"
    echo "     OLLAMA_HOST=http://localhost:1 docker compose up -d"
    echo "  3. Monitorear:"
    echo "     - Señales generadas (cada 5m revisar strategy:latest_signals)"
    echo "     - Señales aprobadas (risk:approved stream)"
    echo "     - Trades ejecutados (dashboard en http://localhost:8001)"
    echo "  4. Al finalizar, extraer estadísticas:"
    echo "     python3 scripts/trace_signals.py --hours 72"
    echo ""
    echo "Recomendación: correr en terminal separada con:"
    echo "  screen -S baseline"
    echo "  OLLAMA_HOST=http://localhost:1 docker compose up -d"
    echo "  CTRL+A D (detach)"
    echo ""
    echo "Tiempo estimado: 48-72 horas."
    echo "Criterio de éxito: el sistema no crashea sin LLM, las señales técnicas fluyen."
}

# ── main ─────────────────────────────────────────────────────

main() {
    cd "$ROOT_DIR"

    case "${1:-}" in
        --record)
            step_record
            ;;
        --replay)
            step_replay
            ;;
        --risk)
            step_risk_tests
            ;;
        --baseline)
            step_baseline
            ;;
        --all|"")
            check_prereqs
            cleanup
            step_record || exit 1
            step_replay || exit 1
            cleanup
            step_risk_tests || exit 1
            step_baseline
            ok "Validación Fase 2 completada."
            ;;
        *)
            echo "Uso: $0 [--record|--replay|--risk|--baseline|--all]"
            exit 1
            ;;
    esac
}

main "$@"
