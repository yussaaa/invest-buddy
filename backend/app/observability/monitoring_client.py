"""MonitoringClient — abstraction over MLflow and W&B.

Configured via MONITORING_BACKEND=mlflow|wandb|none in .env.
Both backends are optional — the system works without either.

Usage:
    from app.observability.monitoring_client import get_monitoring_client
    client = get_monitoring_client()
    await client.log_analysis_run(state, ragas_scores)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any, Optional

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


class MonitoringClient(ABC):
    @abstractmethod
    def log_analysis_run(self, run_id: str, ticker: str, params: dict, metrics: dict, artifacts: dict = {}) -> None: ...

    @abstractmethod
    def log_llm_trace(self, agent: str, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int) -> None: ...

    @abstractmethod
    def log_eval_run(self, dataset: str, scores: dict[str, float]) -> None: ...


class MLflowClient(MonitoringClient):
    """MLflow tracking client — Databricks-native."""

    def __init__(self, tracking_uri: str, experiment_name: str = "agent-invest"):
        import mlflow
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)
        self._mlflow = mlflow

    def log_analysis_run(self, run_id: str, ticker: str, params: dict, metrics: dict, artifacts: dict = {}) -> None:
        try:
            with self._mlflow.start_run(run_name=f"{ticker}_{run_id[:8]}"):
                self._mlflow.log_params({"ticker": ticker, "run_id": run_id, **params})
                self._mlflow.log_metrics({k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))})
                for name, content in artifacts.items():
                    with open(f"/tmp/{name}.json", "w") as f:
                        json.dump(content, f, default=str)
                    self._mlflow.log_artifact(f"/tmp/{name}.json")
        except Exception as e:
            log.warning("mlflow_log_error", error=str(e))

    def log_llm_trace(self, agent: str, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int) -> None:
        try:
            with self._mlflow.start_run(run_name=f"trace_{agent}", nested=True):
                self._mlflow.log_params({"agent": agent, "model": model})
                self._mlflow.log_metrics({
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "latency_ms": latency_ms,
                })
        except Exception as e:
            log.warning("mlflow_trace_error", error=str(e))

    def log_eval_run(self, dataset: str, scores: dict[str, float]) -> None:
        try:
            with self._mlflow.start_run(run_name=f"eval_{dataset}"):
                self._mlflow.log_params({"dataset": dataset})
                self._mlflow.log_metrics(scores)
        except Exception as e:
            log.warning("mlflow_eval_error", error=str(e))


class WandBClient(MonitoringClient):
    """Weights & Biases client with Weave LLM tracing."""

    def __init__(self, api_key: str, project: str):
        import wandb
        wandb.login(key=api_key)
        self._wandb = wandb
        self._project = project

    def log_analysis_run(self, run_id: str, ticker: str, params: dict, metrics: dict, artifacts: dict = {}) -> None:
        try:
            with self._wandb.init(project=self._project, name=f"{ticker}_{run_id[:8]}", reinit=True) as run:
                run.config.update({"ticker": ticker, "run_id": run_id, **params})
                run.log(metrics)
        except Exception as e:
            log.warning("wandb_log_error", error=str(e))

    def log_llm_trace(self, agent: str, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int) -> None:
        try:
            import wandb
            wandb.log({
                f"{agent}/prompt_tokens": prompt_tokens,
                f"{agent}/completion_tokens": completion_tokens,
                f"{agent}/latency_ms": latency_ms,
                f"{agent}/model": model,
            })
        except Exception as e:
            log.warning("wandb_trace_error", error=str(e))

    def log_eval_run(self, dataset: str, scores: dict[str, float]) -> None:
        try:
            with self._wandb.init(project=self._project, name=f"eval_{dataset}", reinit=True) as run:
                run.log(scores)
                self._wandb.Table(
                    columns=list(scores.keys()),
                    data=[list(scores.values())],
                )
        except Exception as e:
            log.warning("wandb_eval_error", error=str(e))


class NoOpClient(MonitoringClient):
    """No-op client when monitoring is disabled."""

    def log_analysis_run(self, *args, **kwargs) -> None: pass
    def log_llm_trace(self, *args, **kwargs) -> None: pass
    def log_eval_run(self, *args, **kwargs) -> None: pass


@lru_cache
def get_monitoring_client() -> MonitoringClient:
    """Return the configured monitoring client (cached singleton)."""
    settings = get_settings()
    match settings.monitoring_backend:
        case "mlflow":
            try:
                return MLflowClient(tracking_uri=settings.mlflow_tracking_uri)
            except Exception as e:
                log.warning("mlflow_init_failed", error=str(e))
                return NoOpClient()
        case "wandb":
            if not settings.wandb_api_key:
                log.warning("wandb_no_api_key")
                return NoOpClient()
            try:
                return WandBClient(api_key=settings.wandb_api_key, project=settings.wandb_project)
            except Exception as e:
                log.warning("wandb_init_failed", error=str(e))
                return NoOpClient()
        case _:
            return NoOpClient()
