# Formula Mapping — Excel to Python

This document is the contract between the workbook and the application. `services/analytics.py` implements the *Original* column; the *Corrected* column is implemented behind `mode = "corrected"`.

## Data columns

| Workbook column | Header | Canonical database column | Notes |
|---|---|---|---|
| A | Symbol | `stocks.symbol` | upper-cased; indexed |
| B | Date | `prices.trade_date` | stored as a real DATE so sorting works |
| C | OPEN | `prices.open` | float, nullable |
| D | HIGH | `prices.high` | float, nullable |
| E | LOW | `prices.low` | float, nullable |
| F | CLOSE | `prices.close` | float, nullable |
| G | LAST | `prices.last` | float, nullable |
| H | PREVCLOSE | `prices.prevclose` | float, nullable |
| I | TOTTRDQTY | `prices.tottrdqty` | volume; non-negative check |
| J | TOTTRDVAL | `prices.tottrdval` | turnover |
| K | TOTALTRADES | `prices.totaltrades` | trade count |
| L | Delivery % | `prices.delivery_pct` | percentage 0-100 |

Any header that has no canonical match is preserved in `price_extras` together with its batch, so an import never discards a column.

## Rule-by-rule mapping

| Rule | Analysis/Main cells | Inputs | Original behaviour | Corrected behaviour |
|---|---|---|---|---|
| `T.deliverable_qty` | `Analysis!M15:M200` | Delivery %, TOTTRDQTY | Shares actually delivered = delivery percentage applied to traded quantity. | — |
| `T.avg_price` | `Analysis!N15:N200` | OPEN, HIGH, LOW, CLOSE | Unweighted mean of the day's OPEN, HIGH, LOW and CLOSE. | — |
| `T.pct_change` | `Analysis!O15:O200` | LAST, PREVCLOSE | Close-to-close change expressed as a RATIO (the workbook does not multiply by 100). | — |
| `T.action` | `Analysis!P15:P200` | TOTTRDQTY, TOTALTRADES | Traded quantity divided by the number of trades. | — |
| `T.vol_ma` | `Analysis!Q15:Q200` | TOTTRDQTY, AA counter, Q13 Days | Window height = AA - 25 where AA counts 29,28,...,1 down the block: rows 15-18 average FUTURE sessions and rows 19-200 return #VALUE! (finding B3). | Window length = min(25, number of sessions available up to and including the current row), always looking backwards. |
| `C.vol_level` | `Analysis!R15:R200` | TOTTRDQTY, Vol MA | Compares today's quantity with the volume moving average. | — |
| `C.v1_jackpot` | `Analysis!S15:S200` | Delivery %, Action, avg Delivery %, avg Action | Only Spl / Trpl / Dbl / J / '' are reachable: the IFERROR fallback that would emit '--' or '^' can never fire (finding B9). | Also emit the documented '--' (delivery high, action low) and '^' (delivery low, action high) signals. |
| `C.vol_badge` | `Analysis!T15:V200` | Delivery %, TOTTRDQTY, Vol MA | Analysis ladder uses factors 1.0 / 0.75 / 0.5 (decreasing). | Use the increasing 1.0 / 1.5 / 2.0 ladder that the workbook legends and the Main sheet describe. |
| `C.trade_indicator` | `Analysis!W15:W200` | TOTALTRADES, avg Trades | Grades the number of trades against the sheet average number of trades. | — |
| `C.d_ind` | `Main!H18:H842` | Today Trades, Avg Trades | Screener-side trade-count grading against the symbol's all-history average. | — |
| `C.dv1` | `Main!I18:I842` | Today Delivery, Today Volume, Avg Volume | The GOLD test reads D{r+38} — a row 38 rows below the current one — for every row from 18 to 842. | The GOLD test reads the current row's today-volume (D{r}). |
| `C.dv2` | `Main!J18:J842` | Today Delivery | Strict '>' comparisons: exactly 60 becomes '50% Plus'; exactly 40 or 80 can produce an empty string. | Closed buckets: <40 Low Del, 40-50, 50-60, 60-80, >80 '80% Plus'. |
| `R.cmp_nd` | `Analysis!I2:I6` | LAST | Windows 5, 7 and 10 all evaluate to the same 4-row range G15:G18. | Windows use exactly n rows (5, 7 and 10 respectively). |
| `R.avg_price_nd` | `Analysis!J2:J6` | Avg Price | Mean of the per-day average price over the lookback window. | — |
| `R.pct_change_nd` | `Analysis!K2:K6` | % Change | Mean of the daily change ratio over the lookback window. | — |
| `R.delivery_nd` | `Analysis!L2:L6` | Delivery % | Windows 5, 7 and 10 all evaluate to the same 4-row range L15:L18. | Windows use exactly n rows. |
| `R.action_nd` | `Analysis!M2:M6` | Action | Mean of the Action metric (qty per trade) over the lookback window. | — |
| `R.cmp_vs_avg` | `Analysis!N2:N6` | CMP, Avg Price | Verdict comparing the windowed CMP with the windowed average price. | — |
| `B.avg_delivery` | `Analysis!L13` | Delivery % | Mean delivery percentage across the populated 15:200 block. | — |
| `B.avg_action` | `Analysis!P13` | Action | Mean Action across the populated block. The raw formula uses AVERAGEIF(P15:P200,"<>#VALUE!") which behaves as a plain average. | — |
| `B.avg_trades` | `Analysis!K13` | TOTALTRADES | Mean number of trades across the populated block. | — |
| `S.today_delivery` | `Main!B18:B842` | Delivery %, Symbol, Date | Sum of today's delivery percentage for the selected symbol and date. | — |
| `S.avg_delivery` | `Main!C18:C842` | Delivery %, Symbol | All-history mean delivery percentage for the symbol. | — |
| `S.today_volume` | `Main!D18:D842` | TOTTRDQTY, Symbol, Date | Traded quantity for the selected symbol and date. | — |
| `S.avg_volume` | `Main!E18:E842` | TOTTRDQTY, Symbol | All-history mean traded quantity for the symbol. | — |
| `S.today_trades` | `Main!F18:F842` | TOTALTRADES, Symbol, Date | Number of trades for the selected symbol and date. | — |
| `S.avg_trades` | `Main!G18:G842` | TOTALTRADES, Symbol | All-history mean number of trades for the symbol. | — |

## Parity evidence

The workbook stores Excel's cached results for every formula, so it doubles as an oracle. `scripts/parity_check.py` recomputes each cell and compares:

| Scope | Cells compared | Result in `original` mode |
|---|---|---|
| `Analysis!M15:W200`, `I2:N6`, baselines | 2,073 | 100% match (parity suite) |
| `Main!B18:J842` | 7,425 | 100% match (parity suite) |

Cells whose workbook formula contains `#REF!` (finding B10) and cells whose cached value is an Excel error have no usable oracle; they are reported as *unavailable* rather than as matches.

