import asyncio
import logging
import random
from collections import defaultdict, deque
from contextlib import suppress
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.analytics.aggregator import AnalyticsAggregator
from app.mock_data.generator import generate_historical_transactions, generate_transaction
from app.models.events import TransactionEvent
from app.models.transaction import PaymentTransaction
from app.cache.runtime_status import RedisRuntimeStatus
from app.repositories.base import TransactionStore
from app.schemas.dashboard import DashboardSnapshot
from app.schemas.data import DashboardSnapshotData
from app.schemas.websocket import EventsSocketMessage, SnapshotSocketMessage
from app.services.event_bus import EventBus, InMemoryEventBus
from app.services.kpi_cache import KpiCache
from app.services.replay_producer import ReplayProducer, TransactionProducer
from app.services.data_reconciliation import DataReconciliationService, ExpectedPeriodMetrics
from app.services.sqlserver_replay_provider import SqlServerReplayProvider
from app.websocket.manager import WebSocketManager

logger = logging.getLogger(__name__)


class TransactionEngine:
    """Supervises either a bounded SQL Server replay or the legacy local mock."""

    def __init__(
        self,
        aggregator: AnalyticsAggregator,
        websocket_manager: WebSocketManager,
        kpi_cache: KpiCache,
        repository: TransactionStore | None = None,
        interval_seconds: float = 5,
        max_batch_size: int = 500,
        replay_provider: SqlServerReplayProvider | None = None,
        fast_forward_interval_seconds: float = 0.1,
        fast_forward_batch_size: int = 500,
        fast_forward_fetch_batch_size: int = 5000,
        event_bus: EventBus | None = None,
        replay_producer: TransactionProducer | None = None,
        redis_status: RedisRuntimeStatus | None = None,
    ) -> None:
        self._aggregator = aggregator
        self._websocket_manager = websocket_manager
        self._kpi_cache = kpi_cache
        self._repository = repository
        self._provider = replay_provider
        self._event_bus = event_bus or InMemoryEventBus()
        subscribe_batch = getattr(self._event_bus, "subscribe_batch", None)
        if callable(subscribe_batch):
            subscribe_batch("transaction", self._aggregator.consume_transaction_batch)
        self._event_bus.subscribe("transaction", self._aggregator.consume_transaction_event)
        self._event_bus.subscribe("alert", self._aggregator.consume_alert_event)
        self._replay_producer = replay_producer or (
            ReplayProducer(replay_provider, self._event_bus) if replay_provider else None
        )
        self._normal_interval_seconds = interval_seconds
        self._normal_batch_size = max_batch_size
        self._fast_forward_interval_seconds = fast_forward_interval_seconds
        self._fast_forward_batch_size = fast_forward_batch_size
        self._fast_forward_fetch_batch_size = fast_forward_fetch_batch_size
        self._fast_forward_enabled = False
        self._fast_forward_buffer: deque[TransactionEvent] = deque()
        self._speed_changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._pipeline_lock = asyncio.Lock()
        self._started_at: str | None = None
        self._batches_processed = 0
        self._transactions_processed = 0
        self._last_batch_latency_ms = 0.0
        self._last_event_at: str | None = None
        self._last_batch_at: str | None = None
        self._failures = 0
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._paused = False
        self._source_exhausted = False
        self._active_period = "today"
        self._replay_run_id = uuid4().hex
        self._expected_metrics: ExpectedPeriodMetrics | None = None
        self._expected_metrics_error: str | None = None
        self._data_reconciliation = DataReconciliationService()
        self._latency_samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=300))
        self._redis_status = redis_status

    @property
    def is_sqlserver_replay(self) -> bool:
        return self._provider is not None

    async def seed(self, count: int = 140) -> None:
        """Prepare a snapshot without ever loading a SQL Server table wholesale."""
        async with self._pipeline_lock:
            if self._provider:
                await self._bootstrap_period_locked(self._active_period, configure_period=True)
                snapshot = await self._build_snapshot_locked()
            else:
                if self._repository is None:
                    raise RuntimeError("A repository is required when no SQL Server replay provider is configured.")
                transactions = await self._repository.load_transactions()
                if not transactions:
                    transactions = await self._repository.persist_batch(generate_historical_transactions(count))
                await self._aggregator.rebuild(transactions)
                snapshot = await self._build_snapshot_locked()

        await self._store_snapshot(snapshot)

    async def mark_source_unavailable(self, exc: Exception) -> None:
        async with self._pipeline_lock:
            self._failures += 1
            self._consecutive_failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._source_exhausted = True
            self._paused = True
            snapshot = await self._build_snapshot_locked(
                detail="Source SQL Server indisponible. API démarrée en mode dégradé sans inventer de données."
            )
        await self._store_snapshot(snapshot)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._paused = False
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._task = asyncio.create_task(self._supervise(), name="supervision-replay-supervisor")

    async def stop(self) -> None:
        self._stop_event.set()
        self._speed_changed.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._provider:
            await self._provider.close()

    async def pause(self) -> None:
        self._paused = True
        self._speed_changed.set()
        async with self._pipeline_lock:
            snapshot = await self._build_snapshot_locked()
        await self._store_snapshot(snapshot)
        await self._websocket_manager.broadcast(self._event_payload(snapshot))

    async def resume(self) -> None:
        self._paused = False
        self._speed_changed.set()
        async with self._pipeline_lock:
            snapshot = await self._build_snapshot_locked()
        await self._store_snapshot(snapshot)
        await self._websocket_manager.broadcast(self._event_payload(snapshot))

    async def set_fast_forward(self, enabled: bool) -> dict[str, Any]:
        """Change replay cadence without resetting state or moving the cursor."""
        async with self._pipeline_lock:
            self._fast_forward_enabled = enabled
            snapshot = await self._build_snapshot_locked(
                detail="Avance rapide activée." if enabled else "Rythme normal rétabli."
            )
        await self._store_snapshot(snapshot)
        await self._websocket_manager.broadcast(self.snapshot_payload(snapshot))
        self._speed_changed.set()
        return snapshot["replay_status"]

    async def reset(self) -> None:
        async with self._pipeline_lock:
            if self._provider:
                await self._provider.reset()
                self._fast_forward_buffer.clear()
                self._replay_run_id = uuid4().hex
                await self._bootstrap_period_locked(self._active_period, configure_period=False)
            else:
                await self._aggregator.reset()
                self._replay_run_id = uuid4().hex
                self._batches_processed = 0
                self._transactions_processed = 0
                self._last_batch_at = None
                self._last_error = None
                self._source_exhausted = False
            snapshot = await self._build_snapshot_locked()
        await self._store_snapshot(snapshot)
        await self._websocket_manager.broadcast(self.snapshot_payload(snapshot))

    async def _supervise(self) -> None:
        while not self._stop_event.is_set():
            try:
                if not self._paused and not self._source_exhausted:
                    await self._run_iteration()
                self._consecutive_failures = 0
                await self._wait_for_next_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failures += 1
                self._consecutive_failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("supervision_replay_iteration_failed")
                await asyncio.sleep(min(10.0, self._current_interval_seconds * 2 ** min(self._consecutive_failures, 4)))

    async def _wait_for_next_tick(self) -> None:
        try:
            await asyncio.wait_for(self._speed_changed.wait(), timeout=self._current_interval_seconds)
        except TimeoutError:
            pass
        finally:
            self._speed_changed.clear()

    async def _run_iteration(self) -> None:
        started = perf_counter()
        async with self._pipeline_lock:
            if self._provider:
                events = await self._next_replay_events()
                if not events:
                    self._source_exhausted = True
                    snapshot = await self._build_snapshot_locked(detail="Fin des données historiques atteinte.")
                    event_payload = self._event_payload(snapshot)
                else:
                    aggregation_started = perf_counter()
                    recorded = await self._aggregator.drain_consumed_event_batch()
                    alerts = await self._aggregator.evaluate_alerts(recorded)
                    for alert in alerts:
                        await self._event_bus.publish_alert_event(alert)
                    self._observe_latency("aggregation_latency_ms", aggregation_started)
                    self._batches_processed += 1
                    self._transactions_processed += len(recorded)
                    self._last_batch_at = datetime.now(timezone.utc).isoformat()
                    self._last_batch_latency_ms = round((perf_counter() - started) * 1000, 2)
                    snapshot = await self._build_snapshot_locked()
                    event_payload = self._event_payload(snapshot)
            else:
                if self._repository is None:
                    raise RuntimeError("Mock replay requires a local repository.")
                batch_size = random.randint(1, self._current_batch_size)
                transactions = await self._repository.persist_batch([generate_transaction() for _ in range(batch_size)])
                if not transactions:
                    return
                recorded = await self._aggregator.record_batch(transactions)
                await self._aggregator.detect_alerts(recorded)
                self._observe_latency("aggregation_latency_ms", started)
                self._batches_processed += 1
                self._transactions_processed += len(recorded)
                self._last_batch_at = datetime.now(timezone.utc).isoformat()
                self._last_batch_latency_ms = round((perf_counter() - started) * 1000, 2)
                snapshot = await self._build_snapshot_locked()
                event_payload = self._event_payload(snapshot)
        await self._store_snapshot(snapshot)
        await self._websocket_manager.broadcast(event_payload)
        self._last_event_at = event_payload["emitted_at"]
        self._observe_latency("backend_end_to_end_latency_ms", started)

    async def synchronized_snapshot(self, period: str | None = None) -> DashboardSnapshotData:
        broadcast_payload: dict[str, Any] | None = None
        async with self._pipeline_lock:
            if self._provider and period and period != self._active_period:
                await self._bootstrap_period_locked(period, configure_period=True)
                snapshot = await self._build_snapshot_locked()
                broadcast_payload = self.snapshot_payload(snapshot)
            else:
                snapshot = await self._build_snapshot_locked()
        await self._store_snapshot(snapshot)
        if broadcast_payload:
            await self._websocket_manager.broadcast(broadcast_payload)
        return snapshot

    async def _bootstrap_period_locked(self, period: str, configure_period: bool) -> None:
        if self._provider is None:
            return
        await self._provider.connect()
        if configure_period:
            await self._provider.configure_period(period)
        await self._aggregator.reset()
        self._fast_forward_buffer.clear()
        await self._aggregator.set_amount_unit(self._provider.amount_metadata["amount_unit"])
        self._active_period = period
        self._replay_run_id = uuid4().hex
        self._expected_metrics = None
        self._expected_metrics_error = None
        self._batches_processed = 0
        self._transactions_processed = 0
        self._last_batch_at = None
        self._last_error = None
        self._source_exhausted = False
        try:
            transfer_amount, transfer_count = await self._provider.transfer_summary()
            await self._aggregator.set_transfer_summary(transfer_amount, transfer_count)
        except Exception:
            logger.warning("virement_summary_unavailable", exc_info=True)
        await self._refresh_affiliation_summary(period)
        await self._refresh_expected_metrics()

        events = await self._next_replay_events()
        if not events:
            self._source_exhausted = True
            return
        recorded = await self._aggregator.drain_consumed_event_batch()
        alerts = await self._aggregator.evaluate_alerts(recorded)
        for alert in alerts:
            await self._event_bus.publish_alert_event(alert)
        self._batches_processed = 1
        self._transactions_processed = len(recorded)
        self._last_batch_at = datetime.now(timezone.utc).isoformat()

    async def _next_replay_events(self) -> list[TransactionEvent]:
        """Return a visible replay step while retaining a bounded fast buffer.

        Fast-forward preloads up to 5,000 SQL rows to reduce round trips, but
        only aggregates 500 rows per update.  The dashboard therefore keeps
        emitting intermediate states instead of jumping to a final snapshot.
        """
        if self._provider is None:
            return []
        if self._fast_forward_enabled:
            if not self._fast_forward_buffer:
                fetch_started = perf_counter()
                source_batch = await self._replay_producer.read_next_batch(self._fast_forward_fetch_batch_size) if self._replay_producer else []
                self._observe_latency("sql_fetch_latency_ms", fetch_started)
                self._fast_forward_buffer.extend(source_batch)
            if not self._fast_forward_buffer:
                return []
        if self._fast_forward_buffer:
            step_size = self._current_batch_size
            events = [self._fast_forward_buffer.popleft() for _ in range(min(step_size, len(self._fast_forward_buffer)))]
        else:
            fetch_started = perf_counter()
            events = await self._replay_producer.read_next_batch(self._current_batch_size) if self._replay_producer else []
            self._observe_latency("sql_fetch_latency_ms", fetch_started)
        if self._replay_producer:
            bus_started = perf_counter()
            await self._replay_producer.publish_events(events)
            self._observe_latency("event_bus_latency_ms", bus_started)
        return events

    async def refresh_snapshot_cache(self) -> None:
        try:
            await self.synchronized_snapshot()
        except Exception:
            logger.exception("snapshot_cache_refresh_failed")

    async def _update_snapshot_cache(self, detail: str | None = None) -> DashboardSnapshotData:
        snapshot = await self._build_snapshot_locked(detail)
        await self._store_snapshot(snapshot)
        return snapshot

    async def _build_snapshot_locked(self, detail: str | None = None) -> DashboardSnapshotData:
        snapshot_started = perf_counter()
        actual_metrics = await self._aggregator.actual_period_metrics()
        data_quality = self._data_reconciliation.reconcile(
            expected=self._expected_metrics,
            actual=actual_metrics,
            replay_complete=self._source_exhausted if self._provider else True,
            event_bus=self._event_bus_quality(actual_metrics.duplicates_ignored),
            snapshot=await self._aggregator.snapshot(),
            unavailable_reason=self._expected_metrics_error,
        )
        await self._aggregator.set_replay_status(self._replay_status(detail, data_quality))
        snapshot = await self._aggregator.snapshot()
        snapshot["data_quality"] = data_quality
        snapshot["metric_metadata"] = data_quality["metric_metadata"]
        snapshot["snapshot_version"] = snapshot.get("state_version")
        snapshot["snapshot_generated_at"] = datetime.now(timezone.utc).isoformat()
        self._observe_latency("snapshot_build_latency_ms", snapshot_started)
        return snapshot

    async def _store_snapshot(self, snapshot: DashboardSnapshotData) -> None:
        redis_started = perf_counter()
        await self._kpi_cache.store_snapshot(snapshot)
        self._observe_latency("redis_write_latency_ms", redis_started)

    async def _refresh_affiliation_summary(self, period: str | None) -> None:
        if not self._provider or not hasattr(self._provider, "get_affiliation_summary"):
            return
        try:
            await self._aggregator.set_affiliation_summary(await self._provider.get_affiliation_summary(period))
        except Exception:
            logger.warning("affiliation_summary_unavailable", exc_info=True)

    async def _refresh_expected_metrics(self) -> None:
        if not self._provider or not hasattr(self._provider, "expected_period_metrics"):
            return
        try:
            self._expected_metrics = await self._provider.expected_period_metrics()
            self._expected_metrics_error = None
        except Exception as exc:
            self._expected_metrics = None
            self._expected_metrics_error = f"{type(exc).__name__}: {exc}"
            logger.warning("expected_period_metrics_unavailable", exc_info=True)

    def _event_bus_quality(self, duplicates_ignored: int) -> dict[str, Any]:
        metrics_fn = getattr(self._event_bus, "metrics", None)
        metrics = metrics_fn() if callable(metrics_fn) else {}
        transaction = dict(metrics.get("transaction", {}))
        transaction.setdefault("published", 0)
        transaction.setdefault("delivered", 0)
        transaction.setdefault("handler_errors", 0)
        transaction["delivered_to_aggregator"] = transaction.get("delivered", 0)
        transaction["accepted_after_deduplication"] = self._transactions_processed
        transaction["duplicates_ignored"] = duplicates_ignored
        metrics["transaction"] = transaction
        return metrics

    def _replay_status(self, detail: str | None = None, data_quality: dict[str, Any] | None = None) -> dict[str, Any]:
        source_available = self._provider.available if self._provider else True
        provider_detail = self._provider.last_error if self._provider else None
        period_window = self._provider.period_window if self._provider else None
        redis = self._redis_status.to_dict() if self._redis_status else {
            "redis_configured": self._kpi_cache.name == "redis",
            "redis_available": self._kpi_cache.name == "redis",
            "redis_reason": "available" if self._kpi_cache.name == "redis" else "not_configured",
            "redis_latency_ms": None,
        }
        cache_backend = "redis" if redis.get("redis_available") else "memory"
        return {
            "mode": "sqlserver_replay" if self._provider else "mock",
            "replay_mode": "historical_replay",
            "label": "SQL Server replay mode from historical data" if self._provider else "Local mock replay mode",
            "running": self._task is not None and not self._task.done(),
            "paused": self._paused,
            "processed_transactions": self._transactions_processed,
            "batches_processed": self._batches_processed,
            "batch_size": self._current_batch_size,
            "interval_seconds": self._current_interval_seconds,
            "current_speed_tx_per_sec": round(self._current_batch_size / self._current_interval_seconds, 2),
            "current_batch_size": self._current_batch_size,
            "current_interval_ms": round(self._current_interval_seconds * 1000),
            "fast_forward_enabled": self._fast_forward_enabled,
            "cursor": self._provider.cursor_label if self._provider else None,
            "last_batch_at": self._last_batch_at,
            "source_available": source_available,
            "detail": detail or provider_detail or ("Replay en cours." if not self._source_exhausted else "Fin des données historiques atteinte."),
            "period": period_window.period if period_window else None,
            "reference_date": period_window.reference_date.isoformat() if period_window else None,
            "start_date": period_window.start_date.isoformat() if period_window else None,
            "end_date": period_window.end_date.isoformat() if period_window else None,
            "transfer_period_available": self._provider is not None and period_window is not None,
            "replay_run_id": self._replay_run_id,
            "cache_backend": cache_backend,
            "redis_configured": redis.get("redis_configured"),
            "redis_available": redis.get("redis_available"),
            "redis_reason": redis.get("redis_reason"),
            "redis_latency_ms": redis.get("redis_latency_ms"),
            "expected_transactions": data_quality.get("expected_transactions") if data_quality else None,
            "completion_rate": data_quality.get("completion_rate") if data_quality else 0.0,
            "reconciliation_status": data_quality.get("status") if data_quality else "unavailable",
            "last_reconciled_at": data_quality.get("last_reconciled_at") if data_quality else None,
            "differences": data_quality.get("differences") if data_quality else {},
        }

    @staticmethod
    def _event_payload(snapshot: DashboardSnapshotData) -> dict[str, Any]:
        validated = DashboardSnapshot.model_validate(snapshot)
        return EventsSocketMessage(
            state_version=validated.state_version,
            emitted_at=datetime.now(timezone.utc),
            events=[{"type": "supervision_update", "snapshot": validated}],
        ).model_dump(mode="json")

    @staticmethod
    def snapshot_payload(snapshot: DashboardSnapshotData) -> dict[str, Any]:
        validated = DashboardSnapshot.model_validate(snapshot)
        return SnapshotSocketMessage(
            state_version=validated.state_version,
            emitted_at=datetime.now(timezone.utc),
            snapshot=validated,
        ).model_dump(mode="json")

    async def metrics(self) -> dict[str, Any]:
        websocket_metrics = await self._websocket_manager.metrics()
        return {
            "started_at": self._started_at,
            "batches_processed": self._batches_processed,
            "transactions_processed": self._transactions_processed,
            "last_batch_latency_ms": self._last_batch_latency_ms,
            "last_event_at": self._last_event_at,
            "interval_seconds": self._current_interval_seconds,
            "batch_size": self._current_batch_size,
            "current_speed_tx_per_sec": round(self._current_batch_size / self._current_interval_seconds, 2),
            "current_batch_size": self._current_batch_size,
            "current_interval_ms": round(self._current_interval_seconds * 1000),
            "fast_forward_enabled": self._fast_forward_enabled,
            "supervisor_running": self._task is not None and not self._task.done(),
            "paused": self._paused,
            "source_exhausted": self._source_exhausted,
            "failures": self._failures,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "source": "sqlserver_replay" if self._provider else "mock",
            "latencies": {**self._latency_summary(), **{
                "websocket_queue_latency_ms": websocket_metrics.get("websocket_queue_latency_ms", 0.0),
                "websocket_send_latency_ms": websocket_metrics.get("websocket_send_latency_ms", 0.0),
            }},
        }

    def _observe_latency(self, name: str, started: float) -> None:
        self._latency_samples[name].append(round((perf_counter() - started) * 1000, 3))

    def _latency_summary(self) -> dict[str, dict[str, float]]:
        return {name: self._percentiles(values) for name, values in self._latency_samples.items()}

    @staticmethod
    def _percentiles(values: deque[float]) -> dict[str, float]:
        ordered = sorted(values)
        if not ordered:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}

        def pick(percentile: float) -> float:
            index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
            return ordered[index]

        return {
            "p50": pick(0.50),
            "p95": pick(0.95),
            "p99": pick(0.99),
            "max": ordered[-1],
        }

    @property
    def _current_batch_size(self) -> int:
        return self._fast_forward_batch_size if self._fast_forward_enabled else self._normal_batch_size

    @property
    def _current_interval_seconds(self) -> float:
        return self._fast_forward_interval_seconds if self._fast_forward_enabled else self._normal_interval_seconds
