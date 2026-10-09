# Evaluation

All numbers below come from running CivicSignal on the 17 captured SerpApi responses in `backend/fixtures/serpapi/` (8 original searches captured on 1 October 2026, 9 follow-up searches captured on 5 October 2026), with the analysis date fixed to 1 October 2026. They are reproducible with `python -m pytest` and in the dashboard's captured-data mode.

This is a small, hand-checked set of real Indian public-information questions, not a benchmark. The point is to show how the system behaves on real search results — including where it falls short.

## Results on the captured questions

| Question | Status | Searches | Appearances → pages → independent groups | Original search relevance | Follow-up outcomes |
|---|---|---|---|---|---|
| PM Kisan next installment date 2026 | Conflicting | 3 | 32 → 28 → 24 | 10 high, 1 medium, 1 low | Official pages found, but they do not state the claim values · Additional relevant evidence found (news) |
| PAN Aadhaar link last date 2026 | Conflicting | 3 | 32 → 28 → 25 | 8 high, 4 medium | Official pages found, but they do not state the claim values · Newer relevant evidence found (past month) |
| Aadhaar free update last date 2026 | Minor issues | 2 | 20 → 19 → 12 | **10 off-topic** | Official support found |
| West Bengal Ayushman Bharat deadline 2026 | Potentially stale | 3 | 22 → 18 → 18 | 3 high, 5 medium, 5 low | No results returned · Newer relevant evidence found |
| Telangana Rythu Bharosa installment 2026 | Potentially stale | 3 | 26 → 22 → 22 | 6 high, 5 medium, 1 low | No useful official result found · Newer relevant evidence found |
| NEET UG 2026 exam date | Minor issues | 1 | 17 → 13 → 11 | 9 high, 3 low, 5 off-topic | — |
| RTI application fee central government | No issues detected | 1 | 12 → 8 → 8 | 8 high, 4 medium | — |
| Indian passport validity for adults | No issues detected | 1 | 12 → 8 → 6 | 3 high, 9 medium | — |

Follow-up searches were planned for 5 of the 8 questions and none for the 3 without a specific gap (NEET, RTI, passport) — 17 SerpApi searches in total.

## Case notes

**PM Kisan — a real conflict, left unresolved.** Relevant sources give two different dates for the 23rd installment (20 June 2026; July 2026), and both have already passed as of the analysis date. The official-source lookup found pmkisan.gov.in, kisansuvidha.gov.in and kheri.nic.in addressing the scheme, but none states either date — so both values show "no official source states this". The news search added 10 relevant results. CivicSignal does not pick a date.

**Aadhaar — recovering from a bad first search.** All 10 original results were off-topic for the question. The official-source lookup found 11 official pages, 7 of which address the topic, and two PIB pages state the free-update deadline (December 2026) and the earlier ₹75 fee. Without the follow-up, the same question is reported as *Insufficient evidence*. Official support is value-specific: the other 5 official pages that address the topic are listed under "where to verify" but are not counted as support.

**PAN–Aadhaar — old deadlines still ranking.** Deadlines from 2019 and 2025 appear as current in highly relevant results (4 staleness findings). The past-month search added 5 relevant newer results; the official-source lookup found incometaxindia.gov.in addressing the topic without stating any of the values.

**West Bengal — an empty official search is not a failure.** The planner restricted the official lookup to pmjay.gov.in because a result mentioned it; that search returned no results, and the report says so plainly instead of inventing evidence. A 2024 deadline is still presented in the results.

**RTI and passport — clean controls.** No conflicts, staleness or repeated content. RTI's ₹10 fee is stated by 2 independent sources. The status reads "No issues detected by the checks run", which is not a verification.

## Test suite

`python -m pytest` → **472 passed, 1 skipped, 3 expected failures** (offline, about 20 seconds).

- Unit tests for every extractor and detector, with synthetic examples for each rule and each exclusion.
- Fixture tests that pin the behaviour on all 17 captured responses (statuses, findings, follow-up outcomes).
- Traceability tests: every evidence-map value maps to real claims, every excerpt is an exact substring of its evidence span, every referenced ID exists.
- Wording tests: no "true / false / fake / verified / misinformation" in generated text, and no majority-vote language in conflict explanations.
- The 1 skipped test calls the live SerpApi API and only runs when a key is provided.

## Known limitations

These are recorded as strict expected-failure tests, so a future fix is noticed:

1. **Wire stories with reworded headlines** — two outlets running the same story with a headline overlap of 0.67 stay below the 0.70 threshold and are counted as independent.
2. **Generic identical sentences** — an identical sentence made only of generic words plus a date stays "uncertain" and is not linked.
3. **Ambiguous tense** — "CBDT set 31 December 2025 as the deadline" ("set" is both past and present) cannot be resolved by the rules, so it may be flagged as stale when it is historical narration.

Other limitations:

- Only titles and snippets are analysed; a value stated only in the full page text is not seen.
- Relevance is lexical, so homonyms (e.g. a product called "Passport") are not recognised.
- Any government domain counts as official, including foreign ones (e.g. travel.state.gov appears for the passport question).
- Captured data reflects search results on the capture dates; live results will differ.
