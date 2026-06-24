import ast
import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger("alpha_zoo")


class AlphaMeta(BaseModel):
    id: str
    nickname: str = ""
    theme: list[str] = []
    formula_latex: str = ""
    columns_required: list[str] = ["close"]
    extras_required: list[str] = []
    universe: list[str] = ["crypto"]
    frequency: list[str] = ["1h", "4h", "1d"]
    decay_horizon: int = 0
    min_warmup_bars: int = 20
    notes: str = ""


class Alpha:
    def __init__(self, alpha_id: str, zoo: str, module_path: str, meta: AlphaMeta):
        self.id = alpha_id
        self.zoo = zoo
        self.module_path = module_path
        self.meta = meta
        self._module = None

    def compute(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        if self._module is None:
            self._module = importlib.import_module(self.module_path)
        result = self._module.compute(panel)
        required_cols = self.meta.columns_required
        panel_shape = panel[required_cols[0]].shape if required_cols else (0, 0)
        if result.shape != panel_shape:
            raise ValueError(
                f"Alpha {self.id}: output shape {result.shape} != panel shape {panel_shape}"
            )
        if result.isin([np.inf, -np.inf]).any().any():
            raise ValueError(f"Alpha {self.id}: output contains inf values")
        nan_ratio = result.isna().sum().sum() / max(1, result.size)
        if nan_ratio > 0.95:
            logger.warning(f"Alpha {self.id}: {nan_ratio:.1%} NaN — skipping")
            raise ValueError(f"Alpha {self.id}: >95% NaN")
        return result


class Registry:
    def __init__(self, zoo_root: str = "shared.alpha_zoo.zoo"):
        self.zoo_root = zoo_root
        self._alphas: dict[str, Alpha] = {}
        self._errors: dict[str, str] = {}

    def discover(self):
        root_pkg = importlib.import_module(self.zoo_root)
        root_path = Path(root_pkg.__file__).parent
        for subdir in sorted(root_path.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith("_") or subdir.name == "__pycache__":
                continue
            zoo_id = subdir.name
            pkg_path = f"{self.zoo_root}.{zoo_id}"
            for file in sorted(subdir.glob("*.py")):
                if file.name == "__init__.py":
                    continue
                short_id = file.stem
                alpha_id = f"{zoo_id}_{short_id}"
                self._register(alpha_id, zoo_id, pkg_path, file)

    def _register(self, alpha_id: str, zoo_id: str, pkg_path: str, file_path: Path):
        module_path = f"{pkg_path}.{file_path.stem}"
        try:
            source = file_path.read_text()
            meta = self._extract_meta(source, alpha_id)
            self._alphas[alpha_id] = Alpha(
                alpha_id=alpha_id, zoo=zoo_id,
                module_path=module_path, meta=meta,
            )
        except Exception as e:
            self._errors[alpha_id] = str(e)
            logger.warning(f"Failed to register {alpha_id}: {e}")

    def _extract_meta(self, source: str, alpha_id: str) -> AlphaMeta:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__alpha_meta__":
                        meta_dict = ast.literal_eval(node.value)
                        if "id" not in meta_dict:
                            meta_dict["id"] = alpha_id
                        return AlphaMeta(**meta_dict)
        return AlphaMeta(id=alpha_id, notes="No __alpha_meta__ found")

    def list(self, zoo: Optional[str] = None, theme: Optional[str] = None,
             universe: Optional[str] = None) -> list[str]:
        ids = list(self._alphas.keys())
        if zoo:
            ids = [i for i in ids if i.startswith(f"{zoo}_")]
        if theme:
            ids = [i for i in ids if theme in self._alphas[i].meta.theme]
        if universe:
            ids = [i for i in ids if universe in self._alphas[i].meta.universe]
        return sorted(ids)

    def get(self, alpha_id: str) -> Optional[Alpha]:
        return self._alphas.get(alpha_id)

    def get_source(self, alpha_id: str) -> Optional[str]:
        alpha = self.get(alpha_id)
        if alpha is None:
            return None
        mod = importlib.import_module(alpha.module_path)
        return mod.__source__ if hasattr(mod, "__source__") else ""

    def compute(self, alpha_id: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        alpha = self.get(alpha_id)
        if alpha is None:
            raise KeyError(f"Alpha {alpha_id} not found")
        required = alpha.meta.columns_required
        extras = alpha.meta.extras_required
        for col in required + extras:
            if col not in panel:
                raise ValueError(
                    f"Alpha {alpha_id} requires '{col}' in panel"
                )
        return alpha.compute(panel)

    def health(self) -> dict:
        return {
            "loaded": len(self._alphas),
            "failed": len(self._errors),
            "errors": dict(list(self._errors.items())[:5]),
        }

    def export_manifest(self) -> list[dict]:
        return [
            {
                "id": a.id,
                "zoo": a.zoo,
                "nickname": a.meta.nickname,
                "theme": a.meta.theme,
                "universe": a.meta.universe,
                "frequency": a.meta.frequency,
                "decay_horizon": a.meta.decay_horizon,
                "min_warmup_bars": a.meta.min_warmup_bars,
            }
            for a in sorted(self._alphas.values(), key=lambda x: x.id)
        ]


_default_registry: Registry | None = None


def get_default_registry() -> Registry:
    global _default_registry
    if _default_registry is None:
        _default_registry = Registry()
        _default_registry.discover()
        logger.info(
            f"Alpha Zoo: {len(_default_registry._alphas)} loaded, "
            f"{len(_default_registry._errors)} failed"
        )
    return _default_registry


def reset_default_registry():
    global _default_registry
    _default_registry = None
