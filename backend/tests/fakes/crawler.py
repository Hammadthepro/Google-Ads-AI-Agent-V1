"""TEST FIXTURE ONLY — never imported by the application."""
from __future__ import annotations

import html
import json
from urllib.parse import urlparse

from app.adapters.crawler import CrawlerAdapter, FetchResult
from tests.fakes.mock_world.failures import check_failure


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
        from tests.fakes.mock_world.fixtures import SITES
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
