"""
End-to-end API tests: FastAPI app + orchestrator + SerpApi client (mock and
scripted-live) + SQLite, all in a temp directory. No network.
"""

import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from civicsignal.api import create_app
from civicsignal.db import Repository
from civicsignal.orchestrator import Investigator
from civicsignal.serp import FixtureStore, SerpApiClient

from .conftest import FAKE_KEY, sample_raw_response

QUERY = "Aadhaar free update last date 2026"


def _app(settings, transport=None):
    repo = Repository(settings.database_path)

    async def no_sleep(_):
        return None

    client = SerpApiClient(settings, transport=transport, sleep=no_sleep)
    investigator = Investigator(settings, client, repo, today=lambda: date(2026, 9, 29))
    return TestClient(create_app(settings, client=client, repository=repo, investigator=investigator))


@pytest.fixture
def mock_settings(tmp_settings, repo_fixtures_dir):
    return tmp_settings.model_copy(
        update={"mock_serpapi": True, "serpapi_api_key": None, "fixtures_dir": repo_fixtures_dir}
    )


def test_health_reports_config_without_secrets(tmp_settings):
    api = _app(tmp_settings)
    r = api.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["serpapi"] == {"configured": True, "mock_mode": False, "cache_enabled": True}
    assert body["llm"] == {"provider": "none", "available": False}
    assert FAKE_KEY not in r.text


def test_investigate_in_mock_mode_end_to_end(mock_settings):
    api = _app(mock_settings)
    r = api.post("/api/investigate", json={"query": f"  {QUERY}  "})
    assert r.status_code == 200
    inv = r.json()
    assert inv["query"] == QUERY  # whitespace normalized
    assert inv["status"] == "completed"
    assert inv["as_of_date"] == "2026-09-29"
    assert inv["data_modes"] == ["mock"]
    assert inv["integrity_status"] == "CONFLICTING"
    runs = {d["detector"]: d["status"] for d in inv["detector_runs"]}
    assert runs == {
        "staleness": "ok",
        "contradiction": "ok",
        "duplication": "ok",
        "authority": "ok",
        "relevance": "ok",
    }
    assert any("RECONSTRUCTED" in w for w in inv["warnings"])

    # Staleness on the reconstructed 2026-09-29 Aadhaar run: four results still present
    # "June 14, 2026" as the free-update deadline (two needed the till/until fix);
    # "till 14 June 2027", "until 30 September 2026" and "until December 31, 2026" are not past.
    findings = [f for f in inv["findings"] if f["kind"] == "staleness"]
    assert {f["date_end"] for f in findings} == {"2026-06-14"}
    assert all(f["days_expired"] == 107 for f in findings)
    flagged_sources = {sid for f in findings for sid in f["source_ids"]}
    by_id = {s["id"]: s for s in inv["sources"]}
    assert len(findings) == 4 and len(flagged_sources) == 4
    assert {by_id[s]["registrable_domain"] for s in flagged_sources} == {
        "youtube.com", "etnownews.com", "techlusive.in"
    }  # 4 sources, 3 domains: two different YouTube videos
    strengths = sorted(f["strength"] for f in findings)
    assert strengths == ["high", "high", "medium", "medium"]  # unknown positions can't be HIGH
    spans = {e["id"]: e for e in inv["evidence_spans"]}
    for f in findings:
        for eid in f["evidence_span_ids"]:
            assert "2026" in spans[eid]["text"]

    # Cross-source conflict on the Aadhaar deadline: 2026-06-14 (4 sources) vs 2027-06-14 (zeenews).
    # The children-aged-5-17 deadline has its own subject and is NOT part of the conflict.
    (conflict,) = [f for f in inv["findings"] if f["kind"] == "contradiction"]
    assert conflict["scope"] == "cross_source" and conflict["attribute"] == "deadline"
    assert conflict["conflicting_values"] == ["2026-06-14", "2027-06-14"]
    assert [len(g["source_ids"]) for g in conflict["value_groups"]] == [4, 1]
    assert "does not make it correct" in conflict["explanation"]
    children = [c for c in inv["clusters"] if "population:children" in c["subject"]]
    assert len(children) == 1 and children[0]["has_conflict"] is False

    # Regression (fixed false conflict): loansjagat's EPFO e-mail deadline (2026-12-31) used to join
    # the Aadhaar deadline conflict. Its clause names no programme and the page also covers EPFO,
    # so its subject is unresolved and it is never compared.
    sources = {s["id"]: s for s in inv["sources"]}
    (epfo,) = [c for c in inv["claims"] if sources[c["source_id"]]["domain"] == "loansjagat.com"]
    assert epfo["subject"] == "entity:unresolved" and epfo["comparable"] is False
    assert epfo["id"] not in conflict["claim_ids"]

    search = inv["searches"][0]
    assert search["request"]["reason"] == "base_query"
    assert search["fixture_kind"] == "reconstructed"

    m = inv["metrics"]
    assert m["total_search_results"] == 7
    assert m["unique_urls"] == 7
    assert m["unique_sources"] == 7
    assert m["unique_domains"] == 6  # two different YouTube videos share one domain
    assert m["extracted_claims"] == len(inv["claims"]) == 7  # one date claim per result
    kinds = sorted(f["kind"] for f in inv["findings"])
    # 4 staleness + 1 cross-source conflict + 1 "limited primary-source coverage" (no official page)
    assert kinds == ["authority", "contradiction", "staleness", "staleness", "staleness", "staleness"]
    assert m["finding_events"] == 6
    assert m["primary_sources"] == 0 and m["source_types"]["user_generated"] == 2
    assert m["claim_clusters"] == 2
    # independence view: two YouTube videos share a platform domain but not a publisher -> 7 groups
    assert m["apparent_duplicate_groups"] == 0 and m["independent_source_groups"] == 7
    assert m["unique_sources"] == 7  # never replaced by the independence count

    # Round trip: stored report, listing, raw response
    got = api.get(f"/api/investigations/{inv['id']}")
    assert got.status_code == 200 and got.json()["metrics"] == m
    listed = api.get("/api/investigations").json()
    assert listed[0]["id"] == inv["id"]
    raw = api.get(f"/api/investigations/{inv['id']}/searches/{search['id']}/raw")
    assert raw.status_code == 200
    assert len(raw.json()["organic_results"]) == 7


def test_live_path_with_scripted_serpapi(tmp_settings):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=sample_raw_response())

    api = _app(tmp_settings, transport=httpx.MockTransport(handler))
    inv = api.post("/api/investigate", json={"query": "aadhaar update deadline", "hl": "te", "max_results": 50}).json()
    assert inv["data_modes"] == ["live"]
    params = seen[0].url.params
    assert params["hl"] == "te" and params["gl"] == "in"
    assert params["num"] == "10"  # clamped to MAX_RESULTS_PER_SEARCH
    assert inv["metrics"]["unique_sources"] == 5
    assert any("no usable URL" in w for w in inv["warnings"])
    assert FAKE_KEY not in json.dumps(inv)


def test_search_failure_yields_failed_investigation_not_500(tmp_settings):
    def handler(request):
        return httpx.Response(401, json={"error": "Invalid API key."})

    api = _app(tmp_settings, transport=httpx.MockTransport(handler))
    r = api.post("/api/investigate", json={"query": QUERY})
    assert r.status_code == 200
    inv = r.json()
    assert inv["status"] == "failed"
    assert inv["integrity_status"] == "INSUFFICIENT_EVIDENCE"
    assert inv["searches"][0]["status"] == "failed"
    assert "SerpApiAuthError" in inv["searches"][0]["error"]
    assert inv["errors"]
    assert api.get(f"/api/investigations/{inv['id']}/searches/{inv['searches'][0]['id']}/raw").status_code == 404


def test_detector_failure_is_isolated(mock_settings, monkeypatch):
    from civicsignal.detectors import staleness

    def boom(*args, **kwargs):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(staleness, "find_stale", boom)
    inv = _app(mock_settings).post("/api/investigate", json={"query": QUERY}).json()
    assert inv["status"] == "completed"  # search data still reported
    runs = {d["detector"]: d for d in inv["detector_runs"]}
    assert runs["staleness"]["status"] == "unavailable" and "detector crashed" in runs["staleness"]["message"]
    assert runs["contradiction"]["status"] == "ok"  # other detector unaffected
    assert {f["kind"] for f in inv["findings"]} == {"contradiction", "authority"}
    assert inv["metrics"]["total_search_results"] == 7


def test_duplication_failure_is_isolated(mock_settings, monkeypatch):
    from civicsignal.detectors import duplication

    def boom(*args, **kwargs):
        raise RuntimeError("duplication crashed")

    monkeypatch.setattr(duplication, "detect_duplication", boom)
    inv = _app(mock_settings).post("/api/investigate", json={"query": QUERY}).json()
    runs = {d["detector"]: d["status"] for d in inv["detector_runs"]}
    assert runs["duplication"] == "unavailable"
    assert runs["staleness"] == runs["contradiction"] == "ok"
    assert inv["integrity_status"] == "CONFLICTING"
    assert inv["independence"] is None
    assert inv["metrics"]["independent_source_groups"] is None and inv["metrics"]["unique_sources"] == 7


def test_claim_extraction_failure_marks_both_claim_detectors_unavailable(mock_settings, monkeypatch):
    from civicsignal import orchestrator

    def boom(*args, **kwargs):
        raise RuntimeError("extraction crashed")

    monkeypatch.setattr(orchestrator, "extract_claims", boom)
    inv = _app(mock_settings).post("/api/investigate", json={"query": QUERY}).json()
    runs = {d["detector"]: d["status"] for d in inv["detector_runs"]}
    assert runs["staleness"] == runs["contradiction"] == "unavailable"
    assert runs["duplication"] == "ok"  # needs only source text
    assert inv["status"] == "completed" and inv["claims"] == []
    assert {f["kind"] for f in inv["findings"]} <= {"authority", "relevance"}  # claim-free analyzers still ran


def test_staleness_thresholds_come_from_settings(mock_settings):
    settings = mock_settings.model_copy(update={"staleness_high_days": 200})
    inv = _app(settings).post("/api/investigate", json={"query": QUERY}).json()
    assert {f["strength"] for f in inv["findings"] if f["kind"] == "staleness"} == {"medium"}  # 107 < 200


def test_mock_mode_unknown_query_fails_gracefully(mock_settings):
    inv = _app(mock_settings).post("/api/investigate", json={"query": "something with no fixture"}).json()
    assert inv["status"] == "failed"
    assert "no fixture" in inv["errors"][0]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "ab"},
        {"query": "x" * 301},
        {"query": "valid query\x00"},
        {"query": "valid query", "hl": "english"},
        {"query": "valid query", "max_results": 0},
    ],
)
def test_input_validation(tmp_settings, body):
    assert _app(tmp_settings).post("/api/investigate", json=body).status_code == 422


def test_unknown_investigation_404(tmp_settings):
    assert _app(tmp_settings).get("/api/investigations/inv_missing").status_code == 404


def test_repository_schema_version(tmp_settings):
    repo = Repository(tmp_settings.database_path)
    repo.init_schema()
    repo.init_schema()  # idempotent
    import sqlite3

    with sqlite3.connect(tmp_settings.database_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "investigations", "queries", "search_runs", "search_results", "sources",
        "evidence_spans", "claims", "claim_clusters", "findings", "detector_runs",
    } <= tables


def test_fixture_store_roundtrip(tmp_path):
    store = FixtureStore(tmp_path)
    store.save("x", "google", "Q", {"gl": "in"}, {"organic_results": []}, kind="captured", note="n")
    f = store.find("google", "q")
    assert f and f.kind == "captured" and f.note == "n"
    assert store.find("google_news", "q") is None


def test_fixture_store_prefers_captured_over_reconstructed(tmp_path):
    store = FixtureStore(tmp_path)
    store.save("a_reconstructed", "google", "Q", {}, {"organic_results": []}, kind="reconstructed")
    store.save("b_captured", "google", "Q", {}, {"organic_results": []}, kind="captured")
    assert store.find("google", "q").kind == "captured"
