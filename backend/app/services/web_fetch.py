"""Web Fetch: open ONE known URL and extract its readable content.

It does not discover pages (that is Web Search) and does not follow links (that is the Website Crawler). It uses the same SSRF-guarded
fetcher as the crawler: private and reserved addresses are refused before any request, every redirect hop is re-checked, responses are
size-limited and time-limited. HTTP errors, timeouts and non-HTML content are returned as failures with their reason, never as content."""
from __future__ import annotations

from app.adapters import registry
from app.core import clock
from app.services import research_events

HTML = ("text/html", "application/xhtml+xml")


def fetch(url: str, *, stage: str = "", purpose: str = "") -> dict:
    from app.services.crawler_service import parse_page
    research_events.emit("url_crawling", url, stage=stage, url=url, purpose=purpose or None)
    try:
        res = registry.get("crawler").fetch(url)
    except Exception as exc:  # noqa: BLE001 - SSRF refusals and transport errors are reported, not raised
        out = {"url": url, "ok": False, "status": None, "error": f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:200]}"}
        research_events.emit("page_failed", url, stage=stage, level="warn", url=url, error=out["error"])
        return out
    base = {"url": url, "final_url": res.final_url, "status": res.status, "redirects": list(res.redirects or []),
            "content_type": res.content_type, "fetched_at": clock.now().isoformat(), "elapsed_ms": res.elapsed_ms}
    if not res.ok:
        out = {**base, "ok": False, "error": res.error or f"HTTP {res.status}"}
        research_events.emit("page_failed", url, stage=stage, level="warn", url=url, status=res.status, error=out["error"][:200])
        return out
    if not any(t in (res.content_type or "").lower() for t in HTML):
        out = {**base, "ok": False, "error": f"Unsupported content type: {res.content_type or 'unknown'}"}
        research_events.emit("page_failed", url, stage=stage, level="warn", url=url, error=out["error"])
        return out
    page = parse_page(res.text, res.final_url)
    out = {**base, "ok": True, "error": "", "title": page.get("title", ""), "meta_description": page.get("meta_description", ""),
           "h1": (page.get("h1") or [""])[0], "headings": (page.get("h2") or [])[:10], "word_count": page.get("word_count", 0),
           "text_excerpt": (page.get("text_excerpt") or "")[:2000]}
    research_events.emit("page_extracted", out["title"] or url, stage=stage, url=url, page_title=out["title"][:160], h1=out["h1"][:160],
                         headings=[h[:80] for h in out["headings"][:6]], words=out["word_count"])
    return out
