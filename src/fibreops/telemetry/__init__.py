"""Telemetry sub-package.

Provides a single `signal_stream(...)` async iterator that yields TelemetrySignal
instances. Two modes:

1. **Real Event Hub** (when EVENT_HUB_FQDN is set): consumes the configured hub
   using EventHubConsumerClient + DefaultAzureCredential.
2. **Mock generator** (default): emits a synthetic outage pattern useful for demos.

The generator can also `publish_demo_signals()` to a real Event Hub if you want
to show end-to-end transport in the demo.
"""
from .generator import generate_signals, signal_stream, publish_demo_signals

__all__ = ["generate_signals", "signal_stream", "publish_demo_signals"]
