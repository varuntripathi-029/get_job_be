"""RSS feed auto-discovery from a blog page's <link rel="alternate">."""

import pytest

from app.crawler.fetchers.rss import discover_feed_url

BASE = "https://www.keka.com/blog"


class TestDiscoverFeedUrl:
    def test_finds_an_rss_link_at_an_unguessable_path(self) -> None:
        html = (
            '<head><link rel="alternate" type="application/rss+xml" '
            'title="Keka" href="/hubfs/rss"></head>'
        )
        assert discover_feed_url(html, BASE) == "https://www.keka.com/hubfs/rss"

    def test_finds_an_atom_link(self) -> None:
        html = '<link rel="alternate" type="application/atom+xml" href="/atom.xml">'
        assert discover_feed_url(html, BASE) == "https://www.keka.com/atom.xml"

    def test_attribute_order_does_not_matter(self) -> None:
        html = '<link type="application/rss+xml" href="/feed" rel="alternate">'
        assert discover_feed_url(html, BASE) == "https://www.keka.com/feed"

    def test_absolute_href_is_kept(self) -> None:
        html = (
            '<link rel="alternate" type="application/rss+xml" '
            'href="https://feeds.example.com/blog.xml">'
        )
        assert discover_feed_url(html, BASE) == "https://feeds.example.com/blog.xml"

    def test_alternate_without_a_feed_type_is_ignored(self) -> None:
        # hreflang alternates are also rel="alternate" but are not feeds.
        html = '<link rel="alternate" hreflang="fr" href="/fr/blog">'
        assert discover_feed_url(html, BASE) is None

    def test_no_link_returns_none(self) -> None:
        html = "<head><title>Just a blog, no feed</title></head>"
        assert discover_feed_url(html, BASE) is None

    @pytest.mark.parametrize("html", ["", None])
    def test_empty_input_is_safe(self, html: str | None) -> None:
        assert discover_feed_url(html, BASE) is None  # type: ignore[arg-type]
