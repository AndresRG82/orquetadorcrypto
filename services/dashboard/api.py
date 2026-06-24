from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone, timedelta
import json
import sys

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database

app = FastAPI(title="CryptoTrader Dashboard", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis: RedisClient | None = None
db: Database | None = None


@app.on_event("startup")
async def startup():
    global redis, db
    redis = await RedisClient.get_instance()
    db = await Database.get_instance()


@app.on_event("shutdown")
async def shutdown():
    if redis:
        await redis.close()
    if db:
        await db.close()


@app.get("/")
async def root():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html><head><title>CryptoTrader Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a0a; color: #e0e0e0; padding: 20px; }
        h1 { color: #00d4aa; margin-bottom: 20px; font-size: 1.5rem; }
        h2 { color: #00d4aa; margin: 15px 0 10px; font-size: 1.1rem; border-bottom: 1px solid #1a1a1a; padding-bottom: 5px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .card { background: #111; border: 1px solid #222; border-radius: 8px; padding: 12px; }
        .card .label { font-size: 0.7rem; color: #888; text-transform: uppercase; }
        .card .value { font-size: 1.3rem; font-weight: 700; margin-top: 2px; }
        .card .value.positive { color: #00d4aa; }
        .card .value.negative { color: #ff4757; }
        table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
        th { text-align: left; color: #888; padding: 6px; border-bottom: 1px solid #222; }
        td { padding: 4px 6px; border-bottom: 1px solid #1a1a1a; }
        .buy { color: #00d4aa; }
        .sell { color: #ff4757; }
        .hold { color: #888; }
        .btn { background: #00d4aa; color: #000; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: 600; font-size: 0.85rem; margin-right: 10px; }
        .btn:hover { background: #00b894; }
        .btn.danger { background: #ff4757; }
        .btn.danger:hover { background: #e84343; }
        .controls { margin-bottom: 20px; }
    </style></head><body>
    <h1>CryptoTrader Dashboard</h1>
    <div class="controls">
        <button class="btn" onclick="loadData()">Actualizar</button>
        <button class="btn danger" onclick="resetPortfolio()">Reset Portfolio ($1,000)</button>
    </div>
    <div class="grid" id="stats"></div>
    <h2>Posiciones Abiertas</h2>
    <table id="positions"><thead><tr><th>Symbol</th><th>Lado</th><th>Cantidad</th><th>Entrada</th><th>PnL</th><th>Estrategia</th></tr></thead><tbody></tbody></table>
    <h2>Ultimas Operaciones</h2>
    <table id="trades"><thead><tr><th>Time</th><th>Symbol</th><th>Lado</th><th>PnL</th><th>Estrategia</th><th>Estado</th></tr></thead><tbody></tbody></table>
    <h2>Senales Recientes</h2>
    <table id="signals"><thead><tr><th>Time</th><th>Symbol</th><th>Senal</th><th>Confianza</th><th>Estrategia</th></tr></thead><tbody></tbody></table>
    <h2>Metricas por Estrategia</h2>
    <table id="metrics"><thead><tr><th>Estrategia</th><th>Trades</th><th>Win Rate</th><th>PnL Total</th><th>PnL Avg</th></tr></thead><tbody></tbody></table>
    <h2>Sentimiento de Mercado</h2>
    <table id="sentiment"><thead><tr><th>Indicador</th><th>Valor</th></tr></thead><tbody></tbody></table>
    <h2>Backtest</h2>
    <table id="backtest"><thead><tr><th>Estrategia</th><th>Symbol</th><th>PnL</th><th>Win Rate</th><th>Sharpe</th><th>MaxDD</th><th>Trades</th></tr></thead><tbody></tbody></table>
    <h2>Evolution Agent</h2>
    <table id="evolution"><thead><tr><th>Indicador</th><th>Valor</th></tr></thead><tbody></tbody></table>
    <script>
    async function loadData() {
        try {
            const [statsRes, tradesRes, signalsRes, metricsRes, sentimentRes, backtestRes, evolutionRes] = await Promise.all([
                fetch('/api/stats'), fetch('/api/trades?limit=20'), fetch('/api/signals?limit=20'), fetch('/api/metrics'),
                fetch('/api/sentiment'), fetch('/api/backtest'), fetch('/api/evolution')
            ]);
            const stats = await statsRes.json();
            const trades = await tradesRes.json();
            const signals = await signalsRes.json();
            const metrics = await metricsRes.json();
            const sentiment = await sentimentRes.json();
            const backtest = await backtestRes.json();
            const evolution = await evolutionRes.json();

            const pnlClass = stats.total_pnl >= 0 ? 'positive' : 'negative';
            const pnlPctClass = stats.total_pnl_pct >= 0 ? 'positive' : 'negative';
            document.getElementById('stats').innerHTML = `
                <div class="card"><div class="label">Valor Total</div><div class="value">$${stats.total_value?.toFixed(2) || '0.00'}</div></div>
                <div class="card"><div class="label">Efectivo</div><div class="value">$${stats.cash?.toFixed(2) || '0.00'}</div></div>
                <div class="card"><div class="label">PnL Total</div><div class="value ${pnlClass}">$${stats.total_pnl?.toFixed(2) || '0.00'}</div></div>
                <div class="card"><div class="label">PnL %</div><div class="value ${pnlPctClass}">${stats.total_pnl_pct?.toFixed(1) || '0.0'}%</div></div>
                <div class="card"><div class="label">Trades</div><div class="value">${stats.total_trades || 0}</div></div>
                <div class="card"><div class="label">Win Rate</div><div class="value">${stats.win_rate?.toFixed(1) || '0.0'}%</div></div>
                <div class="card"><div class="label">Posiciones</div><div class="value">${stats.open_positions || 0}</div></div>
                <div class="card"><div class="label">Fees</div><div class="value negative">$${stats.total_fees?.toFixed(2) || '0.00'}</div></div>
            `;

            if (stats.positions && Object.keys(stats.positions).length > 0) {
                const rows = Object.entries(stats.positions).map(([id, p]) => {
                    const pnlClass2 = (p.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';
                    return `<tr><td>${p.symbol}</td><td class="${p.side}">${p.side}</td><td>${parseFloat(p.quantity).toFixed(6)}</td><td>$${parseFloat(p.entry_price).toFixed(2)}</td><td class="${pnlClass2}">$${(p.unrealized_pnl || 0).toFixed(2)}</td><td>${p.strategy || '-'}</td></tr>`;
                }).join('');
                document.querySelector('#positions tbody').innerHTML = rows;
            } else {
                document.querySelector('#positions tbody').innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888">Sin posiciones</td></tr>';
            }

            document.querySelector('#trades tbody').innerHTML = trades.length > 0
                ? trades.map(t => `<tr><td>${new Date(t.time).toLocaleString()}</td><td>${t.symbol}</td><td class="${t.side}">${t.side}</td><td class="${t.pnl_usd >= 0 ? 'positive' : 'negative'}">$${t.pnl_usd?.toFixed(2)}</td><td>${t.strategy || '-'}</td><td>${t.status}</td></tr>`).join('')
                : '<tr><td colspan="6" style="text-align:center;color:#888">Sin operaciones</td></tr>';

            document.querySelector('#signals tbody').innerHTML = signals.length > 0
                ? signals.map(s => `<tr><td>${new Date(s.time).toLocaleString()}</td><td>${s.symbol}</td><td class="${s.signal}">${s.signal}</td><td>${(s.confidence * 100).toFixed(0)}%</td><td>${s.strategy || '-'}</td></tr>`).join('')
                : '<tr><td colspan="5" style="text-align:center;color:#888">Sin senales</td></tr>';

            document.querySelector('#metrics tbody').innerHTML = metrics.length > 0
                ? metrics.map(m => `<tr><td>${m.strategy}</td><td>${m.total_trades}</td><td>${m.win_rate.toFixed(1)}%</td><td class="${m.total_pnl >= 0 ? 'positive' : 'negative'}">$${m.total_pnl.toFixed(2)}</td><td>$${m.avg_pnl.toFixed(2)}</td></tr>`).join('')
                : '<tr><td colspan="5" style="text-align:center;color:#888">Sin datos</td></tr>';

            const sentRows = [];
            if (sentiment.fear_greed) sentRows.push(`<tr><td>Fear & Greed</td><td>${sentiment.fear_greed.value} (${sentiment.fear_greed.classification})</td></tr>`);
            if (sentiment.sentiment) sentRows.push(`<tr><td>Sentimiento</td><td>${sentiment.sentiment.sentiment_signal} (ajuste: ${sentiment.sentiment.confidence_adjustment})</td></tr>`);
            if (sentiment.funding_rates) {
                const rates = Object.entries(sentiment.funding_rates).map(([s,r]) => `${s}: ${r.toFixed(4)}%`).join(', ');
                sentRows.push(`<tr><td>Funding Rates</td><td>${rates}</td></tr>`);
            }
            if (sentiment.sentiment && sentiment.sentiment.reasoning) sentRows.push(`<tr><td>Razonamiento</td><td>${sentiment.sentiment.reasoning}</td></tr>`);
            document.querySelector('#sentiment tbody').innerHTML = sentRows.length > 0 ? sentRows.join('') : '<tr><td colspan="2" style="text-align:center;color:#888">Sin datos de sentimiento</td></tr>';

            const btResults = backtest.results || [];
            document.querySelector('#backtest tbody').innerHTML = btResults.length > 0
                ? btResults.map(b => `<tr><td>${b.strategy}</td><td>${b.symbol}</td><td class="${(b.total_pnl||0)>=0?'positive':'negative'}">$${(b.total_pnl||0).toFixed(2)}</td><td>${(b.win_rate||0).toFixed(1)}%</td><td>${(b.sharpe_ratio||0).toFixed(2)}</td><td>${(b.max_drawdown_pct||0).toFixed(1)}%</td><td>${b.total_trades||0}</td></tr>`).join('')
                : '<tr><td colspan="7" style="text-align:center;color:#888">Sin resultados de backtest</td></tr>';

            const evoRows = [];
            const lc = evolution.last_cycle;
            if (lc) {
                evoRows.push(`<tr><td>Ultimo ciclo</td><td>${new Date(lc.timestamp).toLocaleString()}</td></tr>`);
                evoRows.push(`<tr><td>Cambios aplicados</td><td>${lc.applied_count}</td></tr>`);
                evoRows.push(`<tr><td>Analisis</td><td>${lc.analysis || 'N/A'}</td></tr>`);
                if (lc.applied_changes) {
                    lc.applied_changes.forEach(c => {
                        evoRows.push(`<tr><td>${c.type} → ${c.target || c.reason || ''}</td><td>${c.reasoning || JSON.stringify(c.params || c) || ''}</td></tr>`);
                    });
                }
            }
            if (evolution.rollback) {
                evoRows.push(`<tr><td style="color:#ff4757">ROLLBACK</td><td>${evolution.rollback.reason} (${new Date(evolution.rollback.timestamp).toLocaleString()})</td></tr>`);
            }
            if (evolution.risk_params) {
                evoRows.push(`<tr><td>Risk params</td><td>max_pos=${evolution.risk_params.max_position_pct} max_dd=${evolution.risk_params.max_drawdown_pct} kelly=${evolution.risk_params.kelly_fraction}</td></tr>`);
            }
            if (evolution.strategy_params) {
                Object.entries(evolution.strategy_params).forEach(([s, p]) => {
                    evoRows.push(`<tr><td>${s} params</td><td>active=${p.active} sl=${p.atr_sl_multiplier} tp=${p.atr_tp_multiplier}</td></tr>`);
                });
            }
            document.querySelector('#evolution tbody').innerHTML = evoRows.length > 0 ? evoRows.join('') : '<tr><td colspan="2" style="text-align:center;color:#888">Sin datos de evolution</td></tr>';
        } catch(e) {
            document.getElementById('stats').innerHTML = '<div class="card" style="color:#ff4757">Error: ' + e.message + '</div>';
        }
    }
    async function resetPortfolio() {
        if (!confirm('Reset portfolio a $1,000? Se eliminaran todas las posiciones.')) return;
        try {
            const res = await fetch('/api/reset', { method: 'POST' });
            const data = await res.json();
            alert(data.message || 'Portfolio reseteado');
            loadData();
        } catch(e) { alert('Error: ' + e.message); }
    }
    loadData();
    setInterval(loadData, 10000);
    </script></body></html>
    """)


@app.get("/api/stats")
async def get_stats():
    try:
        stats = await redis.get_json("portfolio:stats")
        if not stats:
            stats = {
                "initial_capital": settings.INITIAL_CAPITAL,
                "total_value": settings.INITIAL_CAPITAL,
                "cash": settings.INITIAL_CAPITAL,
                "total_pnl": 0, "total_pnl_pct": 0,
                "open_positions": 0, "total_trades": 0,
                "winning_trades": 0, "losing_trades": 0,
                "win_rate": 0, "total_fees": 0, "total_slippage": 0,
            }
        portfolio = await redis.get_json("paper_trading:state")
        stats["positions"] = portfolio.get("positions", {}) if portfolio else {}
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


INSTANCES = {
    "main": {"state": "paper_trading:state", "stats": "portfolio:stats", "label": "Main (Consolidated)"},
    "highconf": {"state": "paper_trading:highconf", "stats": "portfolio:stats:highconf", "label": "High Confidence (>=90%)"},
    # A/B Test: Time Filter variants
    "main-tf": {"state": "paper_trading:main-tf", "stats": "portfolio:stats:main-tf", "label": "Main + TimeFilter (5,6,8,17,22h)"},
    "conservative-tf": {"state": "paper_trading:conservative-tf", "stats": "portfolio:stats:conservative-tf", "label": "Conservative + TimeFilter"},
    "highconf-tf": {"state": "paper_trading:highconf-tf", "stats": "portfolio:stats:highconf-tf", "label": "HighConf + TimeFilter"},
    "multitf-tf": {"state": "paper_trading:multitf-tf", "stats": "portfolio:stats:multitf-tf", "label": "MultiTF + TimeFilter"},
    "lowfreq-tf": {"state": "paper_trading:lowfreq-tf", "stats": "portfolio:stats:lowfreq-tf", "label": "LowFreq + TimeFilter"},
    "sentiment-tf": {"state": "paper_trading:sentiment-tf", "stats": "portfolio:stats:sentiment-tf", "label": "Sentiment + TimeFilter"},
}


@app.get("/api/portfolios")
async def get_portfolios():
    try:
        result = {}
        for name, cfg in INSTANCES.items():
            stats = await redis.get_json(cfg["stats"])
            if not stats:
                stats = {
                    "initial_capital": 1000, "total_value": 1000, "cash": 1000,
                    "total_pnl": 0, "total_pnl_pct": 0, "open_positions": 0,
                    "total_trades": 0, "win_rate": 0, "total_fees": 0,
                }
            result[name] = {
                "label": cfg["label"],
                "value": round(stats.get("total_value", 1000), 2),
                "cash": round(stats.get("cash", 1000), 2),
                "pnl": round(stats.get("total_pnl", 0), 2),
                "pnl_pct": round(stats.get("total_pnl_pct", 0), 2),
                "trades": stats.get("total_trades", 0),
                "win_rate": round(stats.get("win_rate", 0), 1),
                "positions": stats.get("open_positions", 0),
                "fees": round(stats.get("total_fees", 0), 2),
            }
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
async def get_trades(limit: int = Query(default=20, le=100)):
    try:
        rows = await db.fetch(
            "SELECT time, symbol, side, entry_price, exit_price, quantity, quantity_usd, fee_usd, pnl_usd, status, strategy, confidence FROM trades ORDER BY time DESC LIMIT $1", limit)
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/signals")
async def get_signals(limit: int = Query(default=20, le=100)):
    try:
        rows = await db.fetch(
            "SELECT time, symbol, timeframe, signal, confidence, strategy, reasoning, approved FROM signals ORDER BY time DESC LIMIT $1", limit)
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/metrics")
async def get_metrics():
    try:
        rows = await db.fetch(
            "SELECT strategy, COUNT(*) as total_trades, SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins, SUM(pnl_usd) as total_pnl, AVG(pnl_usd) as avg_pnl FROM trades WHERE status = 'closed' GROUP BY strategy ORDER BY total_pnl DESC")
        result = []
        for r in rows:
            total = int(r["total_trades"]) if r["total_trades"] else 0
            wins = int(r["wins"]) if r["wins"] else 0
            result.append({
                "strategy": r["strategy"] or "unknown",
                "total_trades": total,
                "win_rate": (wins / total * 100) if total > 0 else 0,
                "total_pnl": float(r["total_pnl"]) if r["total_pnl"] else 0,
                "avg_pnl": float(r["avg_pnl"]) if r["avg_pnl"] else 0,
            })
        return result
    except Exception:
        return []


@app.get("/api/portfolio/history")
async def get_portfolio_history(hours: int = Query(default=24, le=168)):
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = await db.fetch(
            "SELECT time, total_value_usd, cash_usd FROM portfolio_snapshots WHERE time > $1 ORDER BY time ASC", since)
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/ohlcv/{symbol}")
async def get_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100):
    try:
        rows = await db.fetch(
            "SELECT time, open, high, low, close, volume FROM ohlcv WHERE symbol = $1 AND timeframe = $2 ORDER BY time DESC LIMIT $3",
            symbol, timeframe, limit)
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.post("/api/reset")
async def reset_portfolio():
    try:
        initial = settings.INITIAL_CAPITAL
        await redis.set_json("paper_trading:state", {
            "cash": initial, "positions": {},
            "total_fees": 0, "total_slippage": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        await redis.set_json("portfolio:state", {
            "total_value": initial, "peak_value": initial,
            "positions": {}, "recent_losses": 0,
            "total_trades": 0, "winning_trades": 0,
            "cash_available": initial,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        await redis.set_json("portfolio:stats", {
            "initial_capital": initial, "total_value": initial,
            "cash": initial, "total_pnl": 0, "total_pnl_pct": 0,
            "open_positions": 0, "total_trades": 0,
            "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0, "total_fees": 0, "total_slippage": 0,
            "positions": {},
        })
        await redis.set_json("portfolio:orchestrator", {
            "cash": initial, "closed_pnl": 0, "positions": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"message": f"Portfolio reseteado a ${initial:.2f}", "status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/sentiment")
async def get_sentiment():
    try:
        fg = await redis.get_json("sentiment:fear_greed")
        rates = await redis.get_json("sentiment:funding_rates")
        current = await redis.get_json("sentiment:current")
        return {"fear_greed": fg, "funding_rates": rates, "sentiment": current}
    except Exception:
        return {"fear_greed": None, "funding_rates": None, "sentiment": None}


def _sanitize_floats(obj):
    import math
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


@app.get("/api/backtest")
async def get_backtest():
    try:
        latest = await redis.get_json("backtest:latest")
        if latest:
            return _sanitize_floats(latest)
        return {"message": "No backtest results yet"}
    except Exception:
        return {"message": "No backtest results"}


@app.post("/api/backtest/run")
async def run_backtest(strategy: str = Query(default="all"), symbol: str = Query(default="BTC/USDT"), days: int = Query(default=30)):
    return {"message": "Backtest runs automatically every 6 hours. Check /api/backtest for results."}


@app.get("/api/training/stats")
async def get_training_stats():
    try:
        stats = await redis.get_json("training:export_stats")
        return stats or {"message": "No training data exported yet"}
    except Exception:
        return {"message": "No training data"}


@app.get("/api/evolution")
async def get_evolution():
    try:
        last_cycle = await redis.get_json("evolution:last_cycle")
        rollback = await redis.get_json("evolution:rollback")
        current = await redis.get_json("evolution:current")
        risk_params = await redis.get_json("risk:params")
        strategy_params = {}
        for s in ["scalping", "swing", "arbitrage"]:
            p = await redis.get_json(f"strategy:params:{s}")
            c = await redis.get_json(f"strategy:config:{s}")
            strategy_params[s] = {**(p or {}), **(c or {})}
        return {
            "last_cycle": last_cycle,
            "rollback": rollback,
            "current_params": current,
            "risk_params": risk_params,
            "strategy_params": strategy_params,
        }
    except Exception:
        return {"last_cycle": None, "rollback": None}


@app.get("/api/watchdog")
async def get_watchdog():
    try:
        status = await redis.get_json("watchdog:status")
        last_restart = await redis.get_json("watchdog:last_restart")
        return {
            "status": status or {"last_check": None, "restarts": [], "total_restarts": 0},
            "last_restart": last_restart,
        }
    except Exception:
        return {"status": {"last_check": None, "restarts": [], "total_restarts": 0}, "last_restart": None}


@app.get("/api/monitoring")
async def get_monitoring():
    try:
        from pathlib import Path
        import json as _json

        log_dir = Path("/app/logs/monitoring")
        if not log_dir.exists():
            return {"snapshots": [], "trend": {}}

        snapshots = []
        for log_file in sorted(log_dir.glob("monitor_*.jsonl"))[-3:]:
            with open(log_file) as f:
                for line in f:
                    try:
                        snapshots.append(_json.loads(line.strip()))
                    except Exception:
                        pass

        snapshots = snapshots[-50:]

        trend = {}
        if len(snapshots) >= 2:
            first = snapshots[0]["portfolios"]
            last = snapshots[-1]["portfolios"]
            for name in last:
                if name in first:
                    trend[name] = {
                        "label": last[name]["label"],
                        "start_pnl": first[name]["pnl"],
                        "end_pnl": last[name]["pnl"],
                        "delta": round(last[name]["pnl"] - first[name]["pnl"], 2),
                        "snapshots": len(snapshots),
                    }

        return {"snapshots": snapshots[-10:], "trend": trend}
    except Exception:
        return {"snapshots": [], "trend": {}}


@app.get("/api/stop-loss-tracker")
async def get_stop_loss_tracker():
    try:
        tracked = await redis.get_json("stop_loss_tracker:tracked") or {}
        return {
            "tracked_count": len(tracked),
            "tracked": tracked,
        }
    except Exception:
        return {"tracked_count": 0, "tracked": {}}


@app.get("/api/analytics/time-performance")
async def get_time_performance():
    try:
        rows = await db.fetch("""
            SELECT 
                EXTRACT(HOUR FROM time) as hour,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(pnl_usd)::numeric, 2) as total_pnl,
                ROUND(AVG(pnl_usd)::numeric, 4) as avg_pnl
            FROM trades 
            WHERE status = 'closed' AND time > NOW() - INTERVAL '7 days'
            GROUP BY 1 ORDER BY 1
        """)
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/analytics/symbol-patterns")
async def get_symbol_patterns():
    try:
        rows = await db.fetch("""
            SELECT 
                symbol,
                strategy,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(100.0 * SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate,
                ROUND(SUM(pnl_usd)::numeric, 2) as total_pnl,
                ROUND(AVG(pnl_usd)::numeric, 4) as avg_pnl,
                ROUND(MIN(pnl_usd)::numeric, 4) as worst_trade,
                ROUND(MAX(pnl_usd)::numeric, 4) as best_trade
            FROM trades 
            WHERE status = 'closed' AND time > NOW() - INTERVAL '7 days'
            GROUP BY symbol, strategy
            HAVING COUNT(*) >= 3
            ORDER BY total_pnl DESC
        """)
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/analytics/win-streaks")
async def get_win_streaks():
    try:
        rows = await db.fetch("""
            SELECT time, symbol, strategy, pnl_usd, confidence
            FROM trades 
            WHERE status = 'closed' AND time > NOW() - INTERVAL '7 days'
            ORDER BY time ASC
        """)
        
        if not rows:
            return {"max_win_streak": 0, "max_loss_streak": 0, "current_streak": 0, "streak_type": "none"}
        
        max_win = 0
        max_loss = 0
        current_win = 0
        current_loss = 0
        
        for r in rows:
            if r["pnl_usd"] > 0:
                current_win += 1
                current_loss = 0
                max_win = max(max_win, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss = max(max_loss, current_loss)
        
        if current_win > 0:
            streak_type = "win"
            streak_count = current_win
        elif current_loss > 0:
            streak_type = "loss"
            streak_count = current_loss
        else:
            streak_type = "none"
            streak_count = 0
        
        return {
            "max_win_streak": max_win,
            "max_loss_streak": max_loss,
            "current_streak": streak_count,
            "streak_type": streak_type,
            "total_trades": len(rows),
        }
    except Exception:
        return {"max_win_streak": 0, "max_loss_streak": 0, "current_streak": 0, "streak_type": "none"}


@app.get("/api/analytics/slippage")
async def get_slippage_analysis():
    try:
        rows = await db.fetch("""
            SELECT 
                strategy,
                COUNT(*) as trades,
                ROUND(SUM(fee_usd)::numeric, 2) as total_fees,
                ROUND(AVG(fee_usd)::numeric, 4) as avg_fee,
                ROUND(SUM(quantity_usd * 0.001)::numeric, 2) as estimated_slippage,
                ROUND(SUM(pnl_usd)::numeric, 2) as total_pnl
            FROM trades 
            WHERE status = 'closed' AND time > NOW() - INTERVAL '7 days'
            GROUP BY strategy
            ORDER BY total_pnl DESC
        """)
        return [dict(r) for r in rows]
    except Exception:
        return []


@app.get("/api/alerts")
async def get_alerts():
    try:
        stream = await redis.client.xrevrange("alerts:critical", count=50)
        alerts = []
        for msg_id, fields in stream:
            alert = {"id": msg_id}
            for k, v in fields.items():
                try:
                    alert[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    alert[k] = v
            alerts.append(alert)
        return alerts
    except Exception:
        return []


@app.get("/api/circuit")
async def get_circuit_state():
    try:
        circuit = await redis.get_json("circuit:state") or {
            "status": "open",
            "reason": None,
            "resume_at": None,
        }
        history = await redis.client.lrange("circuit:history", 0, 19)
        parsed_history = []
        for h in history:
            try:
                parsed_history.append(json.loads(h))
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "state": circuit,
            "history": parsed_history,
        }
    except Exception:
        return {"state": {"status": "open"}, "history": []}
