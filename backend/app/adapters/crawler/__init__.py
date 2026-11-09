"""CrawlerAdapter: fetches raw HTTP responses. Parsing lives in services/crawler_service.py (shared by both modes).

FixtureCrawlerAdapter serves the fictional *.example sites from mock_world.fixtures, rendered to real HTML,
so the genuine BeautifulSoup/lxml/extruct parsing code is exercised without touching the internet.
LiveCrawlerAdapter uses httpx with an SSRF guard applied to every redirect hop.
"""
from __future__ import annotations

import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import get_settings

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
    def _bad(ip) -> bool:
        return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or str(ip) == "169.254.169.254")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if _bad(literal):
            raise SSRFBlocked(f"IP address not allowed: {literal}")
        return
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
    source = "LIVE"

    @abstractmethod
    def fetch(self, url: str) -> FetchResult: ...

    def render_js(self, url: str) -> FetchResult | None:
        return None


# ---------------------------------------------------------------- fixture mode




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
