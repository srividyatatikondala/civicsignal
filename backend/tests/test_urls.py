import pytest

from civicsignal.normalize import canonical_url, host_of, registrable_domain


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://www.example.com/page/", "http://example.com/page"),
        ("https://example.com/page?utm_source=x&utm_medium=y", "https://example.com/page"),
        ("https://example.com/page#:~:text=deadline", "https://example.com/page"),
        ("https://example.com/p?b=2&a=1", "https://example.com/p?a=1&b=2"),
        ("https://EXAMPLE.com:443/page", "https://example.com/page"),
        ("https://www.youtube.com/shorts/nzgTSu7pNTw", "https://youtu.be/nzgTSu7pNTw"),
        ("https://m.youtube.com/watch?v=nzgTSu7pNTw&feature=share", "https://youtube.com/watch?v=nzgTSu7pNTw"),
        ("https://example.com/news/story/amp", "https://example.com/news/story"),
    ],
)
def test_same_page_same_canonical(a, b):
    assert canonical_url(a) == canonical_url(b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://www.youtube.com/watch?v=wtnafTFIJzI", "https://www.youtube.com/shorts/nzgTSu7pNTw"),
        ("https://example.com/a", "https://example.com/b"),
        ("https://example.com/p?id=1", "https://example.com/p?id=2"),
    ],
)
def test_different_pages_differ(a, b):
    assert canonical_url(a) != canonical_url(b)


@pytest.mark.parametrize("bad", [None, "", "not a url", "ftp://example.com/x", "javascript:alert(1)", "http://[bad"])
def test_unusable_urls(bad):
    assert canonical_url(bad) is None


@pytest.mark.parametrize(
    "host, expected",
    [
        ("uidai.gov.in", "uidai.gov.in"),
        ("myaadhaar.uidai.gov.in", "uidai.gov.in"),
        ("pmkisan.gov.in", "pmkisan.gov.in"),
        ("india.nic.in", "india.nic.in"),
        ("news.example.co.in", "example.co.in"),
        ("zeenews.india.com", "india.com"),
        ("youtube.com", "youtube.com"),
        ("127.0.0.1", "127.0.0.1"),
        (None, None),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_host_strips_www_and_mobile():
    assert host_of("https://www.etnownews.com/x") == "etnownews.com"
    assert host_of("https://m.youtube.com/watch?v=x") == "youtube.com"
    assert host_of("https://www.gov.in/") == "gov.in"  # never strip down to a bare suffix label
