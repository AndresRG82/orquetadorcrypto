#!/usr/bin/env bash
set -euo pipefail

# verify.sh — post-change verification for crypto-trader
# Usage: bash verify.sh [--all] [--service <name>]
#   --all         Rebuild all services (use when shared/ changed)
#   --service     Force rebuild a specific service (skip git detection)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

HAVE_DOCKER=false
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    HAVE_DOCKER=true
fi

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

MODIFIED_PY=()
MODIFIED_SERVICES=()
MODIFIED_SHARED=()

detect_changes() {
    if [[ -n "${FORCE_SERVICE:-}" ]]; then
        info "Forcing rebuild of service: $FORCE_SERVICE"
        MODIFIED_SERVICES+=("$FORCE_SERVICE")
        return
    fi

    if ! git rev-parse --is-inside-work-tree &>/dev/null; then
        warn "Not a git repo — cannot detect changes. Use --service <name>."
        return
    fi

    local files
    files=$( { git diff --name-only HEAD 2>/dev/null || git diff --name-only; } | sort -u || true)
    files+=$'\n'
    files+=$(git ls-files --others --exclude-standard 2>/dev/null || true)
    files=$(echo "$files" | grep -v '^$' | sort -u || true)

    if [[ -z "$files" ]]; then
        warn "No changes detected."
        return
    fi

    info "Files changed:"
    echo "$files" | while IFS= read -r f; do echo "    $f"; done

    while IFS= read -r f; do
        [[ "$f" == *.py && -f "$f" ]] && MODIFIED_PY+=("$f")
    done <<< "$files"

    while IFS= read -r f; do
        if [[ "$f" == services/*/*.py || "$f" == services/*/Dockerfile || "$f" == services/*/entrypoint.sh ]]; then
            svc=$(echo "$f" | cut -d/ -f2)
            [[ ! " ${MODIFIED_SERVICES[*]} " =~ " ${svc} " ]] && MODIFIED_SERVICES+=("$svc")
        fi
    done <<< "$files"

    while IFS= read -r f; do
        if [[ "$f" == shared/*.py && -f "$f" ]]; then
            mod_name=$(basename "$f" .py)
            [[ ! " ${MODIFIED_SHARED[*]} " =~ " ${mod_name} " ]] && MODIFIED_SHARED+=("$mod_name")
        fi
    done <<< "$files"

    for mod in "${MODIFIED_SHARED[@]}"; do
        info "shared/$mod.py changed — searching for consumers..."
        deps=$(grep -rl "from shared\.$mod\b" services/ 2>/dev/null || true)
        if [[ -n "$deps" ]]; then
            while IFS= read -r dep_file; do
                svc=$(echo "$dep_file" | cut -d/ -f2)
                echo "    → $svc (via $dep_file)"
                [[ ! " ${MODIFIED_SERVICES[*]} " =~ " ${svc} " ]] && MODIFIED_SERVICES+=("$svc")
            done <<< "$deps"
        fi
    done
}

step_py_compile() {
    echo ""
    echo -e "${CYAN}[1/4] py_compile${NC}"
    if [[ ${#MODIFIED_PY[@]} -eq 0 ]]; then
        pass "No Python files to check"
        return
    fi
    local ok=true
    for f in "${MODIFIED_PY[@]}"; do
        if python -B -m py_compile "$f" 2>/dev/null; then
            pass "$f"
        else
            python -B -m py_compile "$f" || true
            fail "$f (syntax error)"
            ok=false
        fi
    done
    $ok && return 0
    echo -e "${RED}  Fix syntax errors before continuing.${NC}"
    return 1
}

step_rebuild_services() {
    echo ""
    echo -e "${CYAN}[2/4] Rebuild & restart${NC}"
    if ! $HAVE_DOCKER; then
        warn "Docker not available — skipping rebuild"
        return
    fi
    if $DO_ALL; then
        info "Rebuilding ALL services..."
        if timeout 300 docker compose up -d --build 2>&1; then
            pass "All services started"
        else
            warn "Some services failed — check logs"
        fi
        return
    fi
    if [[ ${#MODIFIED_SERVICES[@]} -eq 0 ]]; then
        pass "No services to rebuild"
        return
    fi
    local svcs=()
    while IFS= read -r s; do svcs+=("$s"); done < <(printf '%s\n' "${MODIFIED_SERVICES[@]}" | sort -u)
    info "Services affected: ${svcs[*]}"

    for svc in "${svcs[@]}"; do
        if grep -q "^\s*${svc}:" docker-compose.yml 2>/dev/null; then
            info "Rebuilding $svc..."
            if timeout 120 docker compose up -d --build "$svc" 2>&1; then
                pass "$svc started"
            else
                warn "$svc failed to start — check logs"
            fi
        else
            warn "$svc not found in docker-compose.yml — skipping rebuild"
        fi
    done
}

step_show_logs() {
    echo ""
    echo -e "${CYAN}[3/4] Recent logs (last 50 lines)${NC}"
    if ! $HAVE_DOCKER; then
        warn "Docker not available — skipping logs"
        return
    fi
    if [[ ${#MODIFIED_SERVICES[@]} -eq 0 ]]; then
        pass "No services to inspect"
        return
    fi
    local svcs=()
    while IFS= read -r s; do svcs+=("$s"); done < <(printf '%s\n' "${MODIFIED_SERVICES[@]}" | sort -u)
    for svc in "${svcs[@]}"; do
        echo -e "${CYAN}─── $svc ───${NC}"
        timeout 30 docker compose logs --tail=50 "$svc" 2>/dev/null || \
            warn "No logs available for $svc"
    done
}

step_shared_impact() {
    echo ""
    echo -e "${CYAN}[4/4] Shared module impact analysis${NC}"
    if [[ ${#MODIFIED_SHARED[@]} -eq 0 ]]; then
        pass "No shared modules changed"
        return
    fi
    warn "${#MODIFIED_SHARED[@]} shared module(s) changed — verify ALL consumers:"
    for mod in "${MODIFIED_SHARED[@]}"; do
        echo ""
        info "Consumers of shared.$mod:"
        deps=$(grep -rl "from shared\.$mod\b\|import shared\.$mod\b" services/ 2>/dev/null || true)
        if [[ -n "$deps" ]]; then
            while IFS= read -r dep_file; do echo "    services/$dep_file"; done <<< "$deps"
        else
            echo "    (none)"
        fi
    done
    echo ""
    warn "If you changed a model's fields (shared/models.py), check ALL consumers."
    warn "If you changed config keys (shared/config.py), verify all env vars in .env / docker-compose."
}

# ── Main ────────────────────────────────────────────────────────────────

FORCE_SERVICE=""
DO_ALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all) DO_ALL=true ;;
        --service) shift; FORCE_SERVICE="$1" ;;
        *) echo "Usage: $0 [--all] [--service <name>]"; exit 1 ;;
    esac
    shift
done

echo -e "${CYAN}═══════════════════════════════════════${NC}"
echo -e "${CYAN}  crypto-trader post-change verification${NC}"
echo -e "${CYAN}═══════════════════════════════════════${NC}"

detect_changes
step_py_compile || { echo -e "${RED}Verification aborted.${NC}"; exit 1; }
step_rebuild_services
step_show_logs
step_shared_impact

echo ""
echo -e "${GREEN}✓ Verification complete.${NC}"
echo ""
echo "Next steps (manual):"
echo "  1. Check the logs above for startup errors"
echo "  2. Verify the service processes data (docker compose logs --tail=20 -f <service>)"
echo "  3. If shared/ changed, test the affected services listed in step 4"
