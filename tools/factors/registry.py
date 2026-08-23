"""
factor_registry.py - 因子注册表 (v1.0)

作用:
1. 集中管理所有可用因子 (避免散落各文件)
2. 统一变量名 (避免 chan_beichi / beichi_str / beichi_d 混乱)
3. 一键跑所有因子 (供 老 data 工具 / GA / report 调用)
"""
from typing import Dict, List, Type, Any, Optional
import pandas as pd
import importlib
import inspect
from pathlib import Path

from tools.factors.base import Factor, FactorMeta


# === 1. 注册表 (单例) ===
class FactorRegistry:
    """全局因子注册表"""

    _instance = None
    _factors: Dict[str, Factor] = {}  # name -> instance
    _by_category: Dict[str, List[str]] = {}  # category -> [names]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._auto_register()
        return cls._instance

    def _auto_register(self):
        """自动扫描 tools/factors/ 下所有 Factor 子类并注册"""
        # Path(__file__) = tools/factors/registry.py
        factors_dir = Path(__file__).parent  # tools/factors/
        tools_dir = factors_dir.parent  # tools/
        project_root = tools_dir.parent  # 项目根目录

        for py_file in factors_dir.rglob("*.py"):
            if py_file.name in ("__init__.py", "base.py", "registry.py"):
                continue

            # 算 module path
            # 例: tools/factors/price/returns.py → tools.factors.price.returns
            rel_file = py_file.relative_to(tools_dir).with_suffix("")
            module_path = "tools." + str(rel_file).replace("/", ".")
            try:
                module = importlib.import_module(module_path)
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, Factor) and obj is not Factor:
                        self.register(obj())
            except Exception as e:
                # 文件可能没因子 (例如 __init__.py), 忽略
                pass

    def register(self, factor: Factor):
        """注册一个因子"""
        self._factors[factor.name] = factor
        self._by_category.setdefault(factor.category, []).append(factor.name)

    def get(self, name: str) -> Optional[Factor]:
        return self._factors.get(name)

    def list_all(self) -> List[str]:
        return list(self._factors.keys())

    def list_by_category(self, category: str) -> List[str]:
        return self._by_category.get(category, [])

    def categories(self) -> List[str]:
        return list(self._by_category.keys())

    def count(self) -> int:
        return len(self._factors)

    def summary(self) -> Dict[str, int]:
        """统计: 各类别有多少因子"""
        return {cat: len(names) for cat, names in self._by_category.items()}

    def get_meta(self, name: str) -> Optional[FactorMeta]:
        f = self.get(name)
        return FactorMeta(f) if f else None


# === 2. 因子运行器 ===
class FactorRunner:
    """
    一键跑一组因子

    使用:
        runner = FactorRunner()
        runner.add(["beichi_day", "hub_position", "main_yi_5d"])
        result = runner.run(df)
        # result 是 DataFrame, 每列一个因子
    """

    def __init__(self, registry: Optional[FactorRegistry] = None):
        self.registry = registry or FactorRegistry()
        self.factor_names: List[str] = []

    def add(self, factor_names: List[str]):
        """添加要跑的因子"""
        for name in factor_names:
            if name not in self.registry.list_all():
                raise ValueError(f"Factor {name!r} not registered. "
                                 f"Available: {self.registry.list_all()}")
        self.factor_names.extend(factor_names)
        return self

    def add_category(self, category: str):
        """加整个分类的因子"""
        self.factor_names.extend(self.registry.list_by_category(category))
        return self

    def add_all(self):
        """加所有因子"""
        self.factor_names = self.registry.list_all()
        return self

    def run(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        跑所有添加的因子, 返回 DataFrame

        Args:
            df: 输入数据
            **kwargs: 传给每个因子的额外参数

        Returns:
            DataFrame, 每列一个因子值
            注: output_type='dict' 的因子返回 dict, 不会进 DataFrame
        """
        result = pd.DataFrame(index=df.index)
        dict_results = {}
        for name in self.factor_names:
            try:
                factor = self.registry.get(name)
                out = factor(df, **kwargs)
                if factor.output_type == "dict":
                    dict_results[name] = out
                else:
                    result[name] = out
            except Exception as e:
                # 单个因子失败不影响其他
                print(f"⚠️ Factor {name!r} failed: {e}")
                if factor.output_type == "dict":
                    dict_results[name] = {"error": str(e)}
                else:
                    result[name] = pd.NA
        # 把 dict 结果挂到 DataFrame.attrs (供 老 data 工具 / report 读)
        if dict_results:
            result.attrs["dict_factors"] = dict_results
        return result

    def run_one(self, name: str, df: pd.DataFrame, **kwargs):
        """跑单个因子, 返回 Series 或 dict (按 output_type)"""
        factor = self.registry.get(name)
        return factor(df, **kwargs)


# === 3. 标准因子集 (预定义套餐) ===
class StandardFactorSets:
    """标准因子集, 一键调用"""

    @staticmethod
    def core_5method(registry: FactorRegistry) -> List[str]:
        """5方法核心因子 (跟 report 一致)"""
        return [
            # 缠论 3 要素
            "beichi_day", "beichi_60m", "hub_distance",
            # 威科夫 3 阶段
            "wyckoff_stage", "wyckoff_confidence",
            # SMC 核心
            "smc_total_obs",
            # 量价
            "main_yi_5d", "obv_trend",
            # 多市场
            "resonance",
        ]

    @staticmethod
    def alpha_101_top10(registry: FactorRegistry) -> List[str]:
        """Alpha 101 中最经典的 10 个"""
        return [
            "alpha_001", "alpha_006", "alpha_012", "alpha_034",
            "alpha_038", "alpha_040", "alpha_041", "alpha_042",
            "alpha_054", "alpha_101",
        ]

    @staticmethod
    def value_factors(registry: FactorRegistry) -> List[str]:
        """估值因子 (PEG/DCF)"""
        return [
            "pe_ttm_inverse", "pb_inverse", "peg_estimate",
            "earnings_yield", "fcf_yield",
        ]


# === 4. 调试工具 ===
def print_registry():
    """打印注册表摘要 (调试用)"""
    reg = FactorRegistry()
    print(f"\n📊 因子注册表 ({reg.count()} 个因子):")
    print(f"{'分类':<20} {'数量':<6} {'因子'}")
    print("-" * 60)
    for cat in reg.categories():
        names = reg.list_by_category(cat)
        names_str = ", ".join(names[:5])
        if len(names) > 5:
            names_str += f" ... (+{len(names)-5})"
        print(f"  {cat:<20} {len(names):<6} {names_str}")


if __name__ == "__main__":
    print_registry()
