# Task 8 visual regression goldens

These seven PNGs are representative visual gates from the real package build after Task 8 Fix Round 1 on 2026-08-05. They are intentionally selected rather than a copy of every rendered page. The source PDFs under `tests/output/` are generated artifacts.

| Gate | Golden | Source page | Result and checks |
|---|---|---:|---|
| Cover | `cover-stress-p01.png` | stress 1 | Pass: title, subtitle, metadata rows, spacing, and Chinese glyphs are clear; no clipping or black squares. |
| Executive summary | `executive-summary-sample-p02.png` | sample 2 | Pass: summary, insight cards, roadmap, and TOC remain legible with consistent borders and hierarchy. |
| Multipage TOC | `toc-continuation-stress-p04.png` | stress 4 of TOC pages 3-4 | Pass: repeated table header, entries 9-12, long Chinese/English headings, and numbering are readable and not duplicated. |
| Mixed text/image | `mixed-image-text-stress-p15.png` | stress 15 | Pass: the odd `half` placement uses a 238.1102 pt left column, preserves the source ratio, and keeps its caption above the next section without overlap. |
| Split table | `split-table-stress-p08.png` | stress 8 of table pages 6-15 | Pass: repeated header and rows 022-032 are complete; wrapped English abbreviations and source ID remain readable inside cell boundaries. |
| Recommendation roadmap | `recommendation-roadmap-stress-p02.png` | stress 2 | Pass: four insight rows and four roadmap priorities are complete, aligned, and free of isolated headings. |
| Source appendix | `source-appendix-stress-p28.png` | stress 28 of appendix pages 27-28 | Pass: sources 18-30 appear once, link labels wrap safely, dividers align, and the disclaimer stays inside the page. |

Structural checks complement the visual review: the sample has 7 pages; the stress report has 28; every stress page has extractable Chinese; the TOC spans pages 3-4; the table contains exactly 100 data rows; all 12 section headings share a page with following content; and source numbering 1-30 appears exactly once. The stress report has six rendered image objects: two evidence placements plus four charts.

Fix Round 1 corrects the earlier acceptance wording: before the fix, `ImageSpec.layout` was ignored and `half`/`full` both rendered at 493.2283 pt. The focused real-PDF regression now measures paired halves at 238.1102 pt each, an odd half at 238.1102 pt in the left half-column, and full at the unchanged 493.2283 pt. The paired-half visual probe also confirms two columns with each caption directly below its image and no overlap; `image_count` and `rendered_image_keys` remain exact.

The packaged manifest contains the curated multimodal asset set. The stress fixture uses `ht_generated_chart_001` exactly twice across sections: one `half` and one `full`, so its placement behavior remains deterministic without relying on a remote URL.

`tests/output/legacy/` preserves earlier six-page `rendered/` and `rendered_all/` probes so file browsers show the current `sample-pages/` and `stress-pages/` as the only canonical acceptance renders.

This macOS host did not contain the Docker image's Noto CJK files. The real `register_fonts`/font-probe path ran with the documented STHeiti local fallback; container verification with Dockerfile-installed Noto remains pending because Docker is unavailable.
