"""Generic collectors for approved public ingestion sources."""

from commerce_agent.ingestion.collectors.api import ApiCollector
from commerce_agent.ingestion.collectors.base import (
    BrowserPort,
    BrowserRequest,
    Collector,
    CollectorError,
    HttpPort,
    RenderedPage,
)
from commerce_agent.ingestion.collectors.browser import BrowserCollector, PlaywrightBrowserPort
from commerce_agent.ingestion.collectors.feed import FeedCollector
from commerce_agent.ingestion.collectors.firecrawl import (
    FirecrawlFirstCollector,
    wrap_collectors_with_firecrawl,
)
from commerce_agent.ingestion.collectors.html import HtmlCollector
from commerce_agent.ingestion.collectors.sitemap import SitemapCollector

__all__ = [
    "ApiCollector",
    "BrowserCollector",
    "BrowserPort",
    "BrowserRequest",
    "Collector",
    "CollectorError",
    "FeedCollector",
    "FirecrawlFirstCollector",
    "HtmlCollector",
    "HttpPort",
    "PlaywrightBrowserPort",
    "RenderedPage",
    "SitemapCollector",
    "wrap_collectors_with_firecrawl",
]
