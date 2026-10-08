"""
Phase 7: Evidence Map completion — value-specific traceability, official-support reasons,
conflict wording, staleness per value, investigation trail, backend-owned labels.
Real captures (mock mode) plus synthetic checks. No new searches.
"""

import asyncio
import re
from datetime import date

import pytest

from civicsignal.config import BACKEND_DIR, Settings
from civicsignal.db import Repository
from civicsignal.models import InvestigateRequest
from civicsignal.orchestrator import Investigator
from civicsignal.report import (
    OUTCOME_LABEL,
    SUPPORT_NONE,
    SUPPORT_STATES,
    SUPPORT_TOPIC_ONLY,
    build_report,
    excerpt,
    value_label,
)
from civicsignal.serp import SerpApiClient

from .conftest import base_fixture_queries

FIXTURES = BACKEND_DIR / "fixtures" / "serpapi"
BANNED = re.compile(r"\b(true|false|correct|incorrect|fake|misinformation|verified|authoritative)\b", re.I)
RANKING = re.compile(r"\b(most sources|majority|winner|more sources|most common|likely correct)\b", re.I)
ENUM_LIKE = re.compile(r"\b[a-z]+_[a-z_]+\b")  # snake_case internal names


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p7")
    settings = Settings(mock_serpapi=True, fixtures_dir=FIXTURES, cache_enabled=False, database_path=tmp / "db.sqlite3")
    repo = Repository(settings.database_path)
    repo.init_schema()
    investigator = Investigator(settings, SerpApiClient(settings), repo, today=lambda: date(2026, 10, 1))
    out = {}
    for q in base_fixture_queries(FIXTURES):
        inv = asyncio.run(investigator.investigate(InvestigateRequest(query=q)))
        out[q] = (inv, build_report(inv))
    return out


def _report(data, q):
    return data[q][1]


# --- helpers ------------------------------------------------------------------------------------


def test_excerpt_is_an_exact_prefix_on_a_word_boundary():
    text = "word " * 100
    cut, truncated = excerpt(text)
    assert truncated and text.startswith(cut) and len(cut) <= 220 and not cut.endswith(" ")
    short = "Free Aadhaar update till 14 June 2026."
    assert excerpt(short) == (short, False)


@pytest.mark.parametrize("value, label", [
    ("2026-06-20", "20 Jun 2026"),
    ("2026-07-01/2026-07-31", "Jul 2026"),
    ("2026-06-01/2026-07-31", "Jun–Jul 2026"),
    ("₹9,000", "₹9,000"),
])
def test_value_label_is_formatting_only(value, label):
    assert value_label(value) == label


# --- G/H: traceability and no invention -----------------------------------------------------------


def test_every_map_value_traces_to_real_claims_and_exact_evidence(data):
    for q, (inv, rep) in data.items():
        claims = {c.id: c for c in inv.claims}
        results = {r.id: r for r in inv.results}
        spans = {e.id: e for e in inv.evidence_spans}
        sources = {s.id for s in inv.sources}
        clusters = {c.id: c for c in inv.clusters}
        runs = {s.id for s in inv.searches}
        for entry in rep.evidence_map:
            cluster = clusters[entry.cluster_id]
            assert [v.value for v in entry.values] == [g.value for g in cluster.value_groups]  # no invented values
            for v, g in zip(entry.values, cluster.value_groups):
                assert {s.source_id for s in v.sources} == set(g.source_ids)  # no invented sources
                assert {s.source_id for s in v.official_sources + v.other_sources} <= set(g.source_ids)
                assert v.evidence, (q, v.value)
                for e in v.evidence:
                    assert e.claim_id in g.claim_ids and e.claim_id in claims  # value-specific
                    assert e.evidence_span_id in claims[e.claim_id].evidence_span_ids
                    span = spans[e.evidence_span_id]
                    assert span.text.startswith(e.excerpt) and e.excerpt in span.text  # exact substring
                    assert e.result_id == span.result_id and e.result_id in results
                    assert e.search_run_id == results[e.result_id].search_run_id and e.search_run_id in runs
                    assert e.source_id is None or e.source_id in sources


def test_official_support_flag_and_sources_match_the_value_support_model(data):
    for inv, rep in data.values():
        support = {(s.cluster_id, s.value): set(s.official_source_ids) for s in inv.official_support or []}
        for entry in rep.evidence_map:
            for v in entry.values:
                ids = support.get((entry.cluster_id, v.value), set())
                assert v.stated_by_official_source == bool(ids)
                assert {s.source_id for s in v.official_sources} == ids
                assert all(not s.official for s in v.other_sources)
                expected = SUPPORT_STATES if ids else (SUPPORT_TOPIC_ONLY if inv.metrics.primary_sources else SUPPORT_NONE)
                assert v.official_support_reason == expected


def test_staleness_is_attached_to_the_value_it_concerns(data):
    for inv, rep in data.values():
        stale = {f.id: set(f.claim_ids) for f in inv.findings if f.kind == "staleness"}
        clusters = {c.id: c for c in inv.clusters}
        for entry in rep.evidence_map:
            for v, g in zip(entry.values, clusters[entry.cluster_id].value_groups):
                for fid in v.stale_finding_ids:
                    assert stale[fid] & set(g.claim_ids)  # traced to a claim of THIS value
                assert v.potentially_stale == bool(v.stale_finding_ids)


# --- B: Aadhaar ----------------------------------------------------------------------------------


def test_aadhaar_recovery_and_value_specific_support(data):
    inv, rep = data["Aadhaar free update last date 2026"]
    assert rep.status.code == "MINOR_ISSUES"
    assert rep.status.explanation.startswith("The original search did not address the question; targeted "
                                             "official-source searches recovered 7 relevant official pages")
    assert rep.landscape.base_relevance == {"off_topic": 10}
    assert rep.landscape.official_pages_found == 11 and rep.landscape.official_topic_sources == 7
    supported_sources = {s.source_id for e in rep.evidence_map for v in e.values for s in v.official_sources}
    assert 0 < len(supported_sources) < 7  # official pages found/addressing topic are NOT all claim support
    assert rep.trail[0].text == "Original search: 10 results (0 relevant, 10 off-topic)."
    assert any(t.kind == "outcome" and t.text.startswith("Official support found") for t in rep.trail)
    verify = {v.domain: v for v in rep.where_to_verify}
    assert any(v.states_values for v in rep.where_to_verify)
    assert verify["old.uidai.gov.in"].states_values == []  # addresses the topic, states no value


# --- C: PM Kisan --------------------------------------------------------------------------------


def test_pm_kisan_conflict_without_a_winner(data):
    inv, rep = data["PM Kisan next installment date 2026"]
    entry = rep.evidence_map[0]
    assert entry.has_conflict and entry.attribute == "expected date" and entry.subject == "installment 23"
    assert [v.value_label for v in entry.values] == ["20 Jun 2026", "Jul 2026"]
    assert all(v.official_support_reason == SUPPORT_TOPIC_ONLY for v in entry.values)
    assert all(v.potentially_stale for v in entry.values)
    text = entry.conflict_explanation
    assert "No retrieved official source states any of these values" in text
    assert "does not decide between them" in text
    assert not RANKING.search(text) and not BANNED.search(text)
    assert not re.search(r"\d+ sources?", text)  # no counts that read as votes
    kinds = [t.text for t in rep.trail]
    assert any("Check news coverage" in t for t in kinds)
    assert any(t.startswith("Additional relevant evidence found") for t in kinds)
    assert any(t.startswith("Conflict remains unresolved") for t in kinds)


# --- D: PAN-Aadhaar -------------------------------------------------------------------------------


def test_pan_values_evidence_and_provenance(data):
    inv, rep = data["PAN Aadhaar link last date 2026"]
    (entry,) = [e for e in rep.evidence_map if e.has_conflict]
    assert entry.attribute == "deadline"
    assert all(v.official_support_reason == SUPPORT_TOPIC_ONLY for v in entry.values)
    labels = {e.found_by_label for v in entry.values for e in v.evidence}
    assert "original search" in labels and labels <= {"original search", "official-source lookup",
                                                       "past-month search", "news search"}
    follow = [e for v in entry.values for e in v.evidence if e.found_by != "base_query"]
    assert follow  # follow-up evidence present and labelled by its search
    assert [f.outcome_label for f in rep.follow_ups] == [
        "Official pages found, but they do not state the claim values", "Newer relevant evidence found"]


# --- E: West Bengal -------------------------------------------------------------------------------


def test_west_bengal_no_results_outcome_invents_nothing(data):
    inv, rep = data["West Bengal Ayushman Bharat deadline 2026"]
    (lookup,) = [f for f in rep.follow_ups if f.reason == "primary_source_lookup"]
    assert lookup.outcome_label == "No results returned" and lookup.results_added == 0
    assert any(t.text.startswith("No results returned") for t in rep.trail)
    lookup_run = next(s.id for s in inv.searches if s.request.reason.value == "primary_source_lookup")
    assert not [e for m in rep.evidence_map for v in m.values for e in v.evidence if e.search_run_id == lookup_run]


# --- A/F: clean controls ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["RTI application fee central government", "Indian passport validity for adults",
                                   "NEET UG 2026 exam date"])
def test_clean_controls_stay_clean(data, query):
    inv, rep = data[query]
    assert not any(e.has_conflict for e in rep.evidence_map)
    assert not any(e.conflict_explanation for e in rep.evidence_map)
    assert rep.follow_ups == [] and rep.trail[-2].text == "No targeted follow-up search was needed."
    for e in rep.evidence_map:
        for v in e.values:
            assert not v.stated_by_official_source or v.official_sources  # no support without a source


def test_rti_fee_agreement_is_readable(data):
    _, rep = data["RTI application fee central government"]
    (fee,) = [e for e in rep.evidence_map if e.attribute == "fee"]
    (v,) = fee.values
    assert v.value_label == "₹10" and len(v.sources) == 2 and v.independent_groups == 2
    assert v.official_support_reason == SUPPORT_TOPIC_ONLY  # RTI has official topic pages, none states ₹10


# --- wording ----------------------------------------------------------------------------------------


def test_new_human_facing_text_is_hedged_and_free_of_internal_names(data):
    for _, rep in data.values():
        texts = [t.text for t in rep.trail] + [f.outcome_label for f in rep.follow_ups]
        texts += [e.conflict_explanation for e in rep.evidence_map if e.conflict_explanation]
        texts += [v.official_support_reason for e in rep.evidence_map for v in e.values]
        texts += [w.why_relevant for w in rep.where_to_verify]
        for t in texts:
            assert not BANNED.search(t), t
        labels = [f.outcome_label for f in rep.follow_ups] + [x.found_by_label for x in rep.where_to_verify]
        labels += [v_.found_by_label for e in rep.evidence_map for v in e.values for v_ in v.evidence]
        labels += [r for e in rep.evidence_map for v in e.values for r in v.temporal_roles]
        for label in labels:
            assert not ENUM_LIKE.search(label), label
    assert all(not ENUM_LIKE.search(lbl) for lbl in OUTCOME_LABEL.values())
