"""Optional browser fallback. Every browser request uses the guarded transport."""
from urllib.parse import urlsplit

from .http import BLOCKED_TEXT, CollectionError, MAX_BYTES


def render_page(client, url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise CollectionError("playwright_not_installed") from error
    client.allowed(url)
    origin = urlsplit(url).netloc
    # Serial collection limits browser concurrency to one. Route fulfillment
    # prevents Chromium from making unchecked DNS/subresource/redirect calls.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(service_workers="block", accept_downloads=False)
            context.set_default_timeout(8000)
            context.route_web_socket("**/*", lambda connection: connection.close())

            def guarded(route):
                request = route.request
                try:
                    if urlsplit(request.url).netloc != origin or request.resource_type not in {"document", "script", "xhr", "fetch"}:
                        return route.abort()
                    if request.method != "GET":
                        return route.abort()
                    document = client.fetch(request.url)
                    route.fulfill(status=document.status, content_type=document.content_type or "text/html", body=document.text)
                except Exception:
                    route.abort()

            context.route("**/*", guarded)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=12000)
            page.locator('script[type="application/ld+json"], [itemtype$="/JobPosting"], .job-card, .job-listing, a[href*="/job/"]').first.wait_for(state="attached", timeout=5000)
            html = page.content()
            if len(html.encode()) > MAX_BYTES:
                raise CollectionError("rendered_page_too_large")
            if any(marker in html.lower() for marker in BLOCKED_TEXT):
                raise CollectionError("access_challenge")
            return html
        finally:
            browser.close()
