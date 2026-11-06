"""CrawlerAdapter: fetches raw HTTP responses. Parsing lives in services/crawler_service.py (shared by both modes).

FixtureCrawlerAdapter serves the fictional *.example sites from mock_world.fixtures, rendered to real HTML,
so the genuine BeautifulSoup/lxml/extruct parsing code is exercised without touching the internet.
LiveCrawlerAdapter uses httpx with an SSRF guard applied to every redirect hop.
"""
from __future__ import annotations

import html
import ipaddress
import json
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import get_settings
from app.mock_world.failures import check_failure

MAX_BYTES = 2_000_000
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
USER_AGENT = "GoogleAdsAIAgentAuditBot/1.0 (+site audit; respects robots.txt)"


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    text: str = ""
    content_type: str = "text/html"
    error: str = ""
    elapsed_ms: int = 0
    redirects: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.error


class SSRFBlocked(Exception):
    pass


def check_url_safe(url: str, resolver=socket.getaddrinfo) -> None:
    """Block non-http(s) schemes, odd ports, credentials in URL and any private/loopback/link-local/metadata IP."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SSRFBlocked(f"Scheme not allowed: {p.scheme}")
    if not p.hostname:
        raise SSRFBlocked("URL has no host")
    if p.username or p.password:
        raise SSRFBlocked("Credentials in URL are not allowed")
    if p.port not in ALLOWED_PORTS:
        raise SSRFBlocked(f"Port not allowed: {p.port}")
    host = p.hostname.lower()
    if host in ("localhost", "metadata.google.internal") or host.endswith((".internal", ".local", ".localhost")):
        raise SSRFBlocked(f"Host not allowed: {host}")
    try:
        infos = resolver(host, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise SSRFBlocked(f"DNS resolution failed for {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or str(ip) == "169.254.169.254"):
            raise SSRFBlocked(f"Host {host} resolves to a non-public address ({ip})")


class CrawlerAdapter(ABC):
    name = "crawler"
    source = "MOCK"

    @abstractmethod
    def fetch(self, url: str) -> FetchResult: ...

    def render_js(self, url: str) -> FetchResult | None:
        return None


# ---------------------------------------------------------------- fixture mode
def render_page(site: dict, path: str, base: str) -> str:
    pg = site["pages"][path]
    nav = "".join(f'<li><a href="{p}">{html.escape(label)}</a></li>' for label, p in site["menu"])
    parts = [f"<!doctype html><html lang='en'><head><title>{html.escape(pg['title'])}</title>",
             f'<meta name="description" content="{html.escape(pg["meta"])}">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">', *site["scripts"]]
    if pg.get("schema"):
        parts.append(f'<script type="application/ld+json">{json.dumps(pg["schema"])}</script>')
    parts.append(f"</head><body><header><nav><ul>{nav}</ul></nav>")
    for ph in pg.get("phones", []):
        digits = "".join(c for c in ph if c.isdigit())
        parts.append(f'<a class="cta-call" href="tel:+1{digits}">Call {html.escape(ph)}</a>')
    parts.append(f"</header><main><h1>{html.escape(pg['h1'])}</h1>")
    if pg.get("form"):
        parts.append('<form action="/contact" method="post" class="lead-form"><input name="name"><input name="phone">'
                     '<button type="submit">Request a quote</button></form>')
    for h in pg.get("h2", []):
        parts.append(f"<h2>{html.escape(h)}</h2>")
    parts.append(f"<p>{html.escape(pg['text'])}</p>")
    for h in pg.get("h3", []):
        parts.append(f"<h3>{html.escape(h)}</h3><p>Answer: yes, ask our team for details.</p>")
    parts.append('<footer><a href="/privacy">Privacy</a> <a href="https://www.facebook.com/example">Facebook</a></footer>')
    parts.append("</main></body></html>")
    return "".join(parts)


class FixtureCrawlerAdapter(CrawlerAdapter):
    name = "FixtureCrawlerAdapter"
    source = "MOCK"

    def fetch(self, url: str) -> FetchResult:
        from app.mock_world.fixtures import SITES
        check_failure("crawler", "fetch", url)
        p = urlparse(url)
        host = (p.hostname or "").lower()
        path = p.path or "/"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        site = SITES.get(host)
        base = f"{p.scheme}://{host}"
        if not site:
            return FetchResult(url, url, 0, error=f"Fixture mode: no fixture for host {host} (live crawling disabled)")
        if path in site.get("status", {}):
            code = site["status"][path]
            return FetchResult(url, url, code, text="<html><body>Service Unavailable</body></html>")
        if path == "/robots.txt":
            return FetchResult(url, url, 200, text=site["robots"].format(base=base), content_type="text/plain")
        if path == "/sitemap.xml":
            locs = "".join(f"<url><loc>{base}{pth}</loc></url>" for pth in site["pages"] if pth != "/thank-you")
            return FetchResult(url, url, 200, text=f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>',
                               content_type="application/xml")
        if path not in site["pages"]:
            return FetchResult(url, url, 404, text="<html><body><h1>Not found</h1></body></html>")
        return FetchResult(url, url, 200, text=render_page(site, path, base))


# ---------------------------------------------------------------- live mode
class LiveCrawlerAdapter(CrawlerAdapter):
    name = "LiveCrawlerAdapter"
    source = "LIVE"

    def __init__(self, transport: httpx.BaseTransport | None = None, resolver=socket.getaddrinfo):
        s = get_settings()
        self.timeout = s.CRAWLER_TIMEOUT_SECONDS
        self.resolver = resolver
        self.client = httpx.Client(timeout=self.timeout, follow_redirects=False, transport=transport,
                                   headers={"User-Agent": USER_AGENT})

    def fetch(self, url: str) -> FetchResult:
        redirects: list[str] = []
        current = url
        for _ in range(5):
            try:
                check_url_safe(current, self.resolver)
            except SSRFBlocked as exc:
                return FetchResult(url, current, 0, error=f"Blocked: {exc}", redirects=redirects)
            try:
                with self.client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        nxt = urljoin(current, resp.headers.get("location", ""))
                        redirects.append(nxt)
                        current = nxt
                        continue
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            break
                        chunks.append(chunk)
                    body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                    return FetchResult(url, current, resp.status_code, text=body,
                                       content_type=resp.headers.get("content-type", ""),
                                       elapsed_ms=int(resp.elapsed.total_seconds() * 1000), redirects=redirects)
            except httpx.TimeoutException:
                return FetchResult(url, current, 0, error="Timeout", redirects=redirects)
            except httpx.HTTPError as exc:
                return FetchResult(url, current, 0, error=f"{type(exc).__name__}", redirects=redirects)
        return FetchResult(url, current, 0, error="Too many redirects", redirects=redirects)

    def render_js(self, url: str) -> FetchResult | None:
        """Playwright rendering for JavaScript-heavy pages (optional dependency)."""
        try:
            check_url_safe(url, self.resolver)
            from playwright.sync_api import sync_playwright  # optional
        except (SSRFBlocked, ImportError):
            return None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(user_agent=USER_AGENT)
                page.route("**/*", lambda route: route.continue_() if _route_ok(route.request.url, self.resolver) else route.abort())
                resp = page.goto(url, timeout=int(self.timeout * 1000), wait_until="networkidle")
                content = page.content()
                browser.close()
                return FetchResult(url, url, resp.status if resp else 0, text=content)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(url, url, 0, error=f"Playwright: {type(exc).__name__}")


def _route_ok(url: str, resolver) -> bool:
    try:
        check_url_safe(url, resolver)
        return True
    except SSRFBlocked:
        return False
