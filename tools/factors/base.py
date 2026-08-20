"""
factor base.py - 因子抽象类 (v1.0)

设计原则:
1. 因子 = 输入数据 → 输出数值
2. 统一接口: compute(df, **kwargs) → pd.Series
3. 配置文件驱动: 参数从 config/factors.yaml 读
4. 可测试: 每个因子独立可单测
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import pandas as pd
import numpy as np
import yaml
from pathlib import Path


# === 1. 全局配置加载 (单例) ===
class FactorConfig:
    """因子配置单例, 从 YAML 加载"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        config_path = Path(__file__).parent.parent.parent / "config" / "factors.yaml"
        if not config_path.exists():
            raise FileNotFoundError(
                f"config/factors.yaml 不存在 (路径: {config_path})\n"
                f"  首次使用请创建 config/factors.yaml (从模板/记忆复制完整配置)\n"
                f"  不再提供 .example 兜底, 请直接维护正式配置"
            )
        with open(config_path) as f:
            self._raw = yaml.safe_load(f)

    def get(self, key: str, default=None):
        """点分路径取值, 例: get('chan.beichi_area_ratio') → 0.5"""
        keys = key.split('.')
        v = self._raw
        for k in keys:
            if isinstance(v, dict) and k in v:
                v = v[k]
            else:
                return default
        return v


# === 2. 因子抽象类 ===
class Factor(ABC):
    """
    因子基类
    
    使用:
        class MyFactor(Factor):
            name = "my_factor"
            category = "chan"
            dependencies = ["close", "volume"]
            
            def compute(self, df, **kwargs):
                return df['close'] / df['volume']
    """

    # 子类必须定义
    name: str = ""                  # 因子名 (e.g. "beichi_day")
    category: str = ""              # 因子分类 (e.g. "chan")
    dependencies: List[str] = []     # 依赖的列 (e.g. ["close", "high", "low"])
    description: str = ""           # 一句话描述
    version: str = "1.0"            # 因子版本
    output_type: str = "series"     # "series" (默认, → pd.Series) | "dict" (→ dict, 复合输出)

    def __init__(self, config: Optional[FactorConfig] = None):
        self.config = config or FactorConfig()

    @abstractmethod
    def compute(self, df: pd.DataFrame, **kwargs):
        """
        计算因子值

        Args:
            df: 输入数据, 至少包含 dependencies 里的列
            **kwargs: 额外参数 (e.g. window=20, code="300274")

        Returns:
            pd.Series (output_type=series) 或 dict (output_type=dict)
        """
        pass

    def validate_input(self, df: pd.DataFrame) -> bool:
        """验证输入是否满足依赖"""
        missing = [col for col in self.dependencies if col not in df.columns]
        if missing:
            raise ValueError(
                f"Factor {self.name} requires {missing}, "
                f"got {list(df.columns)}"
            )
        return True

    def __call__(self, df, **kwargs):
        """让因子可以像函数一样调用

        兼容 list-of-dict (RawContext.kline) / DataFrame / KLineBar 列表输入。
        内部统一 normalize 成 DataFrame 后再 validate + compute。
        """
        # 容忍 list-of-dict 输入 (RawContext.kline 实际是这种格式)
        if not isinstance(df, pd.DataFrame):
            try:
                from tools.factors.utils import df_from_bars
                df = df_from_bars(df)
            except Exception:
                pass  # 让 validate_input 自己报错
        self.validate_input(df)
        return self.compute(df, **kwargs)

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name!r} category={self.category!r}>"


# === 3. 因子元数据 (用于 GA 和报告) ===
class FactorMeta:
    """因子元数据"""

    def __init__(self, factor: Factor):
        self.name = factor.name
        self.category = factor.category
        self.dependencies = factor.dependencies
        self.description = factor.description
        self.version = factor.version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "dependencies": self.dependencies,
            "description": self.description,
            "version": self.version,
        }


# === 4. 工具函数 (给因子用) ===
def safe_div(a: pd.Series, b: pd.Series, fill: float = 0.0) -> pd.Series:
    """安全除法, 防 0"""
    return a / b.replace(0, np.nan).fillna(fill)


def rank_pct(s: pd.Series) -> pd.Series:
    """百分位排名 (0-1)"""
    return s.rank(pct=True)


def ts_rank(s: pd.Series, window: int) -> pd.Series:
    """滚动时序排名 (0-1)"""
    return s.rolling(window, min_periods=1).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )


def ts_mean(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=1).mean()


def ts_std(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=1).std()


def zscore(s: pd.Series, window: int = None) -> pd.Series:
    """横截面/时序 z-score 标准化"""
    if window is None:
        return (s - s.mean()) / s.std()
    mu = s.rolling(window).mean()
    sd = s.rolling(window).std()
    return safe_div(s - mu, sd)
