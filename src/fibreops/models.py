"""Domain models — Pydantic v2."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SignalType(str, Enum):
    LOSS_OF_LIGHT = "loss_of_light"
    HIGH_ATTENUATION = "high_attenuation"
    BER_DEGRADATION = "ber_degradation"
    NODE_UNREACHABLE = "node_unreachable"


class FibreNode(BaseModel):
    node_id: str
    region: str
    site: str
    upstream: Optional[str] = None
    customers_served: int = 0
    criticality: Severity = Severity.MEDIUM


class TelemetrySignal(BaseModel):
    signal_id: str = Field(default_factory=lambda: f"sig-{uuid4().hex[:10]}")
    timestamp: datetime = Field(default_factory=_utcnow)
    node_id: str
    signal_type: SignalType
    severity: Severity
    measured_value: float
    unit: str
    raw: dict = Field(default_factory=dict)


class Engineer(BaseModel):
    engineer_id: str
    name: str
    region: str
    skills: list[str]
    on_shift: bool = True
    distance_km_from_node: dict[str, float] = Field(default_factory=dict)


class IncidentAnalysis(BaseModel):
    incident_id: str = Field(default_factory=lambda: f"INC-{uuid4().hex[:8].upper()}")
    signal_id: str
    node_id: str
    severity: Severity
    summary: str
    probable_cause: str
    customer_impact: str
    recommended_actions: list[str]
    sop_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class Ticket(BaseModel):
    ticket_id: str
    incident_id: str
    node_id: str
    severity: Severity
    title: str
    description: str
    status: Literal["new", "assigned", "in_progress", "resolved"] = "new"
    assignee: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class DispatchResult(BaseModel):
    engineer_id: str
    engineer_name: str
    eta_minutes: int
    ticket_id: str
    notes: str
