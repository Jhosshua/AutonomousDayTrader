# Ride the Trend release verified

Commit `c8194ebbd0dc51b0e7dfb34e9807bb06528b5ee6` is pushed to `origin/main`. Railway deployment `8c5fdcdf-f426-4a15-b6d6-8bdb8d65d8a0` is **SUCCESS**.

Live verification at 2026-09-27T22:10:39.342772+00:00: HTTP 200, healthy; policy `V2_NBBO_ADDONS_AUDIT_2026_09_28`; `v2_live`, add-ons enforced; 9/9 profiles with five prior sessions and no build errors; durable checkpoint restored; paper account flat at $49,702.10, no equity drift or broker mismatch.

All 1,195 backend/end-to-end tests passed, frontend build and UI checks passed, and four historical replays passed. Constructed LONG and SHORT sessions passed the real tape/profile entry path. The real historical replays produced zero Ride the Trend signals. No new real-market scan or fill is claimed from these Sunday checks.

Live Chrome QA used the installed browser-use route against local Chrome. Desktop 1440px and mobile 390px showed the deployed card, enforced add-ons, 9/9 profile readiness and correct empty refusal state, with no overflow. Populated simulated cards displayed all nine refusal rows at 1440/390/320px. Screenshots and logs are beside this file. The audit tab was closed and the task-started browser daemon stopped; other tabs were preserved.

[Committed audit and fixes](/Users/mo/AutonomousDayTrader/docs/ride_the_trend_v2/AUDIT_2026_09_27.md)

[Live dashboard](https://autonomousdaytrader-production.up.railway.app/)

Order-book depth vendor selection remains open. The deployed book layer is NBBO.
