# App Insights / Log Analytics KQL Pack — FibreOps

Paste-ready queries for the BRK241 demo. Every query targets the standard
OpenTelemetry-on-Azure-Monitor tables (`dependencies`, `traces`) that the
`azure-monitor-opentelemetry` distro populates automatically — set
`APPLICATIONINSIGHTS_CONNECTION_STRING` in `.env` and every span the agents
emit lands in your workspace within ~60 seconds.

> **Telemetry shape** (see `src/fibreops/observability.py`):
>
> | Source                              | App Insights table | Name                            |
> | ----------------------------------- | ------------------ | ------------------------------- |
> | `orchestrator.handle_signal`        | `dependencies`     | `orchestrator.handle_signal`    |
> | `agent_span(...)`                   | `dependencies`     | `agent.IncidentAnalysisAgent` … |
> | `tool_span(...)`                    | `dependencies`     | `tool.create_ticket` …          |
> | `record_event("fibreops.optimiser.score", ...)` | `traces` | `fibreops.optimiser.score`    |
>
> **Common dimensions** (set on the orchestrator wrap span and every child):
> `signal_id`, `incident_id`, `node_id`, `severity`, `region`,
> `customers_served`, `decision`, `dispatched`, `dispatched_at_ms`.

---

## 1. Agent decision timeline (last hour)

The hero query — one row per orchestrator / agent / tool span, in order. Pin
this on the Insights tab during Act 5.

```kusto
dependencies
| where timestamp > ago(1h)
| where name startswith "agent." or name startswith "tool." or name == "orchestrator.handle_signal"
| project timestamp, span = name, duration_ms = duration, success,
          signal_id   = tostring(customDimensions.signal_id),
          incident_id = tostring(customDimensions.incident_id),
          node_id     = tostring(customDimensions.node_id),
          severity    = tostring(customDimensions.severity),
          decision    = tostring(customDimensions.decision),
          operation_Id
| order by timestamp asc
```

## 2. End-to-end latency per outage

How long from telemetry-in to engineer-dispatched? Click the `operation_Id`
to drill into the per-incident trace.

```kusto
dependencies
| where timestamp > ago(24h)
| where name == "orchestrator.handle_signal"
| extend signal_id  = tostring(customDimensions.signal_id),
         incident   = tostring(customDimensions.incident_id),
         node       = tostring(customDimensions.node_id),
         severity   = tostring(customDimensions.severity),
         dispatched = tobool(customDimensions.dispatched)
| project timestamp, signal_id, incident, node, severity, dispatched,
          e2e_ms = duration, success, operation_Id
| order by timestamp desc
```

## 3. Per-agent p50 / p95 latency (last 24h)

```kusto
dependencies
| where timestamp > ago(24h)
| where name startswith "agent."
| summarize count       = count(),
            p50_ms      = percentile(duration, 50),
            p95_ms      = percentile(duration, 95),
            failures    = countif(success == false)
            by agent = name
| order by p95_ms desc
```

## 4. Tool-call frequency and failure rate

Catches drift — if `tool.dispatch_engineer` starts failing more, you know
before users do.

```kusto
dependencies
| where timestamp > ago(24h)
| where name startswith "tool."
| summarize calls       = count(),
            failures    = countif(success == false),
            failure_pct = round(100.0 * countif(success == false) / count(), 2),
            avg_ms      = avg(duration)
            by tool = name
| order by calls desc
```

## 5. Optimiser score trend over time

Span events the optimiser emits — the slope tells you whether prompt
iterations are paying off.

```kusto
traces
| where timestamp > ago(7d)
| where message == "fibreops.optimiser.score"
| extend score    = todouble(customDimensions.score),
         run_id   = tostring(customDimensions.run_id),
         incident = tostring(customDimensions.incident_id)
| summarize avg_score = avg(score), runs = count() by bin(timestamp, 1h)
| render timechart
```

## 6. Which rubric criterion fails most often?

Drives the next prompt edit.

```kusto
traces
| where timestamp > ago(7d)
| where message == "fibreops.optimiser.criterion"
| extend criterion = tostring(customDimensions.criterion),
         passed    = tobool(customDimensions.passed)
| summarize evaluations = count(),
            failures    = countif(passed == false),
            fail_pct    = round(100.0 * countif(passed == false) / count(), 2)
            by criterion
| order by fail_pct desc
```

## 7. Dispatch SLA — were criticals dispatched within 5 minutes?

```kusto
dependencies
| where timestamp > ago(24h)
| where name == "orchestrator.handle_signal"
| extend severity         = tostring(customDimensions.severity),
         dispatched       = tobool(customDimensions.dispatched),
         dispatched_at_ms = todouble(customDimensions.dispatched_at_ms)
| where severity in ("critical", "high") and dispatched == true
| summarize within_5min = countif(dispatched_at_ms <= 5 * 60 * 1000),
            total       = count(),
            sla_pct     = round(100.0 * countif(dispatched_at_ms <= 5 * 60 * 1000) / count(), 2),
            p95_ms      = percentile(dispatched_at_ms, 95)
            by severity
```

## 8. Top 10 noisiest nodes (last 7 days)

Operational insight — repeat offenders need physical inspection, not more
agent runs.

```kusto
dependencies
| where timestamp > ago(7d)
| where name == "orchestrator.handle_signal"
| extend node     = tostring(customDimensions.node_id),
         severity = tostring(customDimensions.severity)
| summarize incidents = count(), criticals = countif(severity == "critical") by node
| order by incidents desc
| take 10
```

## 9. Teams card delivery success

Distinguish "we did the work" from "the notification actually landed".

```kusto
dependencies
| where timestamp > ago(24h)
| where name in ("tool.teams.post_status_update", "tool.teams.post_outage_notice")
| summarize sent     = count(),
            failed   = countif(success == false),
            avg_ms   = avg(duration)
            by tool = name
```

## 10. Full trace replay for one incident

Tie everything together — paste an incident ID into the parameter line and
get every span + event that touched it.

```kusto
let target_incident = "INC-XXXXXXXX";   // <-- paste here
union dependencies, traces
| where timestamp > ago(7d)
| where tostring(customDimensions.incident_id) == target_incident
| project timestamp, itemType, span_or_event = coalesce(name, message),
          duration, success, operation_Id, customDimensions
| order by timestamp asc
```

---

## Workbook ideas (optional, for the talk slide-deck)

* **Operations dashboard**: queries 1, 2, 3, 7 on a single grid.
* **Agent quality dashboard**: queries 5, 6, 4 — feeds product decisions.
* **Network health dashboard**: query 8 + an Azure Maps layer of nodes.

All three reuse the same telemetry schema — no extra emit code, no extra cost.
