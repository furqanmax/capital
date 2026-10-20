# Formula Audit Report

_Generated 2026-10-18 21:15:27_

## How to read this report

Every rule below is implemented twice:

| Mode | Meaning |
|---|---|
| `original` | Replays the supplied workbook **exactly**, including the defects. This is the default, and it is the mode the parity suite verifies against the workbook's cached values. |
| `corrected` | Applies the documented intent of the rule. Divergences are logged to the Formula Audit Log with the finding reference. |

No rule was silently changed. Suspect formulas were reproduced first, verified against the workbook's own cached results, and only then given a corrected variant.

**Inventory:** 9,537 formula cells across 4 worksheets, of which **4,116 are cached errors**.

## Confirmed defects (preserved by design)

| ID | Severity | Sheet | Cells | Finding |
|---|---|---|---|---|
| B1 | high | Analysis | `I4:I6` | 5D/7D/10D CMP windows are 4 rows instead of 5/7/10 |
| B2 | high | Analysis | `L4:L6` | 5D/7D/10D Delivery-% windows are 4 rows instead of 5/7/10 |
| B3 | critical | Analysis | `Q15:Q200 (helper counter AA15:AA43)` | The volume moving average is #VALUE! for every row except the first four, and those four average FUTURE sessions |
| B4 | high | Analysis vs Main | `Analysis!T15:V200 vs Main!I18:I842` | SILVER/GOLD volume thresholds contradict between Analysis and Main |
| B5 | critical | Main | `I18:I842` | DV1 reads the volume of the wrong row (D{r+38}) |
| B6 | medium | Main | `J18:J842` | DV2 mis-buckets exact boundary values |
| B7 | low | Analysis | `M13, P13` | AVERAGEIF("<>#VALUE!") cannot test for errors |
| B8 | medium | Analysis | `A15:L200 (data block ordering)` | The Analysis formulas assume newest-session-first, the sample data is oldest-first |
| B9 | low | Analysis | `S15:S200 (V1)` | The V1 fallback branch is unreachable dead code |
| B10 | medium | Analysis | `S200:W200` | The last table row's formulas are destroyed (#REF!) |

### B1 — 5D/7D/10D CMP windows are 4 rows instead of 5/7/10

* **Sheet / cells:** `Analysis` `I4:I6`
* **Workbook formula:** `='AVERAGE($G$15:G18)'  (identical in I4, I5 and I6)`
* **What it should be:** I4=AVERAGE($G$15:G19), I5=AVERAGE($G$15:G21), I6=AVERAGE($G$15:G24)
* **Evidence:** Cached values for I4, I5 and I6 are all 460.25, i.e. the 4-row average of G15:G18. A correct 10-day average is 464.51.
* **Business impact:** The 'CMP' summary for 5/7/10 sessions understates the price window and therefore the CMP-vs-AvgPrice verdict.
* **Resolution:** Replay as-is in 'original' mode; use the 5/7/10-row windows in 'corrected' mode. Requires user sign-off to change the default.

### B2 — 5D/7D/10D Delivery-% windows are 4 rows instead of 5/7/10

* **Sheet / cells:** `Analysis` `L4:L6`
* **Workbook formula:** `='AVERAGE($L$15:L18)'  (identical in L4, L5 and L6)`
* **What it should be:** L4=AVERAGE($L$15:L19), L5=AVERAGE($L$15:L21), L6=AVERAGE($L$15:L24)
* **Evidence:** Cached L4 = L5 = L6 = 27.2125 (the 4-row mean 108.85/4).
* **Business impact:** Delivery-percentage summaries for longer windows are wrong, which feeds the delivery classifications.
* **Resolution:** Same dual-mode policy as B1.

### B3 — The volume moving average is #VALUE! for every row except the first four, and those four average FUTURE sessions

* **Sheet / cells:** `Analysis` `Q15:Q200 (helper counter AA15:AA43)`
* **Workbook formula:** `='AVERAGE(OFFSET(data[[#This Row],[TOTTRDQTY]],0,0,-$Q$13+AA15,1))'`
* **What it should be:** A backward-only window of min(25, sessions available) per row.
* **Evidence:** AA is a countdown helper: AA43 = 1, AA{r} = AA{r+1}+1, so AA{r} = 44-r (AA15 = 29) and it is blank from row 44. The OFFSET height is therefore -25+AA = 4-i for data index i = r-15. Row 15 (i=0) averages rows 15..18 - four FUTURE sessions; row 19 (i=4) has height 0 and rows 20+ have a negative height that reaches above the table's data body, which Excel rejects on a structured reference. Cached values confirm it: Q15:Q18 are numbers (6286342, 7912200.33, 7463366, 2853849) and Q19:Q200 are all #VALUE!. The Mov Avg column, the Vol Level column (R) and the BRONZE/SILVER/GOLD columns (T/U/V) are consequently dead for 182 of the 186 sessions.
* **Business impact:** Every volume-based signal in the workbook is unusable: the volume MA, the Above/Below volume level and all three volume badges only ever evaluate on the first four rows of the block, and there they are computed from sessions that had not happened yet.
* **Resolution:** Replay the four forward-looking values and the #VALUE! rows exactly in 'original' mode (the parity suite matches all of them); use the documented backward, capped window in 'corrected' mode.

### B4 — SILVER/GOLD volume thresholds contradict between Analysis and Main

* **Sheet / cells:** `Analysis vs Main` `Analysis!T15:V200 vs Main!I18:I842`
* **Workbook formula:** `Analysis: I<Q -> BRONZE, I<0.75*Q -> SILVER, I<0.5*Q -> GOLD`
* **What it should be:** Main + legend: Vol < 1.0*Avg -> BRONZE, < 1.5*Avg -> SILVER, < 2.0*Avg -> GOLD
* **Evidence:** The Analysis legend (S7/S8) and the Main legend (N22:N25) both describe the *increasing* 1.0/1.5/2.0 ladder used by Main, while the Analysis column formulas use a *decreasing* 1.0/0.75/0.5 ladder.
* **Business impact:** The same badge means different things on the two sheets; a stock can be GOLD in Analysis and blank in Main for the same day.
* **Resolution:** Both ladders are exposed as named rule variants; the audit log records which ladder produced each badge.

### B5 — DV1 reads the volume of the wrong row (D{r+38})

* **Sheet / cells:** `Main` `I18:I842`
* **Workbook formula:** `IF(AND(B18>60, D18<E18), "BRONZE", IF(AND(B18>60, D18<1.5*E18), "SILVER", IF(AND(B18>60, D56<2*E18), "GOLD", "")))`
* **What it should be:** The third comparison must use D18 (the current row), not D56.
* **Evidence:** Row 18 -> D56, row 19 -> D57 ... row 842 -> D880: a constant +38 row offset applied to all 825 rows. D880 is outside the populated range.
* **Business impact:** GOLD can never be awarded correctly: the volume test is taken from an unrelated stock 38 rows further down.
* **Resolution:** Default 'original' mode reproduces the offset exactly (so parity checks pass); 'corrected' mode uses the current row and flags the difference in the Formula Audit Log.

### B6 — DV2 mis-buckets exact boundary values

* **Sheet / cells:** `Main` `J18:J842`
* **Workbook formula:** `IF(B<40,"Low Del",IF(B>80,"80% Plus",IF(B>60,"60% Plus",IF(B>50,"50% Plus",IF(B>40,"40% Plus","")))))`
* **What it should be:** Boundaries 40/50/60/80 should land in a defined bucket.
* **Evidence:** Delivery exactly 60 evaluates to '50% Plus' (the B>60 test fails, B>50 succeeds); exactly 40/80 can fall through to an empty string.
* **Business impact:** Boundary rows are labelled one bucket too low or left blank.
* **Resolution:** Replay the inclusive/exclusive chain in 'original' mode; use documented boundary-closed buckets in 'corrected' mode.

### B7 — AVERAGEIF("<>#VALUE!") cannot test for errors

* **Sheet / cells:** `Analysis` `M13, P13`
* **Workbook formula:** `=AVERAGEIF(M15:M200,"<>#VALUE!")`
* **What it should be:** Either a plain AVERAGE, or AGGREGATE(1,6,...) to skip errors.
* **Evidence:** A criterion string is compared against the cell *values*, not their error state, so the criterion never filters anything out; the formula behaves exactly like AVERAGE.
* **Business impact:** None while the columns contain no errors; would silently include errors otherwise.
* **Resolution:** Implement as a plain mean and record the deviation in the audit report.

### B8 — The Analysis formulas assume newest-session-first, the sample data is oldest-first

* **Sheet / cells:** `Analysis` `A15:L200 (data block ordering)`
* **Workbook formula:** `I2 '=G15' (CMP) with Q15 using OFFSET(...,-25+AA,1)`
* **What it should be:** Row 15 should hold the most recent session; subsequent rows step backwards in time.
* **Evidence:** D1 'CMP' is defined as G15, and Q's OFFSET window is anchored on the current row while AA decreases down the sheet — both only make sense if the next row is one session OLDER. The supplied block, however, runs 2025-01-01 (row 15) to 2025-09-30 (row 200), i.e. ascending.
* **Business impact:** The sample's CMP/1D..10D summary describes the OLDEST sessions instead of the latest, so the summary and the volume MA disagree about time direction.
* **Resolution:** The application always stores history ascending and evaluates lookbacks over the most recent N sessions, which is what the workbook intends; the layout mismatch is recorded and the block direction is documented in the UI.

### B9 — The V1 fallback branch is unreachable dead code

* **Sheet / cells:** `Analysis` `S15:S200 (V1)`
* **Workbook formula:** `=IFERROR(<nested IF chain>, IF(AND(L>$L$13,P<$P$13),"--",IF(AND(L<$L$13,P>$P$13),"^","")))`
* **What it should be:** Either drop the fallback, or make the first argument able to fail.
* **Evidence:** The nested IF returns "" (not an error) when no grade matches, so IFERROR never evaluates its second argument. Across all 186 rows the column only ever holds Spl / Trpl / Dbl / J / blank — '--' and '^' never appear, although the legend at Y7/Y8 documents them.
* **Business impact:** The documented '--' and '^' V1 signals are silently never produced.
* **Resolution:** Reproduce the reachable outputs in 'original' mode and expose the documented '--'/'^' signals in 'corrected' mode.

### B10 — The last table row's formulas are destroyed (#REF!)

* **Sheet / cells:** `Analysis` `S200:W200`
* **Workbook formula:** `=IFERROR(IF(AND(#REF!>2*$L$13,P200>2*$P$13), ...)`
* **What it should be:** The formulas should reference L200 / K200 / I200 like every other row.
* **Evidence:** Row 200 (2025-09-30) has #REF! wherever a structured/absolute reference to the current row should be; cached results are #REF! for S200 and W200 and blank for T200:V200. Every other row in the block is intact.
* **Business impact:** The most recent session of the block gets no V1 grade, no volume badge and no trade indicator.
* **Resolution:** The application computes that row normally; the parity suite excludes cells whose formula contains #REF! because the workbook has no usable oracle there.

## Rule catalogue

### Group: `data_transform`

#### T.deliverable_qty — Deliverable Quantity

* **Sheet / cells:** `Analysis` `M15:M200`
* **What it does:** Shares actually delivered = delivery percentage applied to traded quantity.
* **Workbook formula:** `=data[[#This Row],[Delivery %]]*data[[#This Row],[TOTTRDQTY]]/100`
* **Inputs:** Delivery %, TOTTRDQTY
* **Output:** float — non-negative; blank source -> blank result
* **Original mode:** Shares actually delivered = delivery percentage applied to traded quantity.

#### T.avg_price — Average Price

* **Sheet / cells:** `Analysis` `N15:N200`
* **What it does:** Unweighted mean of the day's OPEN, HIGH, LOW and CLOSE.
* **Workbook formula:** `=AVERAGE(OPEN+HIGH+LOW+CLOSE)/4`
* **Inputs:** OPEN, HIGH, LOW, CLOSE
* **Output:** float — all four OHLC values must be present
* **Original mode:** Unweighted mean of the day's OPEN, HIGH, LOW and CLOSE.

#### T.pct_change — % Change

* **Sheet / cells:** `Analysis` `O15:O200`
* **What it does:** Close-to-close change expressed as a RATIO (the workbook does not multiply by 100).
* **Workbook formula:** `=((LAST-PREVCLOSE)/PREVCLOSE)`
* **Inputs:** LAST, PREVCLOSE
* **Output:** float — PREVCLOSE = 0 -> blank (division undefined)
* **Original mode:** Close-to-close change expressed as a RATIO (the workbook does not multiply by 100).

#### T.action — Action (avg qty per trade)

* **Sheet / cells:** `Analysis` `P15:P200`
* **What it does:** Traded quantity divided by the number of trades.
* **Workbook formula:** `=data[[#This Row],[TOTTRDQTY]]/data[[#This Row],[TOTALTRADES]]`
* **Inputs:** TOTTRDQTY, TOTALTRADES
* **Output:** float — TOTALTRADES = 0 -> blank
* **Original mode:** Traded quantity divided by the number of trades.

### Group: `rolling`

#### T.vol_ma — Volume Moving Average

* **Sheet / cells:** `Analysis` `Q15:Q200`
* **What it does:** Rolling mean of traded quantity over the 'Days' parameter (default 25).
* **Workbook formula:** `=AVERAGE(OFFSET(data[[#This Row],[TOTTRDQTY]],0,0,-$Q$13+AA15,1))`
* **Inputs:** TOTTRDQTY, AA counter, Q13 Days
* **Output:** float — window length >= 1
* **Original mode:** Window height = AA - 25 where AA counts 29,28,...,1 down the block: rows 15-18 average FUTURE sessions and rows 19-200 return #VALUE! (finding B3).
* **Corrected mode:** Window length = min(25, number of sessions available up to and including the current row), always looking backwards.
* **Related findings:** B3

#### R.cmp_nd — CMP over 1/3/5/7/10 sessions

* **Sheet / cells:** `Analysis` `I2:I6`
* **What it does:** Mean of the LAST price over the requested lookback window.
* **Workbook formula:** `1D '=G15' ; 3D '=AVERAGE($G$15:G17)' ; 5D/7D/10D '=AVERAGE($G$15:G18)'`
* **Inputs:** LAST
* **Output:** float — window length n
* **Original mode:** Windows 5, 7 and 10 all evaluate to the same 4-row range G15:G18.
* **Corrected mode:** Windows use exactly n rows (5, 7 and 10 respectively).
* **Related findings:** B1

#### R.avg_price_nd — Avg Price over 1/3/5/7/10 sessions

* **Sheet / cells:** `Analysis` `J2:J6`
* **What it does:** Mean of the per-day average price over the lookback window.
* **Workbook formula:** `1D '=N15' ; 3D '=AVERAGE($N$15:N17)' ; 5D '=AVERAGE($N$15:N19)' etc.`
* **Inputs:** Avg Price
* **Output:** float — window length n
* **Original mode:** Mean of the per-day average price over the lookback window.

#### R.pct_change_nd — % Change over 1/3/5/7/10 sessions

* **Sheet / cells:** `Analysis` `K2:K6`
* **What it does:** Mean of the daily change ratio over the lookback window.
* **Workbook formula:** `1D '=O15' ; 3D '=AVERAGE($O$15:O17)' ; 5D '=AVERAGE($O$15:O19)' etc.`
* **Inputs:** % Change
* **Output:** float — window length n
* **Original mode:** Mean of the daily change ratio over the lookback window.

#### R.delivery_nd — Delivery % over 1/3/5/7/10 sessions

* **Sheet / cells:** `Analysis` `L2:L6`
* **What it does:** Mean delivery percentage over the lookback window.
* **Workbook formula:** `1D '=L15' ; 3D '=AVERAGE($L$15:L17)' ; 5D/7D/10D '=AVERAGE($L$15:L18)'`
* **Inputs:** Delivery %
* **Output:** float — window length n
* **Original mode:** Windows 5, 7 and 10 all evaluate to the same 4-row range L15:L18.
* **Corrected mode:** Windows use exactly n rows.
* **Related findings:** B2

#### R.action_nd — Action over 1/3/5/7/10 sessions

* **Sheet / cells:** `Analysis` `M2:M6`
* **What it does:** Mean of the Action metric (qty per trade) over the lookback window.
* **Workbook formula:** `1D '=P15' ; 3D '=AVERAGE($P$15:P17)' ; 5D '=AVERAGE($P$15:P19)' etc.`
* **Inputs:** Action
* **Output:** float — window length n
* **Original mode:** Mean of the Action metric (qty per trade) over the lookback window.

### Group: `classification`

#### C.vol_level — Volume Level

* **Sheet / cells:** `Analysis` `R15:R200`
* **What it does:** Compares today's quantity with the volume moving average.
* **Workbook formula:** `=IF(Q15>data[[#This Row],[TOTTRDQTY]],"Below","Above")`
* **Inputs:** TOTTRDQTY, Vol MA
* **Output:** str — one of Above / Below
* **Original mode:** Compares today's quantity with the volume moving average.

#### C.v1_jackpot — V1 Jackpot Signal

* **Sheet / cells:** `Analysis` `S15:S200`
* **What it does:** Delivery and Action both above the sheet baselines, graded by multiplier.
* **Workbook formula:** `=IFERROR(IF(AND(L>2*$L$13,P>2*$P$13),"Spl",IF(AND(L>1.6*$L$13,P>1.6*$P$13),"Trpl",IF(AND(L>1.3*$L$13,P>1.3*$P$13),"Dbl",IF(AND(L>$L$13,P>$P$13),"J","")))),IF(AND(L>$L$13,P<$P$13),"--",IF(AND(L<$L$13,P>$P$13),"^","")))`
* **Inputs:** Delivery %, Action, avg Delivery %, avg Action
* **Output:** str — one of Spl / Trpl / Dbl / J / ''
* **Original mode:** Only Spl / Trpl / Dbl / J / '' are reachable: the IFERROR fallback that would emit '--' or '^' can never fire (finding B9).
* **Corrected mode:** Also emit the documented '--' (delivery high, action low) and '^' (delivery low, action high) signals.
* **Related findings:** B9

#### C.vol_badge — Volume Badge (BRONZE/SILVER/GOLD)

* **Sheet / cells:** `Analysis` `T15:V200`
* **What it does:** Badges awarded when delivery exceeds 60% and volume is below a multiple of the MA.
* **Workbook formula:** `BRONZE: L>60 & I<Q ; SILVER: L>60 & I<0.75*Q ; GOLD: L>60 & I<0.5*Q`
* **Inputs:** Delivery %, TOTTRDQTY, Vol MA
* **Output:** str — one of BRONZE / SILVER / GOLD / -- / ^ / ''
* **Original mode:** Analysis ladder uses factors 1.0 / 0.75 / 0.5 (decreasing).
* **Corrected mode:** Use the increasing 1.0 / 1.5 / 2.0 ladder that the workbook legends and the Main sheet describe.
* **Related findings:** B4

#### C.trade_indicator — Trade-count Indicator

* **Sheet / cells:** `Analysis` `W15:W200`
* **What it does:** Grades the number of trades against the sheet average number of trades.
* **Workbook formula:** `=IF(K=0,"",IF(K<$K$13,"Low",IF(K>4*$K$13,"V.High",IF(K>2*$K$13,"High","Above"))))`
* **Inputs:** TOTALTRADES, avg Trades
* **Output:** str — one of Low / Above / High / V.High / ''
* **Original mode:** Grades the number of trades against the sheet average number of trades.

#### C.d_ind — D. Ind (trade-count grade, screener)

* **Sheet / cells:** `Main` `H18:H842`
* **What it does:** Screener-side trade-count grading against the symbol's all-history average.
* **Workbook formula:** `=IF(F>4*G,"V.High",IF(F>2*G,"High",IF(F>G,"Above","Low")))`
* **Inputs:** Today Trades, Avg Trades
* **Output:** str — one of Low / Above / High / V.High
* **Original mode:** Screener-side trade-count grading against the symbol's all-history average.

#### C.dv1 — DV1 (BRONZE/SILVER/GOLD, screener)

* **Sheet / cells:** `Main` `I18:I842`
* **What it does:** Screener-side volume badge using today's volume against the all-history average.
* **Workbook formula:** `=IF(AND(B>60,D<E),"BRONZE",IF(AND(B>60,D<1.5*E),"SILVER",IF(AND(B>60,D{r+38}<2*E),"GOLD","")))`
* **Inputs:** Today Delivery, Today Volume, Avg Volume
* **Output:** str — one of BRONZE / SILVER / GOLD / ''
* **Original mode:** The GOLD test reads D{r+38} — a row 38 rows below the current one — for every row from 18 to 842.
* **Corrected mode:** The GOLD test reads the current row's today-volume (D{r}).
* **Related findings:** B5

#### C.dv2 — DV2 (delivery threshold labels)

* **Sheet / cells:** `Main` `J18:J842`
* **What it does:** Buckets today's delivery percentage into labelled bands.
* **Workbook formula:** `=IF(B<40,"Low Del",IF(B>80,"80% Plus",IF(B>60,"60% Plus",IF(B>50,"50% Plus",IF(B>40,"40% Plus","")))))`
* **Inputs:** Today Delivery
* **Output:** str — Low Del / 40% Plus / 50% Plus / 60% Plus / 80% Plus / ''
* **Original mode:** Strict '>' comparisons: exactly 60 becomes '50% Plus'; exactly 40 or 80 can produce an empty string.
* **Corrected mode:** Closed buckets: <40 Low Del, 40-50, 50-60, 60-80, >80 '80% Plus'.
* **Related findings:** B6

#### R.cmp_vs_avg — CMP vs Average Price

* **Sheet / cells:** `Analysis` `N2:N6`
* **What it does:** Verdict comparing the windowed CMP with the windowed average price.
* **Workbook formula:** `=IF(I2>J2,"Above",IF(I2<J2,"Below",IF(I2=J2,"")))`
* **Inputs:** CMP, Avg Price
* **Output:** str — one of Above / Below / ''
* **Original mode:** Verdict comparing the windowed CMP with the windowed average price.

### Group: `baseline`

#### B.avg_delivery — Baseline average Delivery %

* **Sheet / cells:** `Analysis` `L13`
* **What it does:** Mean delivery percentage across the populated 15:200 block.
* **Workbook formula:** `=AVERAGE(L15:L200)`
* **Inputs:** Delivery %
* **Output:** float — empty range -> blank
* **Original mode:** Mean delivery percentage across the populated 15:200 block.

#### B.avg_action — Baseline average Action

* **Sheet / cells:** `Analysis` `P13`
* **What it does:** Mean Action across the populated block. The raw formula uses AVERAGEIF(P15:P200,"<>#VALUE!") which behaves as a plain average.
* **Workbook formula:** `=AVERAGEIF(P15:P200,"<>#VALUE!")`
* **Inputs:** Action
* **Output:** float — empty range -> blank
* **Original mode:** Mean Action across the populated block. The raw formula uses AVERAGEIF(P15:P200,"<>#VALUE!") which behaves as a plain average.
* **Related findings:** B7

#### B.avg_trades — Baseline average Trades

* **Sheet / cells:** `Analysis` `K13`
* **What it does:** Mean number of trades across the populated block.
* **Workbook formula:** `=AVERAGE(K15:K200)`
* **Inputs:** TOTALTRADES
* **Output:** float — empty range -> blank
* **Original mode:** Mean number of trades across the populated block.

### Group: `aggregate`

#### S.today_delivery — Today Delivery

* **Sheet / cells:** `Main` `B18:B842`
* **What it does:** Sum of today's delivery percentage for the selected symbol and date.
* **Workbook formula:** `=SUMIFS(Data!$L:$L,Data!$A:$A,$A18,Data!$B:$B,$B$1)`
* **Inputs:** Delivery %, Symbol, Date
* **Output:** float — unknown symbol/date -> blank
* **Original mode:** Sum of today's delivery percentage for the selected symbol and date.

#### S.avg_delivery — Average Delivery

* **Sheet / cells:** `Main` `C18:C842`
* **What it does:** All-history mean delivery percentage for the symbol.
* **Workbook formula:** `=AVERAGEIF(Data!$A:$A,$A18,Data!$L:$L)`
* **Inputs:** Delivery %, Symbol
* **Output:** float — no history -> blank
* **Original mode:** All-history mean delivery percentage for the symbol.

#### S.today_volume — Today Volume

* **Sheet / cells:** `Main` `D18:D842`
* **What it does:** Traded quantity for the selected symbol and date.
* **Workbook formula:** `=SUMIFS(Data!$I:$I,Data!$A:$A,$A18,Data!$B:$B,$B$1)`
* **Inputs:** TOTTRDQTY, Symbol, Date
* **Output:** float — 
* **Original mode:** Traded quantity for the selected symbol and date.

#### S.avg_volume — Average Volume

* **Sheet / cells:** `Main` `E18:E842`
* **What it does:** All-history mean traded quantity for the symbol.
* **Workbook formula:** `=AVERAGEIF(Data!$A:$A,$A18,Data!$I:$I)`
* **Inputs:** TOTTRDQTY, Symbol
* **Output:** float — 
* **Original mode:** All-history mean traded quantity for the symbol.

#### S.today_trades — Today Trades

* **Sheet / cells:** `Main` `F18:F842`
* **What it does:** Number of trades for the selected symbol and date.
* **Workbook formula:** `=SUMIFS(Data!$K:$K,Data!$A:$A,$A18,Data!$B:$B,$B$1)`
* **Inputs:** TOTALTRADES, Symbol, Date
* **Output:** float — 
* **Original mode:** Number of trades for the selected symbol and date.

#### S.avg_trades — Average Trades

* **Sheet / cells:** `Main` `G18:G842`
* **What it does:** All-history mean number of trades for the symbol.
* **Workbook formula:** `=AVERAGEIF(Data!$A:$A,$A18,Data!$K:$K)`
* **Inputs:** TOTALTRADES, Symbol
* **Output:** float — 
* **Original mode:** All-history mean number of trades for the symbol.

