"""Conservative structured JobPosting/job-card extraction from public HTML."""
import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.services.job_normalization_service import build_normalized_job, strip_html, is_safe_application_url
from .base import Collection, date, expired
from .http import CollectionError


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def text(value):
    if isinstance(value, list):
        return ", ".join(text(item) for item in value)
    if isinstance(value, dict):
        return text(value.get("name") or value.get("value") or "")
    return strip_html(str(value or ""))


def _location(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (_location(item) for item in value)))
    if not isinstance(value, dict):
        return text(value)
    address = value.get("address") or value
    if not isinstance(address, dict):
        return text(address)
    return ", ".join(text(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k))


def parse_jobposting(node, source, page_url):
    if expired(node.get("validThrough")):
        return None
    # JobPosting company identity must not contradict the configured employer.
    company = text(node.get("hiringOrganization"))
    if company:
        from app.services.job_enrichment_service import _company_names_match
        if not _company_names_match(source.company_name, company):
            return None
    url = node.get("url") or page_url
    if not isinstance(url, str):
        return None
    url = urljoin(page_url, url)
    apply = node.get("applicationUrl") or node.get("applyUrl") or url
    if not isinstance(apply, str):
        apply = url
    normalized = build_normalized_job(
        source="company_direct", source_mode="live", source_job_id=text(node.get("identifier")) or url,
        title=text(node.get("title")), company=source.company_name,
        location=_location(node.get("jobLocation") or node.get("applicantLocationRequirements")),
        work_mode="Remote" if str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE" else text(node.get("workplaceType")),
        job_type=text(node.get("employmentType")), description=text(node.get("description")),
        requirements=text(node.get("qualifications")), skills=text(node.get("skills")),
        posted_at=date(node.get("datePosted")), source_url=url, application_url=urljoin(page_url, apply),
    )
    if normalized:
        salary = node.get("baseSalary") or {}
        value = salary.get("value") or {} if isinstance(salary, dict) else {}
        normalized.update(preferred_skills=text(node.get("preferredSkills")), department=text(node.get("occupationalCategory")),
                          salary_currency=salary.get("currency") if isinstance(salary, dict) else None,
                          salary_min=value.get("minValue", value.get("value")) if isinstance(value, dict) else value,
                          salary_max=value.get("maxValue", value.get("value")) if isinstance(value, dict) else value,
                          salary_interval=value.get("unitText") if isinstance(value, dict) else None)
    return normalized


def parse_html(html, source, page_url):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        for node in nodes(payload):
            kind = node.get("@type")
            if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                try:
                    job = parse_jobposting(node, source, page_url)
                except (TypeError, ValueError):
                    job = None
                if job:
                    jobs.append(job)
    # Explicit job containers only; navigation/footer text is never a JD.
    containers = soup.select('[itemtype$="/JobPosting"], .job-card, .job-listing, [data-job-id]')
    if not containers and soup.select_one(".job-description, [data-job-description], [itemprop=description]"):
        containers = [soup]
    for card in containers:
        title = card.select_one('[itemprop=title], .job-title, [data-job-title], h1, h2, h3')
        description = card.select_one('[itemprop=description], .job-description, [data-job-description]')
        if not title or not description:
            continue
        link = card.select_one('a.job-title, .job-title a, a[itemprop=url], h2 a, h3 a')
        apply = card.select_one('a.apply-button, a[data-apply-url], a[itemprop=applicationUrl]')
        def field(selector):
            element = card.select_one(selector)
            return element.get_text(" ", strip=True) if element else ""
        posted = card.select_one('time[datetime], [itemprop=datePosted][content]')
        salary = card.select_one('[itemprop=baseSalary], .salary')
        job = parse_jobposting({
            "title": title.get_text(" ", strip=True), "description": str(description),
            "identifier": card.get("data-job-id"), "url": link.get("href") if link else page_url,
            "applyUrl": apply.get("href") if apply else None,
            "jobLocation": field('[itemprop=jobLocation], .job-location'),
            "employmentType": field('[itemprop=employmentType], .employment-type'),
            "workplaceType": field('.workplace-type, .work-mode'),
            "skills": field('[itemprop=skills], .required-skills'), "preferredSkills": field('.preferred-skills'),
            "datePosted": (posted.get("datetime") or posted.get("content")) if posted else None,
        }, source, page_url)
        if job:
            # Preserve unstructured public salary text without guessing a
            # range/currency/period from ambiguous formatting.
            if salary:
                job["compensation"] = salary.get_text(" ", strip=True)
            jobs.append(job)
    unique = {job["source_url"]: job for job in jobs}
    links = []
    for anchor in soup.select('a[href]'):
        try:
            url = urljoin(page_url, anchor.get("href"))
        except ValueError:
            continue
        if urlsplit(url).hostname != urlsplit(page_url).hostname or not is_safe_application_url(url):
            continue
        if re.search(r"/(?:jobs?|careers?|positions?|openings?)/[^/?#]+", urlsplit(url).path, re.I) and url not in unique and url not in links:
            links.append(url)
    return list(unique.values()), links


def inventory_count(html):
    """Only an explicit structured total can make HTML an authoritative list."""
    counts = []
    for script in BeautifulSoup(html, "html.parser").select('script[type="application/ld+json"]'):
        try:
            for node in nodes(json.loads(script.string or script.get_text())):
                if node.get("@type") == "ItemList" and type(node.get("numberOfItems")) is int:
                    counts.append(node["numberOfItems"])
        except (TypeError, ValueError):
            continue
    return counts[0] if len(counts) == 1 and counts[0] >= 0 else None


def collect(client, source, detection, limit, *, initial=None, render=False):
    document = initial or client.fetch(detection.url)
    html = document.text
    jobs, links = parse_html(html, source, document.url)
    if not jobs and not links and render:
        from .playwright_fallback import render_page
        html = render_page(client, document.url)
        jobs, links = parse_html(html, source, document.url)
    errors = 0
    for url in links[:max(0, limit - len(jobs))]:
        try:
            detail = client.fetch(url)
            items, _ = parse_html(detail.text, source, detail.url)
            if not items and render:
                from .playwright_fallback import render_page
                items, _ = parse_html(render_page(client, detail.url), source, detail.url)
            jobs.extend(items)
        except Exception:
            errors += 1
    if not jobs and errors:
        raise CollectionError("job_details_unavailable")
    jobs = list({job["source_url"]: job for job in jobs}.values())
    total = inventory_count(html)
    # An empty HTTP 200 alone can be a changed layout, not a closed inventory.
    complete = total is not None and total == len(jobs) <= limit and not errors
    return Collection(jobs[:limit], complete)
