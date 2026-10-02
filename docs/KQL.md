# Application Insights / Log Analytics KQL Pack — FibreOps

Queries for the BRK241 demo's Application Insights **Logs** view (classic
table names). Set `APPLICATIONINSIGHTS_CONNECTION_STRING` in your private
environment to enable Azure Monitor export. The application also writes
spans to `state/traces.jsonl`; verify telemetry ingestion in Application
Insights before relying on these cloud queries. In a Log Analytics workspace
using workspace table names, use `AppDependencies` instead of `dependencies`
and the corresponding PascalCase field names.

> **Telemetry shape** (see `src/fibreops/observability.py`):
>
> | Source                              | Application Insights table | Name                            |
> | ----------------------------------- | ------------------ | ------------------------------- |
> | `orchestrator.handle_signal`        | `dependencies`     | `orchestrator.handle_signal`    |
> | `agent_span(...)`                   | `dependencies`     | `agent.IncidentAnalysisAgent` … |
> | `tool_span(...)`                    | `dependencies`     | `tool.create_ticket` …          |
> | `optimiser.run`                   | `dependencies`     | `optimiser.run` (average score as a span attribute) |
>
> `record_event(...)` adds **OpenTelemetry span events**, not independently
> queryable `traces` rows. Individual criterion scores are in
> `state/optimiser_suggestions.jsonl` or `/api/optimiser`, not the
> `traces` table. The orchestrator/agent spans carry `signal_id`,
> `incident_id`, `node_id`, `severity`, `region`, and `customers_served`;
> `decision`, `dispatched`, and `dispatched_at_ms` are on the orchestrator
> span (and `decision` is also on the coordinator span).

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

## 5. Optimiser average score trend over time

Each optimiser run stores its average score on the `optimiser.run` span.
This is an average per run, not a separate score for every incident.

```kusto
dependencies
| where timestamp > ago(7d)
| where name == "optimiser.run"
| extend score = todouble(customDimensions.avg_score)
| where isnotnull(score)
| summarize avg_score = avg(score), evaluations = count() by bin(timestamp, 1h)
| render timechart
```

## 6. Which rubric criterion fails most often? (local state)

The per-criterion scores are saved in the optimiser summary, not as
Application Insights `traces`. Run the optimiser first, then inspect its
summary in PowerShell (or request `/api/optimiser` while signed in):

```powershell
$summary = Get-Content state\optimiser_suggestions.jsonl -Raw | ConvertFrom-Json
$summary.scores | ForEach-Object {
    $_.criteria.PSObject.Properties | Where-Object Value -lt 1 |
        Select-Object Name, Value
}
```

## 7. Dispatch latency — did criticals enter dispatch within 5 minutes?

`dispatched_at_ms` is recorded when the dispatch step finishes; this
attribute does **not** confirm that an engineer was successfully booked.

```kusto
dependencies
| where timestamp > ago(24h)
| where name == "orchestrator.handle_signal"
| extend severity         = tostring(customDimensions.severity),
         dispatched       = tobool(customDimensions.dispatched),
         dispatched_at_ms = todouble(customDimensions.dispatched_at_ms)
| where severity in ("critical", "high") and isnotnull(dispatched_at_ms)
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

## 9. Teams tool-call success

Counts tool calls, not confirmed delivery to Teams. With no webhook
configured, the tool writes to the local outbox instead.

```kusto
dependencies
| where timestamp > ago(24h)
| where name in ("tool.teams.post_status_update", "tool.teams.post_outage_notice")
| summarize sent     = count(),
            failed   = countif(success == false),
            avg_ms   = avg(duration)
            by tool = name
```

## 10. Span replay for one incident

Tie everything together — paste an incident ID into the parameter line and
get every exported span carrying that ID. This does not reconstruct
individual OpenTelemetry span events.

```kusto
let target_incident = "INC-XXXXXXXX";   // <-- paste here
dependencies
| where timestamp > ago(7d)
| where tostring(customDimensions.incident_id) == target_incident
| project timestamp, span = name,
          duration, success, operation_Id, customDimensions
| order by timestamp asc
```

---

## Workbook ideas (optional, for the talk slide-deck)

* **Operations dashboard**: queries 1, 2, 3, 7 on a single grid.
* **Agent quality dashboard**: queries 5 and 4, plus the local summary in 6.
* **Network health dashboard**: query 8 + an Azure Maps layer of nodes.

The local optimiser summary is separate from the exported span schema;
Azure Monitor ingestion may incur charges.
