# Architecture

CivicSignal is a FastAPI backend that runs an investigation pipeline over SerpApi results, and a React + Vite dashboard that renders the resulting evidence report. Everything is deterministic: the same SerpApi responses always produce the same report.

## Components

```
frontend/ (React 18 + Vite 5)
   │  /api  (Vite dev proxy → :8000)
   ▼
backend/civicsignal/api/app.py  (FastAPI)
   │
   ▼
orchestrator.py  — Investigator.investigate(query)
   │
   ├─ serp/            SerpApi client
   │    client.py      async httpx, retry with backoff, secrets scrubbed from logs/errors
   │    cache.py       file cache keyed by request (TTL, default 24 h)
   │    fixtures.py    mock mode: serves captured responses from backend/fixtures/serpapi
   │    parsing.py     organic_results, answer_box, related_questions, top_stories, news_results
   │
   ├─ normalize/       SearchResult (one appearance) → Source (one canonical page) → EvidenceSpan
   │                   (exact title/snippet text with offsets); URL canonicalisation merges
   │                   tracking-parameter / www / m. variants of the same page
   │
   ├─ claims/          typed claims read from evidence spans
   │    extract.py     date and amount claims, each tied to its evidence spans
   │    subjects.py    subject qualifiers ("23rd installment", "children 5–17") and query anchors
   │    amounts.py     ₹ / Rs amounts and ranges
   │    eligibility.py which claims may be compared (relevance, historical, procurement, …)
   │    clustering.py  comparable claims → clusters → value groups (overlapping intervals agree)
   │
   ├─ detectors/       each returns findings with links to claims, results, sources, evidence
   │    staleness/     temporal roles + expired deadlines / expected dates
   │    contradiction.py  within-source and cross-source conflicts per cluster
   │    duplication.py    near-identical wording, overlap, same publisher → independence groups
   │    authority.py      source type (official, news, user-generated, tertiary, unknown)
   │    relevance.py      how well each result addresses the question
   │
   ├─ planner.py       decides whether a follow-up search is needed, and why
   ├─ follow_ups.py    runs it, re-analyses all evidence, classifies the outcome
   │
   ├─ db/              SQLite: investigations, searches (raw responses kept for audit)
   └─ report.py        build_report(investigation) → InvestigationReport (pure function)
```

## Investigation flow

1. **Original search** — `engine=google`, `gl=in`, `hl=en`, up to 10 results per block.
2. **Normalise** results into appearances, canonical pages and evidence spans.
3. **Extract claims** (dates, amounts) with subject, attribute, value interval and temporal role.
4. **Run detectors** — relevance first (it gates which claims can be compared), then authority, staleness, contradiction, duplication.
5. **Plan follow-ups** — at most 2, each with a reason code (`PRIMARY_SOURCE_LOOKUP`, `NEWS_CHECK`, `RECENCY_CONTRAST`, `REGIONAL_COMPARE`), the evidence that triggered it and the question it tries to answer. No gap, no follow-up.
6. **Run follow-ups** and **re-analyse everything together**. Results keep their search role, so the original search's relevance is measured separately from what follow-ups recovered.
7. **Classify each follow-up outcome** — e.g. `support_found`, `official_found_not_addressing_claims`, `no_results`, `newer_relevant_evidence`.
8. **Derive the status** — `CONFLICTING` › `POTENTIALLY_STALE` › `INSUFFICIENT_EVIDENCE` › `MINOR_ISSUES` › `NOT_ASSESSED`. There is deliberately no "verified" or "clear" status.
9. **Persist** the investigation and every raw SerpApi response.
10. **Report** — `GET /api/investigations/{id}/report` arranges the stored evidence into the summary, evidence map, investigation trail and where-to-verify list. The report layer does no analysis of its own.

## Evidence model and traceability

```
SearchRun ─► SearchResult ─► EvidenceSpan (exact text + offsets)
                 │
                 ▼
               Source (canonical page, domain, source type)

ExtractedClaim ─► evidence_span_ids, result_id, source_id
ClaimCluster   ─► value groups ─► claim ids, source ids
Finding        ─► claim ids, result ids, source ids, evidence span ids
```

Every value in the evidence map carries the claim, result, evidence-span and search IDs it came from, and every excerpt shown is an exact substring of the stored evidence span. Tests enforce this for all captured investigations.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Service status, mock/live mode, limits |
| GET | `/api/demo-queries` | Questions available in captured-data mode |
| POST | `/api/investigate` | Run an investigation `{ "query": "..." }` |
| GET | `/api/investigations` | Recent investigations |
| GET | `/api/investigations/{id}` | Full investigation (all evidence) |
| GET | `/api/investigations/{id}/report` | Human-readable evidence report |
| GET | `/api/investigations/{id}/searches/{search_id}/raw` | The raw SerpApi response (key removed) |

Interactive docs: `http://localhost:8000/docs`.

## Configuration

All settings come from environment variables or a `.env` file in the project root (see `.env.example`). The most important:

| Variable | Default | Meaning |
|---|---|---|
| `SERPAPI_API_KEY` | — | Required for live mode. Server-side only; never sent to the frontend or logged |
| `MOCK_SERPAPI` | `false` | `true` serves captured responses from `backend/fixtures/serpapi` |
| `MAX_SEARCHES_PER_INVESTIGATION` | `3` | Hard cap on SerpApi calls per investigation |
| `MAX_FOLLOW_UP_SEARCHES` | `2` | Cap on planned follow-ups |
| `PLANNER_ENABLED` | `true` | Turn follow-up searches off entirely |
| `SERPAPI_CACHE_ENABLED` | `true` | Cache responses to avoid repeat spend |
| `STALENESS_*` | 60 / 5 / 14 | Severity thresholds for stale dates |
| `LLM_PROVIDER` | empty | Optional; the pipeline does not need an LLM |

## Captured data

`backend/fixtures/serpapi/` holds 17 real SerpApi responses captured on 1 October 2026 (8 original searches) and 5 October 2026 (9 follow-up searches). The API key is scrubbed from captured parameters. In mock mode the fixture store matches requests on engine, normalised query and the `tbs` time filter, and the UI and report label the data "Captured SerpApi data · not a live search" with the capture dates.
