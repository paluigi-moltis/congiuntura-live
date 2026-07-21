"""Tests for feed parsing (RSS and Atom formats)."""

from __future__ import annotations

import feedparser
import pytest

from congiuntura_live.feed_reader import FeedReader
from congiuntura_live.settings import FeedConfig


@pytest.fixture
def reader() -> FeedReader:
    return FeedReader()


class TestRssParsing:
    """Tests for RSS 2.0 feed parsing (Istat, INE, Destatis)."""

    def test_parse_istat_rss(self, reader: FeedReader, rss_sample_istat: str):
        feed = FeedConfig(
            label="National accounts", url="https://www.istat.it/tema/conti-nazionali/feed"
        )
        parsed = feedparser.parse(rss_sample_istat)
        entries = [reader._parse_entry(item, feed) for item in parsed.entries]
        entries = [e for e in entries if e is not None]

        assert len(entries) == 3
        assert entries[0].title == "Pil e indebitamento delle AP: dati preliminari"
        assert entries[0].url == "https://www.istat.it/communicato/pil-2025q1"
        assert entries[0].published is not None
        assert entries[0].published.year == 2025
        assert entries[0].published.month == 6
        assert entries[0].published.day == 30

    def test_parse_destatis_rss(self, reader: FeedReader, rss_sample_destatis: str):
        feed = FeedConfig(label="Press releases", url="https://www.destatis.de/Aktuelles.xml")
        parsed = feedparser.parse(rss_sample_destatis)
        entries = [reader._parse_entry(item, feed) for item in parsed.entries]
        entries = [e for e in entries if e is not None]

        assert len(entries) == 2
        assert "Inflationsrate" in entries[0].title
        assert entries[0].url.startswith("https://www.destatis.de/DE/Press/")
        assert entries[0].published is not None
        assert entries[0].published.year == 2025

    def test_summary_extraction(self, reader: FeedReader, rss_sample_istat: str):
        feed = FeedConfig(label="Test", url="https://example.com")
        parsed = feedparser.parse(rss_sample_istat)
        entry = reader._parse_entry(parsed.entries[0], feed)
        assert entry is not None
        assert "PIL" in entry.summary


class TestAtomParsing:
    """Tests for Atom feed parsing (Eurostat)."""

    def test_parse_eurostat_atom(self, reader: FeedReader, atom_sample_eurostat: str):
        feed = FeedConfig(label="News releases", url="https://ec.europa.eu/eurostat")
        parsed = feedparser.parse(atom_sample_eurostat)
        entries = [reader._parse_entry(item, feed) for item in parsed.entries]
        entries = [e for e in entries if e is not None]

        assert len(entries) == 2
        assert entries[0].title == "Flash estimate: HICP +2.1% in June 2025"
        # Atom uses <link href="...">
        assert (
            entries[0].url
            == "https://ec.europa.eu/eurostat/web/products-news-releases/-/dd-20250630-1"
        )
        assert entries[0].published is not None
        assert entries[0].published.year == 2025
        assert entries[0].published.month == 6

    def test_atom_summary(self, reader: FeedReader, atom_sample_eurostat: str):
        feed = FeedConfig(label="News releases", url="https://example.com")
        parsed = feedparser.parse(atom_sample_eurostat)
        entry = reader._parse_entry(parsed.entries[0], feed)
        assert entry is not None
        assert "HICP" in entry.summary


class TestEdgeCases:
    """Tests for malformed or edge-case entries."""

    def test_entry_without_url_skipped(self, reader: FeedReader):
        feed = FeedConfig(label="Test", url="https://example.com")
        malformed = """<?xml version="1.0"?>
<rss version="2.0"><channel>
    <item><title>No URL here</title><description>Missing link</description></item>
</channel></rss>"""
        parsed = feedparser.parse(malformed)
        entry = reader._parse_entry(parsed.entries[0], feed)
        assert entry is None

    def test_entry_without_title_gets_placeholder(self, reader: FeedReader):
        feed = FeedConfig(label="Test", url="https://example.com")
        malformed = """<?xml version="1.0"?>
<rss version="2.0"><channel>
    <item><link>https://example.com/no-title</link></item>
</channel></rss>"""
        parsed = feedparser.parse(malformed)
        entry = reader._parse_entry(parsed.entries[0], feed)
        assert entry is not None
        assert entry.title == "(untitled)"

    def test_text_cleaning(self, reader: FeedReader):
        """Whitespace and HTML entities should be normalized."""
        feed = FeedConfig(label="Test", url="https://example.com")
        messy = """<?xml version="1.0"?>
<rss version="2.0"><channel>
    <item>
        <title>  Multiple   spaces  </title>
        <link>https://example.com/clean</link>
        <description><![CDATA[  Line one.\n\n  Line two.  ]]></description>
    </item>
</channel></rss>"""
        parsed = feedparser.parse(messy)
        entry = reader._parse_entry(parsed.entries[0], feed)
        assert entry is not None
        assert "  " not in entry.title  # collapsed
        assert entry.summary == "Line one. Line two."
