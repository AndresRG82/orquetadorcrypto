import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger("swarm-coordinator")


class OllamaRuntime:
    def __init__(self, base_url: str = "http://ollama:11434",
                 primary_model: str = "gemma3:4b",
                 fallback_model: str = "qwen2.5:3b"):
        self.base_url = base_url.rstrip("/")
        self.primary = primary_model
        self.fallback = fallback_model
        self._client = httpx.AsyncClient(timeout=120.0)

    async def close(self):
        await self._client.aclose()

    async def query(self, prompt: str, model: Optional[str] = None, temperature: float = 0.3) -> str:
        model = model or self.primary
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 512,
            },
        }
        try:
            resp = await self._client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "").strip()
        except Exception as e:
            logger.warning("Ollama primary %s failed: %s", model, e)
            if model == self.primary:
                logger.info("Falling back to %s", self.fallback)
                return await self.query(prompt, model=self.fallback, temperature=temperature)
            return ""

    async def query_agent(self, prompt_template: str, context: dict, temperature: float = 0.3) -> str:
        prompt = prompt_template.format(**context)
        return await self.query(prompt, temperature=temperature)

    async def query_coordinator(self, prompt_template: str, context: dict) -> dict:
        prompt = prompt_template.format(**context)
        raw = await self.query(prompt, temperature=0.2)
        try:
            result = json.loads(raw)
            return result
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Coordinator JSON parse failed: %s | raw=%s", e, raw[:200])
            try:
                start = raw.index("{")
                end = raw.rindex("}") + 1
                return json.loads(raw[start:end])
            except (ValueError, json.JSONDecodeError):
                return {
                    "market_outlook": "lateral",
                    "confidence_adjustment": 1.0,
                    "risk_adjustment": "mantiene",
                    "kelly_fraction_suggested": 0.15,
                    "param_adjustments": "Sin cambios",
                    "reasoning": "No se pudo parsear respuesta del coordinador",
                }
