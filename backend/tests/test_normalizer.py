from datetime import datetime, timezone

from civicsignal.models import DataMode, ResultType, SearchRequest, SerpApiResponse
from civicsignal.normalize import build_results, compute_metrics, group_sources
from civicsignal.serp import parse_response

from .conftest import sample_raw_response


def _response(raw):
    return SerpApiResponse(
        request=SearchRequest(q="aadhaar update deadline"),
        raw=raw,
        data_mode=DataMode.MOCK,
        fetched_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


def _normalize(raw):
    resp = _response(raw)
    results = build_results("srch_1", resp, parse_response(raw, "google"))
    sources = group_sources(results)
    return results, sources


def test_parse_handles_malformed_and_optional_fields():
    items = parse_response(sample_raw_response(), "google")
    types = [i.result_type for i in items]
    assert types.count(ResultType.ANSWER_BOX) == 1
    assert types.count(ResultType.ORGANIC) == 6  # the non-dict entry is skipped
    assert types.count(ResultType.RELATED_QUESTION) == 1
    pos6 = next(i for i in items if i.result_type == ResultType.ORGANIC and i.position == 6)
    assert pos6.title == "" and pos6.snippet == ""  # None/missing -> empty, string position -> int


def test_parse_tolerates_garbage():
    assert parse_response({}, "google") == []
    assert parse_response({"organic_results": "nope"}, "google") == []
    assert parse_response(None, "google") == []  # type: ignore[arg-type]


def test_google_news_nested_stories():
    raw = {
        "news_results": [
            {"position": 1, "title": "A", "link": "https://a.com/1", "source": {"name": "A News"}},
            {"title": "Cluster", "stories": [
                {"title": "B", "link": "https://b.com/2"},
                {"title": "C", "link": "https://c.com/3"},
            ]},
        ]
    }
    items = parse_response(raw, "google_news")
    assert [i.title for i in items] == ["A", "B", "C"]
    assert all(i.result_type == ResultType.NEWS for i in items)
    assert items[0].displayed_link == "A News"


def test_same_page_across_result_types_is_one_source():
    results, sources = _normalize(sample_raw_response())
    gov = [s for s in sources if s.registrable_domain == "example.gov.in"]
    assert len(gov) == 1  # answer box + organic #1 point to the same page
    assert len(gov[0].result_ids) == 2
    assert gov[0].best_position == 1  # answer box position 0 does not count as ranking


def test_youtube_shorts_and_watch_same_video_are_one_source():
    _, sources = _normalize(sample_raw_response())
    yt = [s for s in sources if s.registrable_domain == "youtube.com"]
    assert len(yt) == 2  # nzgTSu7pNTw (2 URLs) and wtnafTFIJzI
    by_id = {s.canonical_url: s for s in yt}
    assert len(by_id["https://youtube.com/watch?v=nzgTSu7pNTw"].urls) == 2


def test_metrics_keep_counts_separate():
    results, sources = _normalize(sample_raw_response())
    m = compute_metrics(results, sources)
    assert m.total_search_results == 8  # 1 answer box + 6 organic + 1 related question
    assert m.results_by_type == {"answer_box": 1, "organic": 6, "related_question": 1}
    assert m.unique_urls == 7  # distinct raw links (one organic result has none)
    assert m.unique_sources == 5  # gov page, 2 videos, news page, blog page
    assert m.unique_domains == 4  # example.gov.in, youtube.com, example.co.in, example.com


def test_results_without_url_are_kept_but_not_sources():
    results, _ = _normalize(sample_raw_response())
    orphan = next(r for r in results if r.title == "No link result")
    assert orphan.source_id is None
    assert orphan.snippet == "orphan snippet"


def test_evidence_is_not_truncated():
    long_snippet = "Deadline 14 June 2026. " * 100
    raw = {"organic_results": [{"position": 1, "title": "t", "link": "https://a.com", "snippet": long_snippet}]}
    results, _ = _normalize(raw)
    assert results[0].snippet == long_snippet.strip()
    assert results[0].raw["snippet"] == long_snippet


def test_group_sources_merges_into_existing():
    raw = sample_raw_response()
    results1, sources1 = _normalize(raw)
    results2 = build_results("srch_2", _response(raw), parse_response(raw, "google"))
    merged = group_sources(results2, existing=sources1)
    assert len(merged) == len(sources1)
    assert all(r.source_id in {s.id for s in sources1} for r in results2 if r.canonical_url)
