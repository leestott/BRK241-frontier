# SOP-FOC-003 — High Attenuation / BER Degradation

**Applies to:** signal_type ∈ {`high_attenuation`, `ber_degradation`}.

## Triage Steps
1. Verify with a second probe to rule out transient interference.
2. Run an OTDR sweep against the suspect segment to localise the fault.
3. Check environmental signals (temperature, recent civils work tickets).
4. If attenuation > 3 dB above baseline, treat as **Medium**; > 6 dB treat as **High**.

## Dispatch Rules
- Engineer must hold OTDR certification.
- Same region engineer preferred to avoid SLA breach.

## Communication
- Notify NOC channel with the OTDR trace summary.
- Open a ticket in D365 Field Service with `category = optical_degradation`.
