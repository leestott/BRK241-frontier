from fibreops.telemetry import generate_signals
from fibreops.models import Severity, SignalType


def test_generates_at_least_one_critical_loss_of_light():
    sigs = generate_signals(count=4, include_critical=True, seed=1)
    assert any(s.severity == Severity.CRITICAL and s.signal_type == SignalType.LOSS_OF_LIGHT for s in sigs)
    assert len(sigs) == 4


def test_deterministic_with_seed():
    a = generate_signals(count=5, seed=42)
    b = generate_signals(count=5, seed=42)
    assert [s.signal_type for s in a] == [s.signal_type for s in b]
    assert [s.node_id for s in a] == [s.node_id for s in b]
