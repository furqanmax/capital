# Workbook Inspection Report

* **Source file:** `Saif-Stock Market Data File-Sample.xlsm`
* **Inspected at:** 2026-10-18 21:15:57
* **Mode:** read-only. Macros are decompressed as text for documentation and are **never executed**.

## Worksheets

| Sheet | State | Used range | Rows | Columns | Tables | Charts | Comments | Hidden rows |
|---|---|---|---|---|---|---|---|---|
| Data | visible | A1:L1192 | 1192 | 12 | `Table1` = A1:L386038 | 0 | 0 | 0 |
| Analysis | visible | A1:AA200 | 200 | 27 | `data` = A14:L200, `Table5` = A1:A2 | 0 | 1 | 0 |
| Main | visible | A1:O842 | 842 | 15 | `Table3` = A17:J842 | 0 | 1 | 0 |
| list | visible | A1:G1043 | 1043 | 7 | — | 0 | 0 | 0 |

## Defined names

| Name | Scope | Hidden | Refers to |
|---|---|---|---|
| `Slicer_D._Ind` | workbook | no | `#N/A` |
| `Slicer_DV1` | workbook | no | `#N/A` |
| `Slicer_DV2` | workbook | no | `#N/A` |
| `symbol` | workbook | no | `Table5[Symbol]` |

## Data validation

* `Analysis!A1` — list, source `list!$A$1:$A$583`
* `Analysis!A2` — list, source `list!$A$1:$A$1050`
* `Main!B1:B15` — list, source `#ref!` **← broken reference (`#ref!`)**

## Conditional formatting

* **Analysis:** R3, R4, R5, R7, R8, R9:R12, K2:K6, N2, N3:N6, L2:L6, R15, O15, P15, R16:R18 R20:R23 R25:R103, O16:O134, P16:P134, W15, T15, W16:W103, R19, R24, R104:R150, W104:W150, O135:O150, P135:P150, U15, V15, T16:T20, U16:U20, V16:V20, T21:T150, U21:U150, V21:V150, R151:R200, W151:W200, T151:T200, U151:U200, V151:V200, O151:O200, P151:P200
* **Main:** I1:I17, N18:N21, N22 N24, N23, N25, A591:A592 A594:A636 A638:A659 A661:A673 A675:A842, A591:A592 A594:A615 A619:A630 A632:A636 A640:A641 A643 A645:A659 A661:A663 A665:A666 A668:A672 A675 A678:A809 A815:A842, A616, A617:A618, A631, A638, A639, A642, A644, A664, A667, A673, A676, A677, A810:A814, H18, J18, I18, H19:H842, J19:J842, I19:I842
* **list:** A574:A575 A577:A619 A621:A642 A644:A656 A658:A1048576 D2 D8 D12 G2 G8 G12, A574:A575 A577:A619 A621:A642 A644:A656 A658:A1048576 D8 G8, A574:A575 A577:A598 A602:A613 A615:A619 A623:A624 A626 A628:A642 A644:A646 A648:A649 A651:A655 A658 A661:A792 A798:A1048576, A574:A575 A577:A619 A621:A642 A644:A656 A658:A1048576 D2 D8 G2 G8, D2 D12 G2 G12, A574:A575 A577:A619 A621:A642 A644:A656 A658:A1048576 D8 D12 G8 G12, A599, A600:A601, A614, A621, A622, A625, A627, A647, A650, A656, A659, A660, D12, D12 G12, D8, D2, G12, G8, G2, A793:A797

## Cell comments

* `Analysis!W14` ( ): dell: Captures Total Trades increase due to Distribution where there is higher retail participation
* `Main!A44` ( ): User: was not coming through in Screener but selected as per 9/24 B/S nos available on Screener, the Inv Ratio was compliant (<33%) which wasn’t the case when 3/24 numbers were used

## VBA project

### Module `Module1.bas`

```vb
Attribute VB_Name = "Module1"
Sub refresh()
'
' refresh Macro
'

'
    ActiveWorkbook.RefreshAll
End Sub
```

### Module `ThisWorkbook.cls`

```vb
Attribute VB_Name = "ThisWorkbook"
Attribute VB_Base = "0{00020819-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
```

### Module `Sheet1.cls`

```vb
Attribute VB_Name = "Sheet1"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
```

### Module `Sheet2.cls`

```vb
Attribute VB_Name = "Sheet2"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
```

### Module `Sheet3.cls`

```vb
Attribute VB_Name = "Sheet3"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
```

The macro performs `ActiveWorkbook.RefreshAll` only. The application does not execute it; imports read cached values through `openpyxl`.

## Drawings and controls

* `xl/drawings/drawing1.xml`
* `xl/drawings/vmlDrawing1.vml`
* `xl/drawings/drawing3.xml`
* `xl/drawings/vmlDrawing2.vml`

## Formula inventory summary

| Sheet | Category | Cells |
|---|---|---|
| Analysis | aggregate | 3 |
| Analysis | classification | 1122 |
| Analysis | rolling | 396 |
| Analysis | transform | 591 |
| Main | aggregate | 4950 |
| Main | classification | 2475 |

### Cached value health

| Sheet | number | text | date | blank | error |
|---|---|---|---|---|---|
| Analysis | 808 | 243 | 0 | 695 | 366 |
| Main | 2700 | 900 | 0 | 75 | 3750 |

> A large number of cached errors is itself an audit finding: the workbook's saved state contains formulas that do not evaluate.
