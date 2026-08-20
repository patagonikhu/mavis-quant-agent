"""XGBoost 二次校准模型

在规则评分基础上叠加 ML 概率，公式：
  final_score = rule_score * 0.6 + ml_probability * 100 * 0.4

训练标签：信号触发后10日内板块最大涨幅 >= 10% → 1，否则 → 0
"""

from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path
from typing import Optional

import numpy as np

from app.ml.feature_engineering import FeatureVector

logger = logging.getLogger(__name__)

MODEL_PATH = Path("data/xgb_calibrator.pkl")


class XGBoostCalibrator:
    """XGBoost 二次校准器（可选依赖 xgboost）"""

    def __init__(self):
        self._model = None
        self._available = self._check_xgb()

    def _check_xgb(self) -> bool:
        try:
            import xgboost  # noqa: F401
            return True
        except ImportError:
            logger.info("xgboost 未安装，ML校准不可用，将使用纯规则评分")
            return False

    def train(
        self,
        features: list[FeatureVector],
        labels: list[int],
        n_estimators: int = 200,
        max_depth: int = 5,
        learning_rate: float = 0.05,
    ) -> dict:
        """训练模型

        Args:
            features: 特征向量列表
            labels: 0/1 标签（1=10日内涨幅>=10%）

        Returns:
            训练指标 dict
        """
        if not self._available:
            return {"error": "xgboost 未安装"}
        if len(features) < 30:
            return {"error": f"样本量不足（{len(features)}），至少需要30条"}

        import xgboost as xgb
        from sklearn.model_selection import cross_val_score

        X = np.array([f.to_array() for f in features])
        y = np.array(labels)

        self._model = xgb.XGBClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            use_label_encoder=False,
            eval_metric="logloss",
        )

        # 5折交叉验证
        scores = cross_val_score(self._model, X, y, cv=5, scoring="roc_auc")
        self._model.fit(X, y)

        self._save()
        metrics = {
            "samples": len(features),
            "positive_rate": float(y.mean()),
            "cv_auc_mean": round(float(scores.mean()), 3),
            "cv_auc_std": round(float(scores.std()), 3),
            "feature_names": FeatureVector.feature_names(),
        }
        logger.info("XGBoost 训练完成: %s", metrics)
        return metrics

    def predict_proba(self, fv: FeatureVector) -> float:
        """预测启动概率（0-1）"""
        if not self._available or self._model is None:
            return 0.0
        try:
            import numpy as np
            X = np.array([fv.to_array()])
            return float(self._model.predict_proba(X)[0][1])
        except Exception as e:
            logger.warning("predict_proba 失败: %s", e)
            return 0.0

    def calibrate_score(self, rule_score: float, fv: FeatureVector) -> float:
        """融合规则分和ML概率

        final = rule_score * 0.6 + ml_prob * 100 * 0.4
        未训练时退化为纯规则分。
        """
        if not self._available or self._model is None:
            return rule_score
        ml_prob = self.predict_proba(fv)
        return round(rule_score * 0.6 + ml_prob * 100 * 0.4, 1)

    def _save(self):
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(self._model, f)
        logger.info("模型已保存: %s", MODEL_PATH)

    def load(self) -> bool:
        """加载已保存的模型"""
        if not MODEL_PATH.exists():
            return False
        try:
            with open(MODEL_PATH, "rb") as f:
                self._model = pickle.load(f)
            logger.info("模型已加载: %s", MODEL_PATH)
            return True
        except Exception as e:
            logger.warning("加载模型失败: %s", e)
            return False


# 全局单例
_calibrator: Optional[XGBoostCalibrator] = None


def get_calibrator() -> XGBoostCalibrator:
    global _calibrator
    if _calibrator is None:
        _calibrator = XGBoostCalibrator()
        _calibrator.load()  # 尝试加载已有模型
    return _calibrator
