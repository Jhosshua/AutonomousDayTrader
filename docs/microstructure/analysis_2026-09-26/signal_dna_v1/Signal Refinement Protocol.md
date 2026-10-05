# Signal Refinement Protocol — research status

**Decision: no new live POST threshold is justified.** `approved_T_threshold` and `approved_lambda_threshold_shares_per_second` remain null. This audit delivers measurable research hypotheses, not a separator that captures all 28 peaks.

1. **Validate the observation before scoring.** Reconstruct the full order-level book without unresolved gaps; require a valid uncrossed book and warmup. Use information visible at quote decision time after the stated feed delay. Retain Type P trades as unknown sign. Require signed-rate coverage ≥0.60, signed rate ≥1 share/s, signed trade age ≤5 seconds, and defined imbalance/velocity. These thresholds were fixed before this comparison as research data-quality requirements; they are not fitted toxicity cutoffs. They retain only 16 of the 28 peaks, which must be reported rather than concealed.

2. **Keep the mathematical inputs separate.** With τ=1 second, λ±(t)=Σ qᵢ exp[−(t−tᵢ)/τ]/τ for signed trades; λunknown is separate. I=(λ+−λ−)/(λ++λ−). Vagg=[I(t)−I(t−0.1s)]/0.1s, in s⁻¹. T is the existing signed-volume VPIN proxy, dimensionless and uncalibrated. Same-side effective depth sums every displayed level with exp(−distance/$0.10) weights. Contra pressure is λsell/depthbid for a buy quote and λbuy/depthask for a sell quote, in s⁻¹. Keep raw λ and raw one-second price volatility as independent inputs. A common exponential decay changes λ but leaves I unchanged; do not mine machine-roundoff velocity as information.

3. **Freeze two targeted shadow hypotheses.** The exploratory flow/depth rule is total λ >17.61783105 shares/s and contra pressure >0.02979401533/s, after the quality checks. The sparse velocity hypothesis is side × Vagg <−10⁻¹²/s; its threshold distinguishes resolved negative change from numerical noise, not an economically calibrated acceleration. The first rule has 36.4% peak precision in date-held-out fitting-procedure validation and negative selected P/L. The second was identified after viewing associations and has only four labeled examples on two dates (three after quality checks). Neither permits POST. Record both passes and rejections with candidate ID, book/feature cutoff, queue, latency, coverage, unsigned rate and reason codes.

4. **Use a target consistent with the Gatekeeper.** Keep interior-peak occurrence as an auxiliary shape diagnostic. Primary labels must be fill-before-expiry, signed execution markout at the fixed 100 ms and 1 s horizons, and net value under the already-fixed exits. A signed execution markout is M_H=s[m(t_fill+H)−p_fill] in USD/share; adverse execution markout is M_H<0. If isolating post-fill price drift, also record A_H=−s[m(t_fill+H)−m(t_fill)] separately. Do not add markout to realized exit P/L and count the same price movement twice. The peak classifier is not P(adverse | fill).

5. **Require economic and prediction evidence before release.** The pre-analysis research criteria require at least 20 out-of-date selections across four dates, peak precision ≥50%, recall ≥25%, and a positive lower date-cluster confidence bound for fixed-policy mean under both existing fill assumptions. These criteria were not met. A future live gate additionally needs an untouched prospective sample, calibrated fill/adverse probabilities with uncertainty, positive expected net value under the intended passive policy, queue/latency sensitivity, and the complete inventory-and-exit replay. No current result substitutes for that evidence.

```text
audit_candidate(candidate, visible_full_depth, tape):
    features = causal_features_at_visible_cutoff(candidate, visible_full_depth, tape)
    if quality_failure(features):
        return WAIT, quality_reason
    tag_flow_depth = total_lambda > 17.61783105 and contra_pressure > 0.02979401533
    tag_velocity = side * Vagg < -1e-12
    append_shadow_log(features, tags, queue_position, latency_assumptions)
    if calibrated_passive_net_value_model_is_unavailable:
        return WAIT, MODEL_UNCALIBRATED
    if validated_expected_net_value_lower_bound <= 0:
        return WAIT, NO_VALIDATED_NET_EDGE
    return POST_ONLY, model_version_and_economic_reason
```

The pseudocode describes qualification for a future Gatekeeper, not a change to current running strategies. If an order is already working, the same quality/value evaluation must issue a cancel request under the real cancel latency, allowing fills until cancellation acknowledgement. Existing protective exits remain part of the strategy convention. A venue-compatible full-depth feed and verified post-only route are still prerequisites; archived ITCH and simulated ladder fills do not establish either.

For the next targeted observation, retain the current hypotheses unchanged, collect event-level states at actual quote decisions, and compare negative aligned-velocity cases with matched same-date, side, activity and coverage controls. Log the surrounding 1 s and 5 s evolution to test whether the 100 ms burst survives the 251 ms compute/outbound delay. Any newly designed horizon becomes a new version requiring prospective validation. Do not optimize offsets again.
