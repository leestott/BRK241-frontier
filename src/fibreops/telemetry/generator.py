"""IoT telemetry — mock generator + Event Hub publisher / consumer.

The demo defaults to the in-process generator so it always runs end-to-end even
without an Event Hub provisioned. Setting EVENT_HUB_FQDN (and authenticating
with `az login`) flips the consumer over to the real namespace.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable

from ..config import get_settings
from ..mocks import load_json
from ..models import FibreNode, Severity, SignalType, TelemetrySignal
from ..observability import get_logger

logger = get_logger(__name__)


def _nodes() -> list[FibreNode]:
    return [FibreNode(**raw) for raw in load_json("fibre_nodes.json")]


def generate_signals(
    *,
    seed: int | None = 7,
    include_critical: bool = True,
    count: int = 5,
) -> list[TelemetrySignal]:
    """Produce a deterministic burst of telemetry signals for the demo.

    Always includes at least one CRITICAL loss-of-light against a high-customer
    node so the agent flow exercises every branch.
    """
    rng = random.Random(seed)
    nodes = _nodes()
    signals: list[TelemetrySignal] = []

    if include_critical:
        critical_node = next(n for n in nodes if n.criticality == Severity.CRITICAL)
        signals.append(
            TelemetrySignal(
                node_id=critical_node.node_id,
                signal_type=SignalType.LOSS_OF_LIGHT,
                severity=Severity.CRITICAL,
                measured_value=-40.0,
                unit="dBm",
                raw={
                    "olt_port": "1/1/3",
                    "last_good_dbm": -18.4,
                    "consecutive_polls_dark": 6,
                },
            )
        )

    for _ in range(max(0, count - len(signals))):
        node = rng.choice(nodes)
        kind = rng.choice(list(SignalType))
        if kind == SignalType.LOSS_OF_LIGHT:
            value, unit, sev = -38.0 + rng.uniform(-2, 2), "dBm", Severity.HIGH
        elif kind == SignalType.HIGH_ATTENUATION:
            value, unit, sev = 6.2 + rng.uniform(0, 2.5), "dB", Severity.MEDIUM
        elif kind == SignalType.BER_DEGRADATION:
            value, unit, sev = 1e-6, "ratio", Severity.MEDIUM
        else:
            value, unit, sev = 1.0, "bool", Severity.HIGH
        signals.append(
            TelemetrySignal(
                node_id=node.node_id,
                signal_type=kind,
                severity=sev,
                measured_value=value,
                unit=unit,
                raw={"source": "mock-generator"},
            )
        )
    return signals


async def _mock_stream(signals: list[TelemetrySignal], interval_s: float) -> AsyncIterator[TelemetrySignal]:
    for sig in signals:
        await asyncio.sleep(interval_s)
        logger.info("telemetry signal emitted", extra={"signal_id": sig.signal_id})
        yield sig


async def _eventhub_stream() -> AsyncIterator[TelemetrySignal]:
    settings = get_settings()
    from azure.eventhub.aio import EventHubConsumerClient
    from azure.identity.aio import DefaultAzureCredential

    queue: asyncio.Queue[TelemetrySignal] = asyncio.Queue()

    async def on_event(partition_context, event) -> None:
        try:
            payload = json.loads(event.body_as_str())
            sig = TelemetrySignal(**payload)
        except Exception as exc:  # pragma: no cover
            logger.warning("dropping malformed event: %s", exc)
            return
        await queue.put(sig)
        await partition_context.update_checkpoint(event)

    credential = DefaultAzureCredential()
    client = EventHubConsumerClient(
        fully_qualified_namespace=settings.event_hub_fqdn,
        eventhub_name=settings.event_hub_name,
        consumer_group=settings.event_hub_consumer_group,
        credential=credential,
    )
    task = asyncio.create_task(client.receive(on_event=on_event, starting_position="-1"))
    try:
        while True:
            sig = await queue.get()
            yield sig
    finally:
        task.cancel()
        await client.close()
        await credential.close()


async def signal_stream(
    *,
    use_event_hub: bool | None = None,
    mock_signals: Iterable[TelemetrySignal] | None = None,
    interval_s: float = 0.4,
) -> AsyncIterator[TelemetrySignal]:
    settings = get_settings()
    use_eh = settings.event_hub_enabled if use_event_hub is None else use_event_hub
    if use_eh:
        logger.info("subscribing to Event Hub %s/%s", settings.event_hub_fqdn, settings.event_hub_name)
        async for sig in _eventhub_stream():
            yield sig
        return
    sigs = list(mock_signals) if mock_signals is not None else generate_signals()
    async for sig in _mock_stream(sigs, interval_s):
        yield sig


async def publish_demo_signals(signals: list[TelemetrySignal]) -> int:
    """Publish a batch of signals to the real Event Hub. Returns the count sent."""
    settings = get_settings()
    if not settings.event_hub_enabled:
        raise RuntimeError("EVENT_HUB_FQDN is not set; cannot publish demo signals")
    from azure.eventhub import EventData
    from azure.eventhub.aio import EventHubProducerClient
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    producer = EventHubProducerClient(
        fully_qualified_namespace=settings.event_hub_fqdn,
        eventhub_name=settings.event_hub_name,
        credential=credential,
    )
    try:
        batch = await producer.create_batch()
        for sig in signals:
            batch.add(EventData(sig.model_dump_json()))
        await producer.send_batch(batch)
        logger.info("published %d telemetry signals to %s", len(signals), settings.event_hub_name)
        return len(signals)
    finally:
        await producer.close()
        await credential.close()


__all__ = [
    "TelemetrySignal",
    "generate_signals",
    "signal_stream",
    "publish_demo_signals",
]
