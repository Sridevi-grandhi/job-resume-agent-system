"""Bounded public transport shared by API, HTML and optional browser collection.

Robots/access failures stop collection. No credentials, retries or unrestricted
redirects. Responses are cached only for this company/refresh.
"""
import json
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

from app.services import url_safety
from app.services.job_normalization_service import is_safe_application_url

USER_AGENT = "CareerBoostAI-JobDiscovery"
MAX_REQUESTS = 30
MAX_BYTES = 2 * 1024 * 1024
BLOCKED_TEXT = ("verify you are human", "access denied", "cf-chl-", "captcha challenge", "sign in to continue")


class CollectionError(ValueError):
    """Errors are fixed diagnostic codes, not arbitrary provider response text."""


@dataclass
class Document:
    url: str
    status: int
    text: str
    content_type: str

    def json(self):
        return json.loads(self.text)


def public_https(url):
    if not isinstance(url, str) or urlsplit(url).scheme != "https" or not is_safe_application_url(url):
        raise CollectionError("unsafe_url")
    if urlsplit(url).port not in (None, 443):
        raise CollectionError("unsafe_port")
    url_safety.assert_public_http_url(url)


class PublicClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.cache = {}
        self.robots = {}
        self.count = 0
        self.last_request = 0.0
        self.started = time.monotonic()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    def _request(self, url, *, method="GET", payload=None, delay=0.25):
        public_https(url)
        if self.count >= MAX_REQUESTS or time.monotonic() - self.started > 60:
            raise CollectionError("collection_budget_exhausted")
        if delay > 10:
            raise CollectionError("crawl_delay_exceeds_budget")
        time.sleep(max(0, self.last_request + delay - time.monotonic()))
        self.count += 1
        self.last_request = time.monotonic()
        # POST is used only for Workday's public read/search endpoint.
        if method != "GET" and not (method == "POST" and "/wday/cxs/" in urlsplit(url).path and urlsplit(url).path.endswith("/jobs")):
            raise CollectionError("method_not_allowed")
        self.session.cookies.clear()
        with self.session.request(method, url, json=payload, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"},
                                  timeout=(5, 12), allow_redirects=False, stream=True) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get("Location", ""))
                public_https(target)
                return Document(target, response.status_code, "", "")
            if response.status_code in (401, 403, 429):
                raise CollectionError("access_restricted")
            chunks, size = [], 0
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise CollectionError("response_too_large")
                chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            if any(marker in text.lower() for marker in BLOCKED_TEXT):
                raise CollectionError("access_challenge")
            return Document(url, response.status_code, text, response.headers.get("Content-Type", ""))

    def allowed(self, url):
        public_https(url)
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            doc = self._request(origin + "/robots.txt")
            if doc.status == 404:
                self.robots[origin] = None
            elif doc.status == 200 and "<html" not in doc.text.lower():
                rules = RobotFileParser()
                rules.parse(doc.text.splitlines())
                self.robots[origin] = rules
            else:
                raise CollectionError("robots_unavailable")
        rules = self.robots[origin]
        if rules and not rules.can_fetch(USER_AGENT, url):
            raise CollectionError("robots_disallowed")
        return max(0.25, (rules.crawl_delay(USER_AGENT) or 0) if rules else 0)

    def fetch(self, url, *, method="GET", payload=None):
        key = (url, method, json.dumps(payload, sort_keys=True))
        if key in self.cache:
            return self.cache[key]
        current = url
        for _ in range(4):
            doc = self._request(current, method=method, payload=payload, delay=self.allowed(current))
            if doc.status in (301, 302, 303, 307, 308):
                if method != "GET":
                    raise CollectionError("api_redirect")
                current = doc.url
                continue
            if doc.status != 200:
                raise CollectionError(f"http_{doc.status}")
            self.cache[key] = doc
            return doc
        raise CollectionError("redirect_limit")
