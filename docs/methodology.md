# Methodology

CivicSignal treats search results as **evidence about a question**, not as answers. Each check below describes the search results; none of them decides what is true. Thresholds were fixed before evaluating on the captured data, and the wording of every finding is deliberately hedged ("potentially stale", "may not be independent").

## 1. Relevance — does a result address the question?

Each result's title, snippet and URL path are compared with the query's **anchor words** (its distinctive words, e.g. *aadhaar*; *pm*, *kisan*).

| Level | Rule |
|---|---|
| OFF_TOPIC | no anchor word (a 1–2 letter anchor such as "pm" only counts next to another anchor) |
| LOW | fewer than half the anchors (for a two-anchor topic: only one of them) |
| MEDIUM | at least half the anchors |
| HIGH | all anchors and at least half the query words |

A year mismatch (query says 2026, result only names other years) lowers the level one step.

Relevance **gates comparison**: claims from LOW or OFF_TOPIC results are kept as evidence but cannot create a conflict, and dates on off-topic pages are not reported as stale information about the question. The original search's relevance is reported separately from the relevance of follow-up results.

## 2. Claims — what values do the results state?

Dates and amounts are extracted from titles and snippets, each with:

- **subject** — e.g. "installment 23", "children aged 5–17", or the investigated topic,
- **attribute** — deadline, expected date, fee, late fee, annual benefit, …,
- **value interval** — "July 2026" is 1–31 July; "about ₹4,000" is a range,
- **temporal role** — deadline, expected event, past event, historical reference, publication date, …,
- the **evidence spans** it was read from.

Only claims with the same subject and attribute are compared. Values whose intervals overlap agree ("July 2026" and "20 July 2026" do not conflict). Historical references, publication stamps, tender/bid dates and unresolved subjects are excluded from comparison — and the report says how many were excluded and why.

## 3. Staleness — is a passed date presented as current?

A temporal role is assigned from the nearest cue in the same clause ("last date", "expected", "will be released", "was extended from", …). A finding is raised only when:

- a **deadline** has entirely passed, or
- an **expected** date has entirely passed (an event still presented as upcoming).

Explicit past-tense narration ("the deadline was …") is historical and never flagged. Severity: HIGH if expired more than 60 days and ranked in the top 5; MEDIUM if expired more than 14 days; LOW otherwise.

## 4. Contradiction — do sources disagree?

Within a cluster of comparable claims, two or more non-overlapping value groups stated by different sources is a **cross-source conflict**; one page stating two different values is a **within-source contradiction**.

Not contradictions: historical vs current dates, different subjects (23rd vs 24th installment), different attributes (fee vs late fee), overlapping precision.

**No value is ever chosen.** Values are listed in value order, never by how many sources repeat them, and the report says which values (if any) an official source states.

## 5. Duplication and independence — how many independent sources?

Pages are compared using 3-word phrases that contain at least one distinctive word (so generic wording like "last date 14 june 2026" never links two pages).

| Relation | Rule |
|---|---|
| Near-identical wording | containment ≥ 0.80 |
| Substantial overlap | containment ≥ 0.50, or an identical run of ≥ 8 words with ≥ 3 distinctive words |
| Related headline | distinctive-title-word Jaccard ≥ 0.70 (reported, does not merge) |
| Same publisher | same registrable domain (except multi-tenant platforms like YouTube) |

Linked pages form **apparently independent groups**. The report shows the funnel *result appearances → unique URLs → distinct pages → domains → independent groups*, and for every value how many independent groups state it. Similarity is described as "potentially repeated or syndicated content", never as copying.

## 6. Authority — who published it?

Source type is context, not a score:

- **Official** — `.gov.in`, `.nic.in`, other government suffixes, Indian academic/research institutions, a short documented list of official bodies on other domains,
- **User generated** — video, social and forum platforms,
- **Established news** — a documented list of news organisations,
- **Tertiary** — aggregators, explainer and jobs/results portals,
- **Unknown** — everything else.

Three things are kept strictly separate:

1. **official pages found** in the results,
2. **official pages that address the topic** (official *and* HIGH/MEDIUM relevance),
3. **official sources that state a specific claim value**.

Only (3) counts as official support for a value. An official page about the scheme that does not state the disputed date is reported as "Official pages address the topic but do not state this value".

## 7. Follow-up planning — when to search again

| Reason | Trigger | Search |
|---|---|---|
| Official-source lookup | no relevant official page, or a conflict no official source settles | the question, sharpened by the conflict's subject, restricted to the official domains found or mentioned, else `site:gov.in OR site:nic.in` |
| News check | an expected date has passed | `engine=google_news` — has the event been reported? |
| Recency check | a deadline has passed | `tbs=qdr:m` — do recent pages say something else? |
| Language comparison | only when the user asks | same query in another `hl` |

Budget: at most 2 follow-ups (3 searches in total). Every follow-up records the evidence that triggered it and the question it tries to answer, and its outcome is classified in plain language (e.g. "Official support found", "Official pages found, but they do not state the claim values", "No results returned"). Nothing in the planner refers to a specific scheme, site or query.

## 8. Status

| Status | When |
|---|---|
| Conflicting claims | a cross-source or within-source conflict exists |
| Potentially stale | stale dates, no conflict |
| Insufficient evidence | no data, or the original search was mostly off-topic and follow-ups recovered fewer than 3 relevant results |
| Minor issues | only lower-impact observations (relevance, source mix, repeated content) |
| No issues detected by the checks run | nothing found — explicitly *not* a verification |

## What CivicSignal deliberately does not do

- It does not read full web pages — only what SerpApi returns (titles, snippets, answer boxes).
- It does not use an LLM to judge claims; every rule is inspectable and tested.
- It does not majority-vote, rank values, or call anything true, false, fake or misinformation.
- It does not treat an official domain as proof — official pages can be outdated too.
