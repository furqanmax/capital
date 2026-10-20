"""Formula catalogue for the Saif Stock Market workbook.

Every business rule that this application re-implements is declared here as
structured metadata:

``original``   — the exact behaviour of the supplied workbook (including the
                 defects recorded in :data:`AUDIT_FINDINGS`),
``corrected``  — the documented intent, applied only when the user opts in
                 (``formulas.mode = "corrected"``).

Nothing in this module is computed here; it is a declarative specification that
:mod:`services.analytics`, :mod:`services.formula_engine` and the audit/report
tooling all consume.  Keeping the specification in one place is what lets the
Formula Lab, the parity tests and the audit log stay consistent.
"""

from __future__ import annotations

from typing import Any, Dict, List

# --------------------------------------------------------------------------
# Moving-average / baseline parameters discovered in the workbook
# --------------------------------------------------------------------------

MOVING_AVERAGE_DAYS = 25          # Analysis!Q13  ("Days")
DELIVERY_BASELINE_WINDOW = 25     # Analysis rows 15..200 -> ~25 conventions
LOOKBACK_WINDOWS = (1, 3, 5, 7, 10)

# Analysis!S (V1 jackpot) multipliers on the Delivery/Action baselines
V1_MULTIPLIERS = (
    ("Spl", 2.0, 2.0),
    ("Trpl", 1.6, 1.6),
    ("Dbl", 1.3, 1.3),
    ("J", 1.0, 1.0),
)

# --------------------------------------------------------------------------
# Audit findings — preserved, never silently "fixed"
# --------------------------------------------------------------------------

AUDIT_FINDINGS: List[Dict[str, Any]] = [
    {
        "id": "B1",
        "severity": "high",
        "sheet": "Analysis",
        "cells": "I4:I6",
        "title": "5D/7D/10D CMP windows are 4 rows instead of 5/7/10",
        "excel_formula": "='AVERAGE($G$15:G18)'  (identical in I4, I5 and I6)",
        "expected": "I4=AVERAGE($G$15:G19), I5=AVERAGE($G$15:G21), I6=AVERAGE($G$15:G24)",
        "evidence": "Cached values for I4, I5 and I6 are all 460.25, i.e. the 4-row "
                    "average of G15:G18. A correct 10-day average is 464.51.",
        "business_impact": "The 'CMP' summary for 5/7/10 sessions understates the price "
                           "window and therefore the CMP-vs-AvgPrice verdict.",
        "resolution": "Replay as-is in 'original' mode; use the 5/7/10-row windows in "
                      "'corrected' mode. Requires user sign-off to change the default.",
    },
    {
        "id": "B2",
        "severity": "high",
        "sheet": "Analysis",
        "cells": "L4:L6",
        "title": "5D/7D/10D Delivery-% windows are 4 rows instead of 5/7/10",
        "excel_formula": "='AVERAGE($L$15:L18)'  (identical in L4, L5 and L6)",
        "expected": "L4=AVERAGE($L$15:L19), L5=AVERAGE($L$15:L21), L6=AVERAGE($L$15:L24)",
        "evidence": "Cached L4 = L5 = L6 = 27.2125 (the 4-row mean 108.85/4).",
        "business_impact": "Delivery-percentage summaries for longer windows are wrong, "
                           "which feeds the delivery classifications.",
        "resolution": "Same dual-mode policy as B1.",
    },
    {
        "id": "B3",
        "severity": "critical",
        "sheet": "Analysis",
        "cells": "Q15:Q200 (helper counter AA15:AA43)",
        "title": "The volume moving average is #VALUE! for every row except the first four, "
                 "and those four average FUTURE sessions",
        "excel_formula": "='AVERAGE(OFFSET(data[[#This Row],[TOTTRDQTY]],0,0,-$Q$13+AA15,1))'",
        "expected": "A backward-only window of min(25, sessions available) per row.",
        "evidence": "AA is a countdown helper: AA43 = 1, AA{r} = AA{r+1}+1, so AA{r} = 44-r "
                    "(AA15 = 29) and it is blank from row 44. The OFFSET height is therefore "
                    "-25+AA = 4-i for data index i = r-15. Row 15 (i=0) averages rows 15..18 - "
                    "four FUTURE sessions; row 19 (i=4) has height 0 and rows 20+ have a "
                    "negative height that reaches above the table's data body, which Excel "
                    "rejects on a structured reference. Cached values confirm it: Q15:Q18 are "
                    "numbers (6286342, 7912200.33, 7463366, 2853849) and Q19:Q200 are all "
                    "#VALUE!. The Mov Avg column, the Vol Level column (R) and the "
                    "BRONZE/SILVER/GOLD columns (T/U/V) are consequently dead for 182 of the "
                    "186 sessions.",
        "business_impact": "Every volume-based signal in the workbook is unusable: the volume "
                           "MA, the Above/Below volume level and all three volume badges only "
                           "ever evaluate on the first four rows of the block, and there they "
                           "are computed from sessions that had not happened yet.",
        "resolution": "Replay the four forward-looking values and the #VALUE! rows exactly in "
                      "'original' mode (the parity suite matches all of them); use the "
                      "documented backward, capped window in 'corrected' mode.",
    },
    {
        "id": "B4",
        "severity": "high",
        "sheet": "Analysis vs Main",
        "cells": "Analysis!T15:V200 vs Main!I18:I842",
        "title": "SILVER/GOLD volume thresholds contradict between Analysis and Main",
        "excel_formula": "Analysis: I<Q -> BRONZE, I<0.75*Q -> SILVER, I<0.5*Q -> GOLD",
        "expected": "Main + legend: Vol < 1.0*Avg -> BRONZE, < 1.5*Avg -> SILVER, < 2.0*Avg -> GOLD",
        "evidence": "The Analysis legend (S7/S8) and the Main legend (N22:N25) both describe "
                    "the *increasing* 1.0/1.5/2.0 ladder used by Main, while the Analysis "
                    "column formulas use a *decreasing* 1.0/0.75/0.5 ladder.",
        "business_impact": "The same badge means different things on the two sheets; a stock "
                           "can be GOLD in Analysis and blank in Main for the same day.",
        "resolution": "Both ladders are exposed as named rule variants; the audit log records "
                      "which ladder produced each badge.",
    },
    {
        "id": "B5",
        "severity": "critical",
        "sheet": "Main",
        "cells": "I18:I842",
        "title": "DV1 reads the volume of the wrong row (D{r+38})",
        "excel_formula": "IF(AND(B18>60, D18<E18), \"BRONZE\", IF(AND(B18>60, D18<1.5*E18), "
                          "\"SILVER\", IF(AND(B18>60, D56<2*E18), \"GOLD\", \"\")))",
        "expected": "The third comparison must use D18 (the current row), not D56.",
        "evidence": "Row 18 -> D56, row 19 -> D57 ... row 842 -> D880: a constant +38 row "
                    "offset applied to all 825 rows. D880 is outside the populated range.",
        "business_impact": "GOLD can never be awarded correctly: the volume test is taken "
                           "from an unrelated stock 38 rows further down.",
        "resolution": "Default 'original' mode reproduces the offset exactly (so parity "
                      "checks pass); 'corrected' mode uses the current row and flags the "
                      "difference in the Formula Audit Log.",
    },
    {
        "id": "B6",
        "severity": "medium",
        "sheet": "Main",
        "cells": "J18:J842",
        "title": "DV2 mis-buckets exact boundary values",
        "excel_formula": "IF(B<40,\"Low Del\",IF(B>80,\"80% Plus\",IF(B>60,\"60% Plus\","
                          "IF(B>50,\"50% Plus\",IF(B>40,\"40% Plus\",\"\")))))",
        "expected": "Boundaries 40/50/60/80 should land in a defined bucket.",
        "evidence": "Delivery exactly 60 evaluates to '50% Plus' (the B>60 test fails, "
                    "B>50 succeeds); exactly 40/80 can fall through to an empty string.",
        "business_impact": "Boundary rows are labelled one bucket too low or left blank.",
        "resolution": "Replay the inclusive/exclusive chain in 'original' mode; use "
                      "documented boundary-closed buckets in 'corrected' mode.",
    },
    {
        "id": "B7",
        "severity": "low",
        "sheet": "Analysis",
        "cells": "M13, P13",
        "title": "AVERAGEIF(\"<>#VALUE!\") cannot test for errors",
        "excel_formula": "=AVERAGEIF(M15:M200,\"<>#VALUE!\")",
        "expected": "Either a plain AVERAGE, or AGGREGATE(1,6,...) to skip errors.",
        "evidence": "A criterion string is compared against the cell *values*, not their "
                    "error state, so the criterion never filters anything out; the formula "
                    "behaves exactly like AVERAGE.",
        "business_impact": "None while the columns contain no errors; would silently include "
                           "errors otherwise.",
        "resolution": "Implement as a plain mean and record the deviation in the audit report.",
    },
    {
        "id": "B8",
        "severity": "medium",
        "sheet": "Analysis",
        "cells": "A15:L200 (data block ordering)",
        "title": "The Analysis formulas assume newest-session-first, the sample data is oldest-first",
        "excel_formula": "I2 '=G15' (CMP) with Q15 using OFFSET(...,-25+AA,1)",
        "expected": "Row 15 should hold the most recent session; subsequent rows step backwards in time.",
        "evidence": "D1 'CMP' is defined as G15, and Q's OFFSET window is anchored on the current row "
                    "while AA decreases down the sheet — both only make sense if the next row is one "
                    "session OLDER. The supplied block, however, runs 2025-01-01 (row 15) to "
                    "2025-09-30 (row 200), i.e. ascending.",
        "business_impact": "The sample's CMP/1D..10D summary describes the OLDEST sessions instead of "
                           "the latest, so the summary and the volume MA disagree about time direction.",
        "resolution": "The application always stores history ascending and evaluates lookbacks over "
                      "the most recent N sessions, which is what the workbook intends; the layout "
                      "mismatch is recorded and the block direction is documented in the UI.",
    },
    {
        "id": "B9",
        "severity": "low",
        "sheet": "Analysis",
        "cells": "S15:S200 (V1)",
        "title": "The V1 fallback branch is unreachable dead code",
        "excel_formula": "=IFERROR(<nested IF chain>, IF(AND(L>$L$13,P<$P$13),\"--\","
                         "IF(AND(L<$L$13,P>$P$13),\"^\",\"\")))",
        "expected": "Either drop the fallback, or make the first argument able to fail.",
        "evidence": "The nested IF returns \"\" (not an error) when no grade matches, so IFERROR "
                    "never evaluates its second argument. Across all 186 rows the column only "
                    "ever holds Spl / Trpl / Dbl / J / blank — '--' and '^' never appear, "
                    "although the legend at Y7/Y8 documents them.",
        "business_impact": "The documented '--' and '^' V1 signals are silently never produced.",
        "resolution": "Reproduce the reachable outputs in 'original' mode and expose the "
                      "documented '--'/'^' signals in 'corrected' mode.",
    },
    {
        "id": "B10",
        "severity": "medium",
        "sheet": "Analysis",
        "cells": "S200:W200",
        "title": "The last table row's formulas are destroyed (#REF!)",
        "excel_formula": '=IFERROR(IF(AND(#REF!>2*$L$13,P200>2*$P$13), ...)',
        "expected": "The formulas should reference L200 / K200 / I200 like every other row.",
        "evidence": "Row 200 (2025-09-30) has #REF! wherever a structured/absolute reference to "
                    "the current row should be; cached results are #REF! for S200 and W200 and "
                    "blank for T200:V200. Every other row in the block is intact.",
        "business_impact": "The most recent session of the block gets no V1 grade, no volume "
                           "badge and no trade indicator.",
        "resolution": "The application computes that row normally; the parity suite excludes "
                      "cells whose formula contains #REF! because the workbook has no usable "
                      "oracle there.",
    },
]

AUDIT_FINDINGS_BY_ID = {f["id"]: f for f in AUDIT_FINDINGS}


# --------------------------------------------------------------------------
# Rule catalogue
# --------------------------------------------------------------------------


def _rule(
    rule_id: str,
    group: str,
    sheet: str,
    cells: str,
    name: str,
    description: str,
    excel_formula: str,
    inputs: List[str],
    python_equivalent: str,
    output_type: str,
    validation: str,
    original: str = "",
    corrected: str = "",
    findings: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "id": rule_id,
        "group": group,
        "sheet": sheet,
        "cells": cells,
        "name": name,
        "description": description,
        "excel_formula": excel_formula,
        "inputs": inputs,
        "python_equivalent": python_equivalent,
        "output_type": output_type,
        "validation": validation,
        "original": original or description,
        "corrected": corrected,
        "findings": findings or [],
    }


FORMULA_RULES: List[Dict[str, Any]] = [
    # ---------------- data transformations ----------------
    _rule(
        "T.deliverable_qty", "data_transform", "Analysis", "M15:M200", "Deliverable Quantity",
        "Shares actually delivered = delivery percentage applied to traded quantity.",
        "=data[[#This Row],[Delivery %]]*data[[#This Row],[TOTTRDQTY]]/100",
        ["Delivery %", "TOTTRDQTY"],
        "delivery_pct * tottrdqty / 100",
        "float", "non-negative; blank source -> blank result",
    ),
    _rule(
        "T.avg_price", "data_transform", "Analysis", "N15:N200", "Average Price",
        "Unweighted mean of the day's OPEN, HIGH, LOW and CLOSE.",
        "=AVERAGE(OPEN+HIGH+LOW+CLOSE)/4",
        ["OPEN", "HIGH", "LOW", "CLOSE"],
        "(open + high + low + close) / 4",
        "float", "all four OHLC values must be present",
    ),
    _rule(
        "T.pct_change", "data_transform", "Analysis", "O15:O200", "% Change",
        "Close-to-close change expressed as a RATIO (the workbook does not multiply by 100).",
        "=((LAST-PREVCLOSE)/PREVCLOSE)",
        ["LAST", "PREVCLOSE"],
        "(last - prevclose) / prevclose",
        "float", "PREVCLOSE = 0 -> blank (division undefined)",
    ),
    _rule(
        "T.action", "data_transform", "Analysis", "P15:P200", "Action (avg qty per trade)",
        "Traded quantity divided by the number of trades.",
        "=data[[#This Row],[TOTTRDQTY]]/data[[#This Row],[TOTALTRADES]]",
        ["TOTTRDQTY", "TOTALTRADES"],
        "tottrdqty / totaltrades",
        "float", "TOTALTRADES = 0 -> blank",
    ),
    _rule(
        "T.vol_ma", "rolling", "Analysis", "Q15:Q200", "Volume Moving Average",
        "Rolling mean of traded quantity over the 'Days' parameter (default 25).",
        "=AVERAGE(OFFSET(data[[#This Row],[TOTTRDQTY]],0,0,-$Q$13+AA15,1))",
        ["TOTTRDQTY", "AA counter", "Q13 Days"],
        "backward window of min(days, rows_available) sessions",
        "float", "window length >= 1",
        original="Window height = AA - 25 where AA counts 29,28,...,1 down the block: rows 15-18 "
                 "average FUTURE sessions and rows 19-200 return #VALUE! (finding B3).",
        corrected="Window length = min(25, number of sessions available up to and including "
                  "the current row), always looking backwards.",
        findings=["B3"],
    ),
    # ---------------- classifications ----------------
    _rule(
        "C.vol_level", "classification", "Analysis", "R15:R200", "Volume Level",
        "Compares today's quantity with the volume moving average.",
        '=IF(Q15>data[[#This Row],[TOTTRDQTY]],"Below","Above")',
        ["TOTTRDQTY", "Vol MA"],
        "'Above' if tottrdqty >= vol_ma else 'Below'",
        "str", "one of Above / Below",
    ),
    _rule(
        "C.v1_jackpot", "classification", "Analysis", "S15:S200", "V1 Jackpot Signal",
        "Delivery and Action both above the sheet baselines, graded by multiplier.",
        '=IFERROR(IF(AND(L>2*$L$13,P>2*$P$13),"Spl",IF(AND(L>1.6*$L$13,P>1.6*$P$13),'
        '"Trpl",IF(AND(L>1.3*$L$13,P>1.3*$P$13),"Dbl",IF(AND(L>$L$13,P>$P$13),"J","")))),'
        'IF(AND(L>$L$13,P<$P$13),"--",IF(AND(L<$L$13,P>$P$13),"^","")))',
        ["Delivery %", "Action", "avg Delivery %", "avg Action"],
        "graded compare against baseline with 2.0/1.6/1.3/1.0 multipliers",
        "str", "one of Spl / Trpl / Dbl / J / ''",
        original="Only Spl / Trpl / Dbl / J / '' are reachable: the IFERROR fallback that "
                 "would emit '--' or '^' can never fire (finding B9).",
        corrected="Also emit the documented '--' (delivery high, action low) and '^' "
                  "(delivery low, action high) signals.",
        findings=["B9"],
    ),
    _rule(
        "C.vol_badge", "classification", "Analysis", "T15:V200", "Volume Badge (BRONZE/SILVER/GOLD)",
        "Badges awarded when delivery exceeds 60% and volume is below a multiple of the MA.",
        'BRONZE: L>60 & I<Q ; SILVER: L>60 & I<0.75*Q ; GOLD: L>60 & I<0.5*Q',
        ["Delivery %", "TOTTRDQTY", "Vol MA"],
        "compare volume against vol_ma * factor",
        "str", "one of BRONZE / SILVER / GOLD / -- / ^ / ''",
        original="Analysis ladder uses factors 1.0 / 0.75 / 0.5 (decreasing).",
        corrected="Use the increasing 1.0 / 1.5 / 2.0 ladder that the workbook legends and "
                  "the Main sheet describe.",
        findings=["B4"],
    ),
    _rule(
        "C.trade_indicator", "classification", "Analysis", "W15:W200", "Trade-count Indicator",
        "Grades the number of trades against the sheet average number of trades.",
        '=IF(K=0,"",IF(K<$K$13,"Low",IF(K>4*$K$13,"V.High",IF(K>2*$K$13,"High","Above"))))',
        ["TOTALTRADES", "avg Trades"],
        "'Low' if < avg, 'V.High' > 4x, 'High' > 2x, else 'Above'",
        "str", "one of Low / Above / High / V.High / ''",
    ),
    _rule(
        "C.d_ind", "classification", "Main", "H18:H842", "D. Ind (trade-count grade, screener)",
        "Screener-side trade-count grading against the symbol's all-history average.",
        '=IF(F>4*G,"V.High",IF(F>2*G,"High",IF(F>G,"Above","Low")))',
        ["Today Trades", "Avg Trades"],
        "'V.High' >4x, 'High' >2x, 'Above' > 1x, else 'Low'",
        "str", "one of Low / Above / High / V.High",
    ),
    _rule(
        "C.dv1", "classification", "Main", "I18:I842", "DV1 (BRONZE/SILVER/GOLD, screener)",
        "Screener-side volume badge using today's volume against the all-history average.",
        '=IF(AND(B>60,D<E),"BRONZE",IF(AND(B>60,D<1.5*E),"SILVER",IF(AND(B>60,D{r+38}<2*E),"GOLD","")))',
        ["Today Delivery", "Today Volume", "Avg Volume"],
        "compare today_volume against avg_volume * factor",
        "str", "one of BRONZE / SILVER / GOLD / ''",
        original="The GOLD test reads D{r+38} — a row 38 rows below the current one — for every "
                 "row from 18 to 842.",
        corrected="The GOLD test reads the current row's today-volume (D{r}).",
        findings=["B5"],
    ),
    _rule(
        "C.dv2", "classification", "Main", "J18:J842", "DV2 (delivery threshold labels)",
        "Buckets today's delivery percentage into labelled bands.",
        '=IF(B<40,"Low Del",IF(B>80,"80% Plus",IF(B>60,"60% Plus",IF(B>50,"50% Plus",'
        'IF(B>40,"40% Plus","")))))',
        ["Today Delivery"],
        "ordered threshold chain",
        "str", "Low Del / 40% Plus / 50% Plus / 60% Plus / 80% Plus / ''",
        original="Strict '>' comparisons: exactly 60 becomes '50% Plus'; exactly 40 or 80 can "
                 "produce an empty string.",
        corrected="Closed buckets: <40 Low Del, 40-50, 50-60, 60-80, >80 '80% Plus'.",
        findings=["B6"],
    ),
    # ---------------- rolling lookback summary ----------------
    _rule(
        "R.cmp_nd", "rolling", "Analysis", "I2:I6", "CMP over 1/3/5/7/10 sessions",
        "Mean of the LAST price over the requested lookback window.",
        "1D '=G15' ; 3D '=AVERAGE($G$15:G17)' ; 5D/7D/10D '=AVERAGE($G$15:G18)'",
        ["LAST"],
        "mean(last[-(n-1):]) over the window",
        "float", "window length n",
        original="Windows 5, 7 and 10 all evaluate to the same 4-row range G15:G18.",
        corrected="Windows use exactly n rows (5, 7 and 10 respectively).",
        findings=["B1"],
    ),
    _rule(
        "R.avg_price_nd", "rolling", "Analysis", "J2:J6", "Avg Price over 1/3/5/7/10 sessions",
        "Mean of the per-day average price over the lookback window.",
        "1D '=N15' ; 3D '=AVERAGE($N$15:N17)' ; 5D '=AVERAGE($N$15:N19)' etc.",
        ["Avg Price"],
        "mean(avg_price[-(n-1):])",
        "float", "window length n",
    ),
    _rule(
        "R.pct_change_nd", "rolling", "Analysis", "K2:K6", "% Change over 1/3/5/7/10 sessions",
        "Mean of the daily change ratio over the lookback window.",
        "1D '=O15' ; 3D '=AVERAGE($O$15:O17)' ; 5D '=AVERAGE($O$15:O19)' etc.",
        ["% Change"],
        "mean(pct_change[-(n-1):])",
        "float", "window length n",
    ),
    _rule(
        "R.delivery_nd", "rolling", "Analysis", "L2:L6", "Delivery % over 1/3/5/7/10 sessions",
        "Mean delivery percentage over the lookback window.",
        "1D '=L15' ; 3D '=AVERAGE($L$15:L17)' ; 5D/7D/10D '=AVERAGE($L$15:L18)'",
        ["Delivery %"],
        "mean(delivery_pct[-(n-1):])",
        "float", "window length n",
        original="Windows 5, 7 and 10 all evaluate to the same 4-row range L15:L18.",
        corrected="Windows use exactly n rows.",
        findings=["B2"],
    ),
    _rule(
        "R.action_nd", "rolling", "Analysis", "M2:M6", "Action over 1/3/5/7/10 sessions",
        "Mean of the Action metric (qty per trade) over the lookback window.",
        "1D '=P15' ; 3D '=AVERAGE($P$15:P17)' ; 5D '=AVERAGE($P$15:P19)' etc.",
        ["Action"],
        "mean(action[-(n-1):])",
        "float", "window length n",
    ),
    _rule(
        "R.cmp_vs_avg", "classification", "Analysis", "N2:N6", "CMP vs Average Price",
        "Verdict comparing the windowed CMP with the windowed average price.",
        '=IF(I2>J2,"Above",IF(I2<J2,"Below",IF(I2=J2,"")))',
        ["CMP", "Avg Price"],
        "'Above' / 'Below' / '' on equality",
        "str", "one of Above / Below / ''",
    ),
    # ---------------- baselines ----------------
    _rule(
        "B.avg_delivery", "baseline", "Analysis", "L13", "Baseline average Delivery %",
        "Mean delivery percentage across the populated 15:200 block.",
        "=AVERAGE(L15:L200)", ["Delivery %"], "mean(delivery_pct)", "float",
        "empty range -> blank",
    ),
    _rule(
        "B.avg_action", "baseline", "Analysis", "P13", "Baseline average Action",
        "Mean Action across the populated block. The raw formula uses "
        'AVERAGEIF(P15:P200,"<>#VALUE!") which behaves as a plain average.',
        '=AVERAGEIF(P15:P200,"<>#VALUE!")', ["Action"], "mean(action)", "float",
        "empty range -> blank", findings=["B7"],
    ),
    _rule(
        "B.avg_trades", "baseline", "Analysis", "K13", "Baseline average Trades",
        "Mean number of trades across the populated block.",
        "=AVERAGE(K15:K200)", ["TOTALTRADES"], "mean(totaltrades)", "float",
        "empty range -> blank",
    ),
    # ---------------- screener aggregates ----------------
    _rule(
        "S.today_delivery", "aggregate", "Main", "B18:B842", "Today Delivery",
        "Sum of today's delivery percentage for the selected symbol and date.",
        "=SUMIFS(Data!$L:$L,Data!$A:$A,$A18,Data!$B:$B,$B$1)",
        ["Delivery %", "Symbol", "Date"], "sum(delivery_pct) per symbol+date", "float",
        "unknown symbol/date -> blank",
    ),
    _rule(
        "S.avg_delivery", "aggregate", "Main", "C18:C842", "Average Delivery",
        "All-history mean delivery percentage for the symbol.",
        "=AVERAGEIF(Data!$A:$A,$A18,Data!$L:$L)",
        ["Delivery %", "Symbol"], "mean(delivery_pct) per symbol", "float",
        "no history -> blank",
    ),
    _rule(
        "S.today_volume", "aggregate", "Main", "D18:D842", "Today Volume",
        "Traded quantity for the selected symbol and date.",
        "=SUMIFS(Data!$I:$I,Data!$A:$A,$A18,Data!$B:$B,$B$1)",
        ["TOTTRDQTY", "Symbol", "Date"], "sum(tottrdqty) per symbol+date", "float", "",
    ),
    _rule(
        "S.avg_volume", "aggregate", "Main", "E18:E842", "Average Volume",
        "All-history mean traded quantity for the symbol.",
        "=AVERAGEIF(Data!$A:$A,$A18,Data!$I:$I)",
        ["TOTTRDQTY", "Symbol"], "mean(tottrdqty) per symbol", "float", "",
    ),
    _rule(
        "S.today_trades", "aggregate", "Main", "F18:F842", "Today Trades",
        "Number of trades for the selected symbol and date.",
        "=SUMIFS(Data!$K:$K,Data!$A:$A,$A18,Data!$B:$B,$B$1)",
        ["TOTALTRADES", "Symbol", "Date"], "sum(totaltrades) per symbol+date", "float", "",
    ),
    _rule(
        "S.avg_trades", "aggregate", "Main", "G18:G842", "Average Trades",
        "All-history mean number of trades for the symbol.",
        "=AVERAGEIF(Data!$A:$A,$A18,Data!$K:$K)",
        ["TOTALTRADES", "Symbol"], "mean(totaltrades) per symbol", "float", "",
    ),
]

RULES_BY_ID = {r["id"]: r for r in FORMULA_RULES}

FORMULA_GROUPS = (
    "data_transform",
    "rolling",
    "classification",
    "baseline",
    "aggregate",
)


def rules_in_group(group: str) -> List[Dict[str, Any]]:
    return [r for r in FORMULA_RULES if r["group"] == group]


__all__ = [
    "AUDIT_FINDINGS",
    "AUDIT_FINDINGS_BY_ID",
    "FORMULA_RULES",
    "RULES_BY_ID",
    "FORMULA_GROUPS",
    "MOVING_AVERAGE_DAYS",
    "DELIVERY_BASELINE_WINDOW",
    "LOOKBACK_WINDOWS",
    "V1_MULTIPLIERS",
    "rules_in_group",
]
