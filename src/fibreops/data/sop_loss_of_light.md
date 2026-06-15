# SOP-FOC-001 — Fibre Optic Cable Loss of Light

**Applies to:** signal_type = `loss_of_light` on access or backhaul fibre.

## Triage Steps
1. Confirm alarm by polling the OLT/DWDM endpoint for the affected node.
2. Cross-check upstream nodes; if upstream also dark, escalate to **SOP-FOC-002** (upstream cascade).
3. Pull OTDR baseline for the segment and compare to last good trace.
4. Identify customers impacted via inventory; if customers_served > 5000, **auto-raise priority to High**.

## Dispatch Rules
- Splicing-capable engineer required.
- Prefer engineers within 10 km of the affected node and currently on shift.
- For Critical incidents, also notify the NOC duty manager via Teams.

## Communication
- Initial customer notice within 15 minutes via NOC channel.
- Status updates every 30 minutes until restore.

## Restore Criteria
- Optical power restored above -22 dBm at receiver.
- BER below 1e-9 for 5 consecutive minutes.
