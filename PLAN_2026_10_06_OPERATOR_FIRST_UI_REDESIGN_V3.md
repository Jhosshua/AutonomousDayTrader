# Plan 2026 10 06 Operator first UI redesign Revision 3

Status

Research complete. Two attack reviews complete. Revision 4 below wins over earlier sections. No product implementation is authorized.

## 1. Correction

The previous concept underweighted performance.

It showed balance and dollar movement, but percent change was not prominent. History was a small recent list instead of a first class exploration surface. The new design corrects both problems.

## 2. Goal

The page must answer these questions in order.

1. How much am I up or down
2. What changed during the selected period
3. Does anything need me now
4. What is the robot doing
5. What is held and how is each holding performing
6. What happens next
7. What happened on each day, in each plan, and in each trade
8. What can I safely control

## 3. Recommended direction

The recommended direction is Cobalt Ledger.

It combines a strong performance surface with quiet operator rows.

1. Performance receives the largest visual area.
2. Required actions interrupt the normal hierarchy only when real action is required.
3. Holdings show both dollar and percent result.
4. History supports Day, Week, Month, and All.
5. History can be broken down by day, plan, or trade.
6. Every period shows a signed dollar value, a signed percent value, and the comparison basis.
7. One selected row reveals full detail without opening several competing cards.

## 4. Research basis

The detailed research is in `docs/operator_first_ui_v2/RESEARCH_2026_10_06.md`.

The design uses these principles.

1. Carbon hierarchy and exploration as supporting guidance.
2. Intentional emphasis through color, shape, size, motion, and containment.
3. Spectrum 2 neutral surfaces, diverging performance color, and limited category color as supporting guidance.
4. USWDS and W3C chart accessibility, text equivalents, reflow, and non color cues.
5. Native HTML first and React Aria Components only where native controls are insufficient.

## 5. Framework decision

Keep Next 15, React 19, Lucide, Instrument Sans, and Bricolage Grotesque.

Use native HTML first. Evaluate React Aria Components only for complex controls and dense data interaction.

Run an isolated Tailwind 4.3 compatibility test before implementation. Do not combine the migration with the redesign unless the test proves low risk on supported browsers.

Do not evaluate Base UI at the same time. Do not import Material, Spectrum, or Carbon component libraries. Their principles inform the design. Their visual language does not replace the product identity.

Any added package must use an exact version and pass license, vulnerability, registry, and lockfile review.

## 6. Performance model

### 6.1 Today

Show these values together.

1. Current equity.
2. Account change today in dollars.
3. Account change today in percent.
4. Finished trades today.
5. Open holdings since entry.
6. Comparison label using today opening equity.

Today percent is calculated as current equity minus daily opening equity, divided by daily opening equity.

The Day chart shows cumulative finished trade result by close time. It is labeled clearly. It is not presented as account equity.

### 6.2 Week

Week means the current day and previous six calendar dates.

Show these values.

1. Earliest complete opening equity in the period.
2. Current equity.
3. Dollar change.
4. Percent change.
5. Best day.
6. Worst day.
7. Finished trade count.
8. Closing equity points by trading session plus the current live point.

### 6.3 Month

Month means the current day and previous twenty nine calendar dates.

Use the same measures as Week.

### 6.4 All

Show change from the earliest complete durable opening equity through current equity. Label it Since recorded history.

If the full ledger is truncated, period totals remain based on complete session summaries. Trade rows state that only the most recent trades are loaded.

### 6.5 Historical day

Each day row shows these facts.

1. Date.
2. Opening equity.
3. Closing equity.
4. Dollar change.
5. Percent change.
6. Finished trade result when known.
7. Trades won and lost when known.
8. A clear daily total only label when individual trade records are unavailable.

### 6.6 Holding

Each holding row shows these facts.

1. Company and symbol.
2. Shares and direction.
3. Dollar result.
4. Percent result.
5. Protection price.
6. Next automatic action.

The percent comes from the existing holding snapshot and means result since entry. It is not derived from account performance.

## 7. Information architecture

### 7.1 Global shell

1. Product name.
2. Practice money label.
3. Live, reconnecting, or stale state.
4. Current Eastern time.
5. Today, Holdings, History, Plans, and Controls.

### 7.2 Performance

This is the default first section.

1. Equity.
2. Signed dollar and percent movement.
3. Day, Week, Month, and All controls.
4. One line or area chart.
5. Comparison basis.
6. Finished trades today and open holdings since entry as separate facts.
7. Best and worst period facts when useful.

### 7.3 Needs you

This appears directly under Performance only when operator action is required.

1. Required action count.
2. Plain problem.
3. What remains protected or paused.
4. One canonical action.
5. Evidence behind a disclosure.

Automatic retries and information do not inflate the required action count.

### 7.4 Now and next

One compact rail shows these facts.

1. What the robot is managing.
2. Earliest automatic action.
3. The next three authoritative events.
4. Daily loss limit remaining.

### 7.5 Holdings

Use compact selectable rows.

One selected row reveals prices, plan, connection, protection, and actions.

Filters never hide exposure. They may reorder or focus rows while preserving a visible total and a show all control.

### 7.6 History explorer

Today shows five recent history rows. The complete explorer exists only in History.

Controls include these choices.

1. Period using Day, Week, Month, and All.
2. Breakdown using Days, Plans, and Trades.

The selected period updates the performance chart, values, and history rows as one atomic change.

Selecting a day opens its plans and trades. Selecting a plan opens its days and trades. Selecting a trade opens entry, exit, shares, direction, result, and exit reason.

### 7.7 Plans

Plans remain a quiet list after History.

Each closed row shows state, next event, today result, and a small identity mark.

### 7.8 Controls

Global controls remain in one destination.

Contextual holding actions remain in the selected holding detail.

The same action does not appear twice on the same screen.

## 8. Desktop layout

Use a twelve column layout.

1. Header spans twelve columns.
2. Performance spans eight columns.
3. Now and next spans four columns.
4. Needs you spans twelve columns when present.
5. Holdings span eight columns.
6. Five recent history rows span four columns.
7. The full explorer exists only in History.
8. Plans span eight columns.
9. Controls span four columns.

At 1024 pixels, Performance and Now and next remain side by side. Holdings and History stack if either becomes cramped.

## 9. Phone layout

The phone order is fixed.

1. Header and connection.
2. Required action when present.
3. Equity, signed dollars, and signed percent.
4. Period controls.
5. Performance chart.
6. Robot state and next event.
7. Holdings.
8. History rows.
9. Plans.
10. Controls.
11. Sticky bottom navigation.

The first phone screen must show live state, equity, dollars, percent, and the selected period.

The second screen must show the chart, robot state, and next event.

## 10. Visual system

### 10.1 Palette

1. Mineral background.
2. Opaque white surfaces.
3. Deep ink text.
4. Cobalt product accent.
5. Emerald positive.
6. Coral negative.
7. Amber required action.
8. Neutral gray information.

### 10.2 Typography

1. Bricolage Grotesque for the product name only.
2. Instrument Sans for every label and number.
3. Tabular figures for money, percent, shares, prices, and time.
4. No supporting text below 12 pixels.

### 10.3 Shape

1. Performance surface uses a twelve pixel radius.
2. Standard surfaces use an eight pixel radius.
3. Dense rows use separators instead of individual cards.
4. Pills are reserved for selected periods and short states.

### 10.4 Color budget

1. One primary accent.
2. Positive and negative performance colors.
3. One required action color.
4. Small plan identity marks.
5. No full rainbow plan rows.

## 11. Motion

1. Period changes move the line and values over 180 to 220 milliseconds.
2. A changed dollar or percent value receives one 900 millisecond background highlight.
3. A selected holding opens over 180 milliseconds.
4. A required action expands once.
5. No idle bobbing.
6. No infinite breathing.
7. No count up animation that displays false money.
8. Reduced motion removes movement while keeping the final state.

## 12. Accessibility

1. The chart has a text summary.
2. Every chart point has a matching history row.
3. Positive and negative values use signs, words, and direction marks in addition to color.
4. Controls remain at least 44 pixels on phone.
5. The page reflows at 320 pixels without horizontal scrolling.
6. Focus order follows the visible reading order.
7. Period controls use one tab stop pattern when implemented with tabs.
8. Dynamic period changes use one polite announcement.
9. Required action uses one assertive announcement.
10. Trade detail returns focus to the originating row.

## 13. Data states

The design must cover these states.

1. Positive day.
2. Negative day.
3. Flat day.
4. No finished trades.
5. Holdings with open gains and losses.
6. Several required actions.
7. Automatic retry only.
8. Reconnecting.
9. Stale prices.
10. Durable history unavailable.
11. Daily total only history.
12. More than 2,000 loaded trades.
13. Slow trade mode.
14. Overnight planned.
15. Overnight held.
16. Market closed.

## 14. Prototype controls

The revised prototype will support these interactions.

1. Desktop and phone.
2. Day, Week, Month, and All.
3. Days, Plans, and Trades.
4. Normal, required action, and reconnecting states.
5. Holding selection.
6. Day detail selection.
7. Trade detail.
8. Destructive action confirmation and verified result.

## 15. Verification

1. Test every period in desktop and phone.
2. Test every breakdown in desktop and phone.
3. Test every operating state in desktop and phone.
4. Check signed dollars and percentages against the displayed comparison basis.
5. Check that daily account changes reconcile to period account change when history is complete.
6. Check that plan and trade rows reconcile only where complete detail exists.
7. Check that daily total only rows never invent trades.
8. Check that recovered totals never overlap detailed rows for the same session.
9. Check 320, 360, 390, 736, 768, 1024, 1280, and 1440 pixel widths.
10. Check keyboard order and return focus.
11. Check reduced motion.
12. Check no console errors.
13. Check no horizontal scrolling.

## 16. Implementation sequence

1. Add daily starting equity to the frontend account type.
2. Prevent recovered session totals from overlapping detailed trade rows.
3. Add complete period strategy aggregates or label Plan results partial.
4. Add completeness and reconciliation states.
5. Add pure performance aggregation helpers with tests.
6. Add signed dollar and percent values to the current summary.
7. Build the history explorer behind fixture data.
8. Connect the current all ledger.
9. Add daily, plan, and trade drilldown.
10. Replace the existing Results panel only after parity tests pass.
11. Add holding percentages to compact rows.
12. Reorder the page around Performance, Needs you, Now and next, Holdings, and recent History.
13. Add motion last.
14. Run current regression harnesses without raising existing caps.

## 17. Decisions still required

1. Whether open holding result belongs in historical period return after the current day.
2. How to treat deposits or withdrawals if they are ever added.
3. Whether a Tailwind 4.3 compatibility test justifies a separate migration.

## 18. Attack review

Two independent agents returned ten P0 findings and nine P1 findings.

Accepted corrections include these items.

1. Day cannot show an intraday equity line without stored equity snapshots.
2. Account change, finished trade result, and open result since entry are separate facts.
3. Week, Month, and All definitions are locked.
4. Since recorded history replaces an ambiguous All baseline.
5. Recovered daily totals cannot overlap detailed rows.
6. Plan totals need complete server aggregates or a partial label.
7. Best and worst day rank by account percent change, then show percent and dollars.
8. Material 3 Expressive is not the visual foundation.
9. Cobalt is the only product accent.
10. Surfaces are opaque.
11. Today shows only five recent history rows.
12. The complete explorer exists only in History.
13. Native HTML and React Aria are the only proposed interaction systems.
14. The chart uses a custom SVG with one straight line, direct labels, a zero line, and an optional eight percent fill.
15. The first prototype is light mode only.

Revision 4 is represented by the corrected sections above. Where an older statement conflicts, the corrected statement wins.
