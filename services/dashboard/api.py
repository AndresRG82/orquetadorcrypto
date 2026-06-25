import asyncio
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

NAUTILUS_ENABLED = settings.NAUTILUS_ENABLED

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
_hb_task: asyncio.Task | None = None

SERVICE_LIST = [
    "market-scanner", "qwen-analyzer", "strategy-scalping", "strategy-swing",
    "strategy-arbitrage", "risk-manager", "orchestrator", "paper-trading",
    "stop-loss", "stop-loss-tracker", "backtesting", "evolution-agent",
    "dashboard", "watchdog", "monitoring",
]


async def _heartbeat_loop():
    while True:
        try:
            if redis:
                await redis.heartbeat("dashboard")
        except Exception:
            pass
        await asyncio.sleep(30)


@app.on_event("startup")
async def startup():
    global redis, db, _hb_task
    redis = await RedisClient.get_instance()
    db = await Database.get_instance()
    _hb_task = asyncio.create_task(_heartbeat_loop())


@app.on_event("shutdown")
async def shutdown():
    global _hb_task
    if _hb_task:
        _hb_task.cancel()
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
        .portfolio-group { margin-bottom: 20px; }
        .portfolio-title { color: #00d4aa; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; padding: 4px 0; border-bottom: 1px solid #1a1a1a; }
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
    <table id="trades"><thead><tr><th>Time</th><th>Symbol</th><th>Lado</th><th>PnL</th><th>Estrategia</th><th>Estado</th><th>Venue</th></tr></thead><tbody></tbody></table>
    <h2>Senales Recientes</h2>
    <table id="signals"><thead><tr><th>Time</th><th>Symbol</th><th>Senal</th><th>Confianza</th><th>Estrategia</th></tr></thead><tbody></tbody></table>
    <h2>Metricas por Estrategia</h2>
    <table id="metrics"><thead><tr><th>Estrategia</th><th>Trades</th><th>Win Rate</th><th>PnL Total</th><th>PnL Avg</th></tr></thead><tbody></tbody></table>
    <h2>Sentimiento de Mercado</h2>
    <table id="sentiment"><thead><tr><th>Indicador</th><th>Valor</th></tr></thead><tbody></tbody></table>
    <h2>Backtest</h2>
    <table id="backtest"><thead><tr><th>Estrategia</th><th>Symbol</th><th>PnL</th><th>Win Rate</th><th>Sharpe</th><th>Sortino</th><th>MaxDD</th><th>Trades</th><th>Beta</th><th>Trend</th></tr></thead><tbody></tbody></table>
    <h2>Evolution Agent</h2>
    <table id="evolution"><thead><tr><th>Indicador</th><th>Valor</th></tr></thead><tbody></tbody></table>
    <h2>Nautilus vs VectorBT</h2>
    <table id="nautilus"><thead><tr><th>Estrategia</th><th>Symbol</th><th>TF</th><th>Sharpe Nautilus</th><th>Sharpe VBT</th><th>Diff</th><th>PnL Nautilus</th><th>Trades Nautilus</th></tr></thead><tbody></tbody></table>
    <h2>Swarm Coordinator</h2>
    <table id="swarm"><thead><tr><th>Indicador</th><th>Valor</th></tr></thead><tbody></tbody></table>
    <h2>Estado de Servicios</h2>
    <table id="services"><thead><tr><th>Servicio</th><th>Estado</th><th>Ultimo heartbeat</th><th>Edad (s)</th></tr></thead><tbody></tbody></table>
    <script>
    async function loadData() {
        try {
            const [statsRes, portfoliosRes, tradesRes, signalsRes, metricsRes, sentimentRes, backtestRes, evolutionRes, attributionRes, nautilusRes, swarmRes, servicesRes] = await Promise.all([
                fetch('/api/stats'), fetch('/api/portfolios'), fetch('/api/trades?limit=20'), fetch('/api/signals?limit=20'), fetch('/api/metrics'),
                fetch('/api/sentiment'), fetch('/api/backtest'), fetch('/api/evolution'),
                fetch('/api/backtest/attribution'), fetch('/api/nautilus'), fetch('/api/swarm'),
                fetch('/api/services')
            ]);
            const stats = await statsRes.json();
            const portfolios = await portfoliosRes.json();
            const trades = await tradesRes.json();
            const signals = await signalsRes.json();
            const metrics = await metricsRes.json();
            const sentiment = await sentimentRes.json();
            const backtest = await backtestRes.json();
            const attribution = await attributionRes.json();
            const nautilus = await nautilusRes.json();
            const swarm = await swarmRes.json();
            const evolution = await evolutionRes.json();

            const attrMap = {};
            if (attribution && attribution.results) {
                attribution.results.forEach(a => {
                    const key = a.strategy + '|' + a.symbol;
                    attrMap[key] = a.attribution_summary || {};
                });
            }

            const portfolioCards = [];
            if (portfolios) {
                Object.entries(portfolios).forEach(([key, p]) => {
                    portfolioCards.push(
                        `<div class="portfolio-group"><div class="portfolio-title">${p.label}</div><div class="grid">` +
                        `<div class="card"><div class="label">Valor</div><div class="value">$${(p.value||0).toFixed(2)}</div>` +
                        `${p.cash ? `<div style="font-size:0.7rem;color:#666">Cash: $${p.cash.toFixed(2)}</div>` : ''}</div>` +
                        `<div class="card"><div class="label">PnL</div><div class="value ${(p.pnl||0)>=0?'positive':'negative'}">` +
                        `${(p.pnl||0)>=0?'+':''}$${(p.pnl||0).toFixed(2)}</div><div style="font-size:0.75rem;color:#666">` +
                        `${(p.pnl_pct||0).toFixed(1)}%</div></div>` +
                        `<div class="card"><div class="label">Trades</div><div class="value">${p.trades||0}</div></div>` +
                        `<div class="card"><div class="label">Win Rate</div><div class="value">${(p.win_rate||0).toFixed(1)}%</div></div>` +
                        `<div class="card"><div class="label">Posiciones</div><div class="value">${p.positions||0}</div></div>` +
                        `</div></div>`
                    );
                });
            }
            document.getElementById('stats').innerHTML = portfolioCards.join('');

            const posHtml = stats.positions ? Object.values(stats.positions).map(p => {
                const upnl = p.current_value ? (p.current_value - (p.quantity||0)*(p.entry_price||0)) : null;
                const pnlStr = upnl !== null ? `<span class="${upnl>=0?'positive':'negative'}">${upnl>=0?'+':''}$${upnl.toFixed(2)}</span>` : '-';
                return `<tr><td>${p.symbol}</td><td class="${p.side}">${p.side}</td><td>${(p.quantity||0).toFixed(4)}</td><td>$${(p.entry_price||0).toFixed(4)}</td><td>${pnlStr}</td><td>${p.strategy||'-'}</td></tr>`;
            }).join('') : '';
            document.querySelector('#positions tbody').innerHTML = posHtml || '<tr><td colspan="6" style="text-align:center;color:#888">Sin posiciones abiertas</td></tr>';

            document.querySelector('#trades tbody').innerHTML = (trades||[]).length > 0
                ? trades.map(t => `<tr><td>${new Date(t.time).toLocaleTimeString()}</td><td>${t.symbol}</td><td class="${t.side}">${t.side}</td><td class="${(t.pnl_usd||0)>=0?'positive':'negative'}">${t.pnl_usd ? '$'+t.pnl_usd.toFixed(2) : '-'}</td><td>${t.strategy||'-'}</td><td>${t.status||'-'}</td><td style="font-size:0.7rem;color:#888">${t.venue||'-'}</td></tr>`).join('')
                : '<tr><td colspan="7" style="text-align:center;color:#888">Sin operaciones</td></tr>';

            document.querySelector('#signals tbody').innerHTML = (signals||[]).length > 0
                ? signals.map(s => `<tr><td>${new Date(s.time).toLocaleTimeString()}</td><td>${s.symbol}</td><td class="${s.signal}">${s.signal}</td><td>${(s.confidence||0).toFixed(2)}</td><td>${s.strategy||'-'}</td></tr>`).join('')
                : '<tr><td colspan="5" style="text-align:center;color:#888">Sin senales</td></tr>';

            document.querySelector('#metrics tbody').innerHTML = (metrics||[]).length > 0
                ? metrics.map(m => `<tr><td>${m.strategy}</td><td>${m.total_trades}</td><td>${(m.win_rate||0).toFixed(1)}%</td><td class="${(m.total_pnl||0)>=0?'positive':'negative'}">$${(m.total_pnl||0).toFixed(2)}</td><td class="${(m.avg_pnl||0)>=0?'positive':'negative'}">$${(m.avg_pnl||0).toFixed(2)}</td></tr>`).join('')
                : '<tr><td colspan="5" style="text-align:center;color:#888">Sin metricas</td></tr>';

            const sentRows = [];
            if (sentiment && sentiment.fear_greed) { sentRows.push(`<tr><td>Fear & Greed</td><td>${sentiment.fear_greed.value||'-'} (${sentiment.fear_greed.classification||'-'})</td></tr>`); }
            if (sentiment && sentiment.sentiment) {
                Object.entries(sentiment.sentiment).forEach(([k,v]) => sentRows.push(`<tr><td>${k}</td><td>${typeof v==='object'?JSON.stringify(v):v}</td></tr>`));
            }
            if (sentiment && sentiment.funding_rates) { sentRows.push(`<tr><td>Funding Rates</td><td>${JSON.stringify(sentiment.funding_rates)}</td></tr>`); }
            document.querySelector('#sentiment tbody').innerHTML = sentRows.length > 0 ? sentRows.join('') : '<tr><td colspan="2" style="text-align:center;color:#888">Sin datos de sentimiento</td></tr>';

            const btResults = backtest.results || [];
            document.querySelector('#backtest tbody').innerHTML = btResults.length > 0
                ? btResults.map(b => {
                    const attr = attrMap[b.strategy + '|' + b.symbol] || {};
                    return `<tr><td>${b.strategy}</td><td>${b.symbol}</td><td class="${(b.total_pnl||0)>=0?'positive':'negative'}">$${(b.total_pnl||0).toFixed(2)}</td><td>${(b.win_rate||0).toFixed(1)}%</td><td>${(b.sharpe_ratio||0).toFixed(2)}</td><td>${attr.sortino !== undefined ? attr.sortino.toFixed(2) : '-'}</td><td>${(b.max_drawdown_pct||0).toFixed(1)}%</td><td>${b.total_trades||0}</td><td>${attr.benchmark_beta !== undefined ? attr.benchmark_beta.toFixed(2) : '-'}</td><td>${attr.current_trend || '-'}</td></tr>`;
                }).join('')
                : '<tr><td colspan="10" style="text-align:center;color:#888">Sin resultados de backtest</td></tr>';

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

            const nautRows = (nautilus.results || []).map(r => {
                const c = r.comparison || {};
                return `<tr><td>${r.nautilus?.strategy || '-'}</td><td>${r.nautilus?.symbol || '-'}</td><td>${r.nautilus?.timeframe || '-'}</td><td>${(c.sharpe_nautilus || 0).toFixed(2)}</td><td>${(c.sharpe_vectorbt || 0).toFixed(2)}</td><td class="${(c.sharpe_diff||0)>=0?'positive':'negative'}">${(c.sharpe_diff||0).toFixed(2)}</td><td class="${(c.pnl_nautilus||0)>=0?'positive':'negative'}">$${(c.pnl_nautilus||0).toFixed(2)}</td><td>${c.trades_nautilus || 0}</td></tr>`;
            }).join('');
            document.querySelector('#nautilus tbody').innerHTML = nautRows || '<tr><td colspan="8" style="text-align:center;color:#888">Sin datos Nautilus</td></tr>';

            const swarmRows = swarm.timestamp ? [
                `<tr><td>Market Outlook</td><td class="${swarm.market_outlook==='alcista'?'positive':swarm.market_outlook==='bajista'?'negative':'hold'}">${swarm.market_outlook || '-'}</td></tr>`,
                `<tr><td>Confianza</td><td>${(swarm.confidence_adjustment || 1.0).toFixed(2)}x</td></tr>`,
                `<tr><td>Riesgo</td><td>${swarm.risk_adjustment || '-'}</td></tr>`,
                `<tr><td>Kelly</td><td>${(swarm.kelly_fraction_suggested || 0).toFixed(2)}</td></tr>`,
                `<tr><td>Ajustes</td><td>${swarm.param_adjustments || '-'}</td></tr>`,
                `<tr><td>Razonamiento</td><td>${swarm.reasoning || '-'}</td></tr>`,
                `<tr><td>Ultima actualizacion</td><td>${swarm.timestamp ? new Date(swarm.timestamp).toLocaleString() : '-'}</td></tr>`
            ].join('') : '<tr><td colspan="2" style="text-align:center;color:#888">Sin datos del Swarm</td></tr>';
            document.querySelector('#swarm tbody').innerHTML = swarmRows;

            const services = await servicesRes.json();
            const svcRows = Object.entries(services).map(([name, s]) =>
                `<tr><td>${name}</td><td style="color:${s.status==='up'?'#00d4aa':s.status==='stale'?'#ffa502':'#ff4757'}">${s.status}</td><td>${s.last_seen ? new Date(s.last_seen).toLocaleTimeString() : '-'}</td><td>${s.age_seconds !== null ? s.age_seconds+'s' : '-'}</td></tr>`
            ).join('');
            document.querySelector('#services tbody').innerHTML = svcRows;
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
        trade_stats = await db.fetch(
            "SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE pnl_usd > 0) as wins, COUNT(*) FILTER (WHERE pnl_usd < 0) as losses FROM trades WHERE status = 'closed'"
        )
        if trade_stats and trade_stats[0]["total"]:
            t = trade_stats[0]
            stats["total_trades"] = t["total"]
            stats["winning_trades"] = t["wins"]
            stats["losing_trades"] = t["losses"]
            stats["win_rate"] = round(t["wins"] / t["total"] * 100, 1) if t["total"] > 0 else 0
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


INSTANCES = {
    "main": {"type": "redis", "state": "paper_trading:state", "stats": "portfolio:stats", "label": "Paper Trading"},
}

VENUE_INSTANCES = {
    "bybit_testnet": {"label": "Bybit Testnet"},
    # future: "binance_paper": {"label": "Binance Paper"},
    # future: "bybit_live": {"label": "Bybit Live"},
}


@app.get("/api/portfolios")
async def get_portfolios():
    try:
        result = {}

        # --- Redis-backed instances (paper trading engine) ---
        for name, cfg in INSTANCES.items():
            stats = await redis.get_json(cfg["stats"])
            if not stats:
                stats = {
                    "initial_capital": 1000, "total_value": 1000, "cash": 1000,
                    "total_pnl": 0, "total_pnl_pct": 0, "open_positions": 0,
                    "total_trades": 0, "win_rate": 0, "total_fees": 0,
                }
            trade_stats = await db.fetch(
                "SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE pnl_usd > 0) as wins FROM trades WHERE status = 'closed' AND venue = 'paper'"
            )
            db_trades = trade_stats[0]["total"] if trade_stats and trade_stats[0]["total"] else 0
            db_wins = trade_stats[0]["wins"] if trade_stats else 0
            db_win_rate = round(db_wins / db_trades * 100, 1) if db_trades > 0 else 0

            result[name] = {
                "label": cfg["label"],
                "value": round(stats.get("total_value", 1000), 2),
                "cash": round(stats.get("cash", 1000), 2),
                "pnl": round(stats.get("total_pnl", 0), 2),
                "pnl_pct": round(stats.get("total_pnl_pct", 0), 2),
                "trades": db_trades,
                "win_rate": db_win_rate,
                "positions": stats.get("open_positions", 0),
                "fees": round(stats.get("total_fees", 0), 2),
            }

        # --- DB-backed instances (exchange venues) ---
        for venue, vcfg in VENUE_INSTANCES.items():
            vt = await db.fetch(
                "SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE pnl_usd > 0) as wins, "
                "COALESCE(SUM(pnl_usd), 0) as total_pnl, COALESCE(SUM(fee_usd), 0) as total_fees "
                "FROM trades WHERE status = 'closed' AND venue = $1", venue
            )
            if vt:
                v = vt[0]
                v_total = v["total"] if v["total"] else 0
                v_wins = v["wins"] if v["wins"] else 0
                v_pnl = float(v["total_pnl"]) if v["total_pnl"] else 0
                v_fees = float(v["total_fees"]) if v["total_fees"] else 0
            else:
                v_total = v_wins = 0
                v_pnl = v_fees = 0.0

            # Initial capital for this venue (stored by engine on startup)
            v_capital_key = f"portfolio:initial_capital:{venue}"
            v_capital = await redis.get_json(v_capital_key)
            initial = float(v_capital) if v_capital else 1000.0

            result[venue] = {
                "label": vcfg["label"],
                "value": round(initial + v_pnl, 2),
                "cash": 0,
                "pnl": round(v_pnl, 2),
                "pnl_pct": round(v_pnl / initial * 100, 2) if initial > 0 else 0,
                "trades": v_total,
                "win_rate": round(v_wins / v_total * 100, 1) if v_total > 0 else 0,
                "positions": 0,
                "fees": round(v_fees, 2),
            }

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
async def get_trades(limit: int = Query(default=20, le=100), venue: str = Query(default="")):
    try:
        if venue:
            rows = await db.fetch(
                "SELECT time, symbol, side, entry_price, exit_price, quantity, quantity_usd, fee_usd, pnl_usd, status, strategy, confidence, venue FROM trades WHERE venue = $1 ORDER BY time DESC LIMIT $2", venue, limit)
        else:
            rows = await db.fetch(
                "SELECT time, symbol, side, entry_price, exit_price, quantity, quantity_usd, fee_usd, pnl_usd, status, strategy, confidence, venue FROM trades ORDER BY time DESC LIMIT $1", limit)
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


SERVICE_STALE_SECONDS = {
    "stop-loss": 30, "stop-loss-tracker": 30, "market-scanner": 600,
    "dashboard": 60, "monitoring": 120, "watchdog": 300,
    "backtesting": 24000, "evolution-agent": 24000,
    "orchestrator": 120, "qwen-analyzer": 60, "paper-trading": 30,
    "risk-manager": 30, "strategy-scalping": 30, "strategy-swing": 30,
    "strategy-arbitrage": 30,
}
DEFAULT_STALE = 30


@app.get("/api/services")
async def get_services():
    try:
        result = {}
        now = datetime.now(timezone.utc)
        for name in SERVICE_LIST:
            hb = await redis.get_json(f"service:heartbeat:{name}")
            if hb and hb.get("last_seen"):
                last = datetime.fromisoformat(hb["last_seen"])
                age = (now - last).total_seconds()
                stale_after = SERVICE_STALE_SECONDS.get(name, DEFAULT_STALE)
                status = "down" if age > stale_after * 3 else ("stale" if age > stale_after else "up")
                result[name] = {"status": status, "last_seen": hb["last_seen"], "age_seconds": round(age, 1)}
            else:
                result[name] = {"status": "unknown", "last_seen": None, "age_seconds": None}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


@app.get("/api/backtest/attribution")
async def get_backtest_attribution(strategy: str = Query(default=""), symbol: str = Query(default="")):
    try:
        latest = await redis.get_json("backtest:latest")
        if not latest or "results" not in latest:
            return {"message": "No backtest results yet"}
        results = latest["results"]
        if strategy:
            results = [r for r in results if r.get("strategy") == strategy]
        if symbol:
            results = [r for r in results if r.get("symbol") == symbol]
        enriched = []
        for r in results:
            entry = {k: v for k, v in r.items() if k != "attribution"}
            entry["has_attribution"] = "attribution" in r and bool(r["attribution"])
            enriched.append(entry)
            if entry["has_attribution"]:
                entry["attribution_summary"] = {
                    "sharpe": r["attribution"].get("stats", {}).get("sharpe_ratio"),
                    "sortino": r["attribution"].get("stats", {}).get("sortino_ratio"),
                    "calmar": r["attribution"].get("stats", {}).get("calmar_ratio"),
                    "profit_factor": r["attribution"].get("stats", {}).get("profit_factor"),
                    "benchmark_beta": r["attribution"].get("beta", {}).get("benchmark_beta"),
                    "jensen_alpha": r["attribution"].get("beta", {}).get("jensen_alpha_annualized"),
                    "current_trend": r["attribution"].get("regime", {}).get("current_trend"),
                    "vol_regime": r["attribution"].get("regime", {}).get("vol_regime"),
                }
        return {"results": enriched, "total": len(enriched)}
    except Exception as e:
        return {"message": f"Error: {e}"}


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


@app.get("/api/nautilus")
async def get_nautilus_results():
    try:
        latest = await redis.get_json("nautilus:latest")
        if not latest:
            return {"message": "Nautilus not yet run", "enabled": NAUTILUS_ENABLED}
        keys = await redis.client.keys("nautilus:*")
        results = []
        for key in keys:
            if key in (b"nautilus:latest",):
                continue
            data = await redis.get_json(key)
            if data:
                results.append(data)
        return {"latest": latest, "results": results, "enabled": True}
    except Exception as e:
        return {"message": str(e), "enabled": False}


@app.get("/api/swarm")
async def get_swarm_status():
    try:
        latest = await redis.get_json("swarm:latest")
        if not latest:
            return {"message": "Swarm not yet run"}
        result = {
            "timestamp": latest.get("timestamp"),
            "market_outlook": latest.get("market_outlook"),
            "risk_adjustment": latest.get("risk_adjustment"),
            "confidence_adjustment": latest.get("confidence_adjustment"),
            "kelly_fraction_suggested": latest.get("kelly_fraction_suggested"),
            "param_adjustments": latest.get("param_adjustments"),
            "reasoning": latest.get("reasoning"),
        }
        return result
    except Exception as e:
        return {"message": str(e)}
