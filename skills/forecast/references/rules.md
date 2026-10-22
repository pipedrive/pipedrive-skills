# Forecast calculation rules

Forecast calculates from saved responses only. For every deal, probability precedence is: deal probability, varying CRM stage probability, accepted local ladder, then unweighted.

Weighted forecast is won-to-date plus open value times base probability. Adjusted forecast uses the single most cautious applicable haircut; haircuts never compound. Best case is won-to-date plus included open value. Slipped deals are open deals dated before the period start; they are included at slipped haircut by default and always shown as a subtotal.

All calculations are per currency. Forecast never converts currencies. Target comparisons use only the selected period/currency/lens. Coverage is open in-period pipeline divided by remaining target; bands are disclosed benchmarks. Activity evidence unavailable for some deals does not discard readable evidence, but marks the adjustment as potentially optimistic. Missing core stages or open deals stops the result. A pipeline whose stages lack Pipedrive's `order_nr` gets no out-of-order probability claim and no ladder offer; its stage breakdown keeps the order Pipedrive returned, and the output carries one line per pipeline: "Stage order not available for <pipeline name>; stage-order rules skipped."
