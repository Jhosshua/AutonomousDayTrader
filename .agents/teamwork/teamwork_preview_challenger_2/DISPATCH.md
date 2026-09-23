# Dispatch: Challenger 2 (Concurrency, Race Condition & Margin Collision Stress Tester)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_challenger_2`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`

## Mission
Write adversarial test generators and stress harnesses attacking:
1. Concurrency races at 09:30:00 ET:
   - Simultaneous swing staged order fills + intraday strategy entries.
   - Sizing and buying power contention: Can intraday double-spend capital allocated for swing?
   - Max 2 swing positions limit ($25k each) under concurrent fill race.
2. Flattening races at 15:45-15:58 ET:
   - Does Phase 1 lockout, Phase 2 order purge, Phase 3 liquidation, or Phase 4 zero-audit ever touch a swing position, swing order, or swing stop under rapid event injection?
3. Symbol collision on `AMD`:
   - Rapid alternating order submissions between intraday ORB/VWAP strategies and swing engine on `AMD`. Verify that mutual exclusion never leaks or nets shares.

## Output Requirements
Write your detailed stress test report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_challenger_2/handoff.md`.
Conclude with a formal verdict: `APPROVE` or `REQUEST_CHANGES`.
Send a message back to the caller when complete.
