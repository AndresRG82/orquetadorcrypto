import asyncio
import json
import logging
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.models import TechnicalIndicators, TradingSignal, SignalType, QwenFeedback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("qwen-analyzer")

DEGRADATION_LEVELS = {
    "normal": {"gpu_threshold": 0.70, "batch_size": 3, "sleep_s": 1},
    "reduced": {"gpu_threshold": 0.85, "batch_size": 2, "sleep_s": 5},
    "minimal": {"gpu_threshold": 1.00, "batch_size": 1, "sleep_s": 15},
}

SYSTEM_PROMPT = """Crypto trading analyst. Output ONLY JSON: {"signal":"buy"|"sell"|"hold","confidence":0.0-1.0,"reasoning":"brief","target_price":null,"stop_loss":null}

Rules:
- Default to hold when signals mixed
- Only buy/sell when multiple indicators align
- confidence 0-1, reasoning max 10 words"""

MINI_PROMPT = "Output JSON: {\"signal\":\"buy\"|\"sell\"|\"hold\",\"confidence\":0.7,\"reasoning\":\"brief\",\"target_price\":null,\"stop_loss\":null}"

RETRY_PROMPT = "Output ONLY valid JSON: {\"signal\":\"buy\"|\"sell\"|\"hold\",\"confidence\":0.7,\"reasoning\":\"brief\"}"


class AnalyzeRequest(BaseModel):
    symbol: str
    timeframe: str
    close: float
    technical_score: int = 0
    technical_reasoning: str = ""


class AnalyzeResponse(BaseModel):
    signal: str
    confidence: float
    reasoning: str
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None


ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
        "target_price": {"type": ["number", "null"]},
        "stop_loss": {"type": ["number", "null"]},
    },
    "required": ["signal", "confidence", "reasoning"],
}


class QwenAnalyzer:
    _instance = None

    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.ollama_host = settings.OLLAMA_HOST
        self.model = settings.OLLAMA_MODEL
        self.prompt_version = "v2.0-qwen25"
        self.feedback_history: list[dict] = []
        self.running = False
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(2)
        self._analysis_cache: dict[str, tuple[datetime, dict]] = {}
        self._cache_ttl = {"1m": 30, "5m": 60, "15m": 120, "1h": 300, "4h": 600, "1d": 3600, "1w": 7200}
        self._degradation_level = "normal"
        self._gpu_load = 0.0

    def get_gpu_load(self) -> float:
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                timeout=3
            )
            return float(out.strip()) / 100.0
        except Exception:
            return 0.5

    def get_degradation_level(self, gpu_load: float) -> str:
        if gpu_load < DEGRADATION_LEVELS["normal"]["gpu_threshold"]:
            return "normal"
        elif gpu_load < DEGRADATION_LEVELS["reduced"]["gpu_threshold"]:
            return "reduced"
        else:
            return "minimal"

    def get_batch_size(self) -> int:
        return DEGRADATION_LEVELS[self._degradation_level]["batch_size"]

    def get_sleep_seconds(self) -> int:
        return DEGRADATION_LEVELS[self._degradation_level]["sleep_s"]

    @classmethod
    def get_instance(cls) -> "QwenAnalyzer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        logger.info(f"Qwen Analyzer initialized, model: {self.model}, host: {self.ollama_host}")
        await self.load_feedback_context()

    async def load_feedback_context(self):
        try:
            rows = await self.db.fetch("SELECT insights, analysis_correct, pnl FROM qwen_feedback ORDER BY time DESC LIMIT 20")
            self.feedback_history = [dict(r) for r in rows]
            logger.info(f"Loaded {len(self.feedback_history)} feedback entries")
        except Exception as e:
            logger.warning(f"Could not load feedback: {e}")
            self.feedback_history = []

    async def get_sentiment_context(self) -> str:
        try:
            sentiment = await self.redis.get_json("sentiment:current")
            if sentiment:
                fg = sentiment.get("fear_greed_value", 50)
                fg_class = sentiment.get("fear_greed_classification", "Neutral")
                sig = sentiment.get("sentiment_signal", "neutral")
                adj = sentiment.get("confidence_adjustment", 0.0)
                funding = sentiment.get("avg_funding_rate", 0.0)
                reasoning = sentiment.get("reasoning", "")
                return f"Sentiment: F&G={fg}({fg_class}) funding={funding:.4f}%"
        except Exception:
            pass
        return ""

    def build_analysis_prompt(self, indicators: TechnicalIndicators, sentiment_ctx: str = "") -> str:
        parts = [f"{indicators.symbol} {indicators.timeframe} price={indicators.close:.6f}"]
        field_map = [
            ("rsi_14", "RSI", ".1f"), ("macd_hist", "MACD_H", ".4f"),
            ("bb_upper", "BB_Up", ".4f"), ("bb_lower", "BB_Lo", ".4f"),
            ("ema_9", "E9", ".4f"), ("ema_21", "E21", ".4f"),
            ("ema_50", "E50", ".4f"), ("atr_14", "ATR", ".4f"),
        ]
        vals = []
        for field, label, fmt in field_map:
            val = getattr(indicators, field, None)
            if val is not None:
                vals.append(f"{label}={val:{fmt}}")
        parts.append(" ".join(vals))
        if indicators.price_change_pct is not None:
            parts.append(f"chg={indicators.price_change_pct:+.2f}%")
        if self.feedback_history:
            recent = self.feedback_history[-3:]
            fb_summary = "; ".join(f"{'OK' if fb.get('analysis_correct') else 'ERR'} ${fb.get('pnl',0):.2f}" for fb in recent)
            parts.append(f"FB: {fb_summary}")
        if sentiment_ctx:
            parts.append(sentiment_ctx.strip())
        parts.append("JSON:")
        return " ".join(parts)

    def build_quick_prompt(self, req: AnalyzeRequest) -> str:
        return f"Quick analysis: {req.symbol} on {req.timeframe}\nCurrent price: {req.close:.6f}\nTechnical score: {req.technical_score}\nTechnical reasoning: {req.technical_reasoning}\n\nOutput JSON:"

    def extract_json(self, raw: str) -> Optional[str]:
        text = raw.strip()
        if not text:
            return None
        if text.startswith("```json"):
            text = text[len("```json"):].strip()
        elif text.startswith("```"):
            text = text[len("```"):].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        return None

    async def query_ollama(self, prompt: str, system: str = SYSTEM_PROMPT, timeout: float = 30.0, retries: int = 1, use_schema: bool = True) -> Optional[dict]:
        models_to_try = [self.model] + [m for m in settings.OLLAMA_FALLBACK_MODELS if m != self.model]
        async with self._semaphore:
            for model in models_to_try:
                for attempt in range(retries + 1):
                    try:
                        current_prompt = prompt if attempt == 0 else f"{RETRY_PROMPT}\n\n{prompt}"
                        current_system = system if attempt == 0 else MINI_PROMPT
                        async with httpx.AsyncClient(timeout=timeout + 15) as client:
                            payload = {
                                "model": model,
                                "prompt": current_prompt,
                                "system": current_system,
                                "stream": False,
                                "options": {"temperature": 0.2, "top_p": 0.9, "num_predict": 350},
                            }
                            if use_schema:
                                payload["format"] = ANALYZE_SCHEMA
                            response = await client.post(f"{self.ollama_host}/api/generate", json=payload)
                            response.raise_for_status()
                            data = response.json()
                            raw_response = data.get("response", "")
                            json_str = self.extract_json(raw_response)
                            if json_str is None:
                                if attempt < retries:
                                    logger.warning(f"{model}: response not JSON (attempt {attempt+1}), retrying...")
                                    await asyncio.sleep(1)
                                    continue
                                logger.warning(f"{model}: failed to parse after {attempt+1} attempts, raw: {raw_response[:150]}")
                                break
                            try:
                                parsed = json.loads(json_str)
                                if parsed.get("signal") is None or parsed.get("confidence") is None:
                                    logger.warning(f"{model}: returned null fields: {json_str[:200]}")
                                    if attempt < retries:
                                        continue
                                    break
                                return parsed
                            except json.JSONDecodeError:
                                if attempt < retries:
                                    await asyncio.sleep(1)
                                    continue
                                break
                    except httpx.TimeoutException:
                        logger.warning(f"{model}: timeout (attempt {attempt+1})")
                        if attempt < retries:
                            await asyncio.sleep(2)
                        continue
                    except Exception as e:
                        logger.error(f"{model}: query error: {e}")
                        break
                logger.warning(f"All attempts failed for {model}, trying next model...")
        return None

    def get_cache_key(self, indicators: TechnicalIndicators) -> str:
        return f"{indicators.symbol}:{indicators.timeframe}:{indicators.close:.4f}"

    def check_cache(self, key: str, timeframe: str) -> Optional[dict]:
        if key in self._analysis_cache:
            cached_time, cached_result = self._analysis_cache[key]
            ttl = self._cache_ttl.get(timeframe, 300)
            if (datetime.now(timezone.utc) - cached_time).total_seconds() < ttl:
                return cached_result
            del self._analysis_cache[key]
        return None

    def set_cache(self, key: str, result: dict):
        self._analysis_cache[key] = (datetime.now(timezone.utc), result)
        if len(self._analysis_cache) > 300:
            oldest_keys = sorted(self._analysis_cache.keys(), key=lambda k: self._analysis_cache[k][0])[:100]
            for k in oldest_keys:
                del self._analysis_cache[k]

    async def analyze_indicators(self, indicators: TechnicalIndicators) -> Optional[TradingSignal]:
        cache_key = self.get_cache_key(indicators)
        cached = self.check_cache(cache_key, indicators.timeframe)
        if cached:
            try:
                signal = TradingSignal(**cached)
                signal.signal_id = str(uuid.uuid4())
                signal.timestamp = datetime.now(timezone.utc)
                return signal
            except Exception:
                pass

        sentiment_ctx = await self.get_sentiment_context()
        async with self._lock:
            prompt = self.build_analysis_prompt(indicators, sentiment_ctx)
            result = await self.query_ollama(prompt, retries=1)

        if result is None:
            return None

        try:
            raw_signal = result.get("signal")
            signal_type = SignalType(raw_signal.lower()) if raw_signal and isinstance(raw_signal, str) else SignalType.HOLD
        except ValueError:
            signal_type = SignalType.HOLD

        raw_conf = result.get("confidence")
        try:
            confidence = min(1.0, max(0.0, float(raw_conf))) if raw_conf is not None else 0.5
        except (TypeError, ValueError):
            confidence = 0.5
        sentiment = await self.redis.get_json("sentiment:current") if self.redis else None
        if sentiment:
            adj = float(sentiment.get("confidence_adjustment", 0.0))
            fg_signal = sentiment.get("sentiment_signal", "neutral")
            if fg_signal in ("contrarian_buy", "contrarian_sell"):
                confidence = min(1.0, max(0.0, confidence + adj))
            elif fg_signal in ("cautious_buy", "cautious_sell"):
                confidence = min(1.0, max(0.0, confidence + adj))
        reasoning = result.get("reasoning", "No reasoning provided")
        try:
            target_price = float(result["target_price"]) if result.get("target_price") is not None else None
        except (TypeError, ValueError, KeyError):
            target_price = None
        try:
            stop_loss = float(result["stop_loss"]) if result.get("stop_loss") is not None else None
        except (TypeError, ValueError, KeyError):
            stop_loss = None

        signal = TradingSignal(
            signal_id=str(uuid.uuid4()),
            symbol=indicators.symbol, timeframe=indicators.timeframe,
            timestamp=datetime.now(timezone.utc), signal=signal_type,
            confidence=confidence, strategy="qwen_direct", reasoning=reasoning,
            entry_price=indicators.close, target_price=target_price, stop_loss=stop_loss,
            indicators_snapshot=indicators,
        )

        if signal.signal != SignalType.HOLD:
            await self.redis.publish(settings.STREAM_SIGNALS, signal.model_dump(mode="json"))
            self.set_cache(cache_key, signal.model_dump(mode="json"))
            logger.info(f"Qwen Signal: {signal.signal.value} {signal.symbol} {indicators.timeframe} conf={signal.confidence:.2f}")

        return signal

    async def analyze_quick(self, req: AnalyzeRequest) -> Optional[dict]:
        prompt = self.build_quick_prompt(req)
        async with self._lock:
            result = await self.query_ollama(prompt, system=MINI_PROMPT, timeout=15.0, retries=0)
        if result is None:
            return {"signal": "hold", "confidence": 0.3, "reasoning": "Qwen analysis unavailable"}
        return result

    async def background_analyzer(self):
        logger.info("Starting background analyzer loop")
        group = "qwen-analyzer"
        consumer = f"analyzer-{uuid.uuid4().hex[:8]}"
        while self.running:
            try:
                self._gpu_load = self.get_gpu_load()
                self._degradation_level = self.get_degradation_level(self._gpu_load)
                batch_size = self.get_batch_size()
                sleep_s = self.get_sleep_seconds()

                await self.redis.client.set("llm:degradation_level", json.dumps({
                    "level": self._degradation_level,
                    "gpu_load": round(self._gpu_load, 3),
                    "batch_size": batch_size,
                }))

                messages = await self.redis.read_stream(settings.STREAM_INDICATORS, group, consumer, count=batch_size, block=5000)
                for msg_id, data in messages:
                    try:
                        indicators = TechnicalIndicators(**data)
                        should_analyze = self._pre_filter(indicators)
                        if should_analyze:
                            asyncio.create_task(self.analyze_indicators(indicators))
                    except Exception as e:
                        logger.error(f"Error processing indicator message: {e}")
                await asyncio.sleep(sleep_s)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background analyzer error: {e}")
                await asyncio.sleep(5)

    def _pre_filter(self, ind: TechnicalIndicators) -> bool:
        if ind.rsi_14 is not None and (ind.rsi_14 > 70 or ind.rsi_14 < 30):
            return True
        if ind.macd_hist is not None and abs(ind.macd_hist) > abs(ind.close * 0.002):
            return True
        if ind.bb_upper is not None and ind.bb_lower is not None and ind.bb_middle is not None:
            bb_width = (ind.bb_upper - ind.bb_lower) / ind.bb_middle if ind.bb_middle else 0
            if bb_width > 0.025:
                price_position = (ind.close - ind.bb_lower) / (ind.bb_upper - ind.bb_lower) if ind.bb_upper != ind.bb_lower else 0.5
                if price_position > 0.92 or price_position < 0.08:
                    return True
        if ind.price_change_pct is not None and abs(ind.price_change_pct) > 3.0:
            return True
        if ind.volume_change_pct is not None and ind.volume_change_pct > 150:
            return True
        if ind.ema_9 is not None and ind.ema_21 is not None and ind.close > 0:
            ema_diff = abs(ind.ema_9 - ind.ema_21) / ind.close * 100
            if ema_diff > 0.5:
                return True
        return False

    async def record_feedback(self, feedback: QwenFeedback):
        try:
            await self.db.execute(
                "INSERT INTO qwen_feedback (time, signal_id, trade_result, pnl, analysis_correct, prompt_version, insights) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                datetime.now(timezone.utc), feedback.signal_id, feedback.trade_result,
                feedback.pnl, feedback.analysis_correct, feedback.prompt_version, feedback.insights,
            )
            self.feedback_history.append(feedback.model_dump())
            if len(self.feedback_history) > 20:
                self.feedback_history = self.feedback_history[-20:]
        except Exception as e:
            logger.error(f"Error recording feedback: {e}")


app = FastAPI(title="Qwen Analyzer", version="2.0.0")
analyzer = QwenAnalyzer.get_instance()
bg_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup():
    global bg_task
    await analyzer.initialize()
    analyzer.running = True
    bg_task = asyncio.create_task(analyzer.background_analyzer())
    logger.info("Qwen Analyzer API + Background started")


@app.on_event("shutdown")
async def shutdown():
    global bg_task
    analyzer.running = False
    if bg_task:
        bg_task.cancel()
    if analyzer.redis:
        await analyzer.redis.close()
    if analyzer.db:
        await analyzer.db.close()


@app.post("/analyze")
async def analyze_endpoint(req: AnalyzeRequest) -> AnalyzeResponse:
    result = await analyzer.analyze_quick(req)
    if result is None:
        return AnalyzeResponse(signal="hold", confidence=0.3, reasoning="Analysis unavailable")

    signal = result.get("signal")
    if not signal or signal not in ("buy", "sell", "hold"):
        signal = "hold"

    confidence = result.get("confidence")
    try:
        confidence = min(1.0, max(0.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.3

    reasoning = result.get("reasoning")
    if not reasoning or not isinstance(reasoning, str):
        reasoning = "No reasoning provided"

    return AnalyzeResponse(
        signal=signal,
        confidence=confidence,
        reasoning=reasoning,
        target_price=result.get("target_price"),
        stop_loss=result.get("stop_loss"),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "model": analyzer.model}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
