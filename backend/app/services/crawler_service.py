"""Website crawling + parsing (identical code path for fixture and live crawler adapters).

Detecting a Google tag proves only that a tag is INSTALLED — never that a conversion fires.
"""
from __future__ import annotations

import re
from collections import deque
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from lxml import etree

from app.adapters import registry
from app.adapters.crawler import USER_AGENT
from app.core.config import get_settings

PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
GTAG_RE = re.compile(r"\b(G-[A-Z0-9]{4,}|AW-\d{6,}|UA-\d{4,}-\d+)\b")
GTM_RE = re.compile(r"\bGTM-[A-Z0-9]{4,}\b")
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".pdf", ".zip", ".css", ".js", ".webp", ".ico", ".mp4")


def parse_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(url).hostname
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    meta = soup.find("meta", attrs={"name": "description"})
    links_int, links_ext = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("tel:", "mailto:", "#", "javascript:")):
            continue
        absu = urljoin(url, href)
        (links_int if urlparse(absu).hostname == host else links_ext).append(absu.split("#")[0])
    menu = [a.get_text(strip=True) for nav in soup.find_all("nav") for a in nav.find_all("a")]
    text = soup.get_text(" ", strip=True)
    raw = html
    phones = sorted(set(PHONE_RE.findall(text)) | {a["href"][4:] for a in soup.find_all("a", href=True) if a["href"].startswith("tel:")})
    schema = []
    try:
        import extruct
        data = extruct.extract(html, base_url=url, syntaxes=["json-ld", "microdata"], uniform=True)
        schema = data.get("json-ld", []) + data.get("microdata", [])
    except Exception:  # noqa: BLE001 — malformed markup must not break the audit
        schema = []
    tags = sorted(set(GTAG_RE.findall(raw)))
    return {
        "url": url, "title": title, "meta_description": (meta.get("content") or "").strip() if meta else "",
        "h1": [h.get_text(strip=True) for h in soup.find_all("h1")],
        "h2": [h.get_text(strip=True) for h in soup.find_all("h2")],
        "h3": [h.get_text(strip=True) for h in soup.find_all("h3")],
        "menu": menu, "internal_links": sorted(set(links_int)), "external_links": sorted(set(links_ext))[:30],
        "forms": len(soup.find_all("form")), "phones": phones, "word_count": len(text.split()),
        "text_excerpt": text[:1500], "schema": schema,
        "google_tags": tags, "gtm_containers": sorted(set(GTM_RE.findall(raw))),
        "has_google_ads_tag": any(t.startswith("AW-") for t in tags),
        "has_viewport": bool(soup.find("meta", attrs={"name": "viewport"})),
        "tel_links": len([a for a in soup.find_all("a", href=True) if a["href"].startswith("tel:")]),
    }


def _robots(base: str):
    rp = RobotFileParser()
    res = registry.get("crawler").fetch(f"{base}/robots.txt")
    sitemaps = []
    if res.ok:
        lines = res.text.splitlines()
        rp.parse(lines)
        sitemaps = [ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sitemap:")]
        return rp, True, sitemaps, res.text
    rp.parse([])  # no robots.txt => allowed
    return rp, False, sitemaps, ""


def _sitemap_urls(urls: list[str]) -> list[str]:
    out = []
    for sm in urls[:3]:
        res = registry.get("crawler").fetch(sm)
        if not res.ok:
            continue
        try:
            root = etree.fromstring(res.text.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True))
            out += [el.text.strip() for el in root.iter("{*}loc") if el.text]
        except etree.XMLSyntaxError:
            continue
    return out


def crawl(start_url: str, *, max_pages: int | None = None, max_depth: int | None = None) -> dict:
    s = get_settings()
    max_pages = max_pages or s.CRAWLER_MAX_PAGES
    max_depth = s.CRAWLER_MAX_DEPTH if max_depth is None else max_depth
    p = urlparse(start_url)
    base = f"{p.scheme}://{p.hostname}"
    host = p.hostname
    adapter = registry.get("crawler")
    rp, robots_found, sitemap_refs, robots_text = _robots(base)
    sitemap = _sitemap_urls(sitemap_refs or [f"{base}/sitemap.xml"])
    queue = deque([(start_url, 0)] + [(u, 1) for u in sitemap if urlparse(u).hostname == host])
    seen, pages, errors, blocked = set(), [], [], []
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        url = url.split("#")[0]
        if url in seen or urlparse(url).hostname != host or url.lower().endswith(SKIP_EXT):
            continue
        seen.add(url)
        if not rp.can_fetch(USER_AGENT, url):
            blocked.append(url)
            continue
        res = adapter.fetch(url)
        if not res.ok:
            errors.append({"url": url, "status": res.status, "error": res.error})
            continue
        page = parse_page(res.text, res.final_url)
        if page["word_count"] < 60:  # likely a JS-rendered site: try Playwright (live mode only)
            rendered = adapter.render_js(url)
            if rendered and rendered.ok:
                page = parse_page(rendered.text, url)
                page["rendered_with"] = "playwright"
        page["depth"] = depth
        page["status"] = res.status
        pages.append(page)
        if depth < max_depth:
            for link in page["internal_links"]:
                if link not in seen:
                    queue.append((link, depth + 1))
    return {"start_url": start_url, "robots_found": robots_found, "robots_txt": robots_text[:2000],
            "sitemap_urls": sitemap[:200], "pages": pages, "errors": errors, "blocked_by_robots": blocked,
            "source": adapter.source, "adapter": adapter.name}


def check_url(url: str) -> dict:
    res = registry.get("crawler").fetch(url)
    return {"url": url, "status": res.status, "ok": res.ok, "error": res.error, "source": registry.get("crawler").source}
