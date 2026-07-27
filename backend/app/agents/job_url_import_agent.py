"""Safe, preview-only extraction for one user-selected job URL."""

import ipaddress
import json
import logging
import re
import socket
from html import unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import requests
from lxml import etree, html as lxml_html


logger = logging.getLogger(__name__)

KNOWN_HOSTS = {
    "linkedin.com": "LinkedIn",
    "www.linkedin.com": "LinkedIn",
    "dice.com": "Dice",
    "www.dice.com": "Dice",
    "ziprecruiter.com": "ZipRecruiter",
    "www.ziprecruiter.com": "ZipRecruiter",
    "remotive.com": "Remotive",
    "www.remotive.com": "Remotive",
}
SOURCE_VALUES = {
    "LinkedIn": "LinkedIn Imported",
    "Dice": "Dice Imported",
    "Indeed": "Indeed Imported",
    "ZipRecruiter": "ZipRecruiter Imported",
    "Remotive": "Remotive Imported",
    "Google Jobs": "Google Jobs Imported",
    "Employer Site": "Employer Site Imported",
}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 4
TIMEOUT = (5, 12)
USER_AGENT = "CareerBoostAI-JobPreview/1.1 (+public metadata only)"

REQUIRED_FIELDS = ("title", "company", "description")
PREVIEW_FIELDS = (
    "title", "company", "location", "work_mode",
    "job_type", "description", "skills_required",
)
BLOCKED_MARKERS = (
    "captcha", "verify you are human", "access denied", "checking your browser",
    "sign in to continue", "login to continue", "unusual traffic",
    "security check", "temporarily blocked", "enable javascript and cookies",
)
INVALID_MARKERS = (
    "job not found", "position has been filled", "position is no longer available",
    "job has expired", "page not found", "404 not found",
)
BOILERPLATE_LINES = (
    "see this and similar jobs on linkedin", "sign in", "join now",
    "show more", "show less", "apply now", "cookie policy",
    "privacy policy", "related jobs", "similar jobs", "people also viewed",
)
SKILL_VOCABULARY = (
    "Java", "Python", "C++", "C#", "JavaScript", "TypeScript", "React",
    "Angular", "Vue.js", "Node.js", "Spring Boot", "Spring", "Hibernate",
    "REST APIs", "GraphQL", "Microservices", "SQL", "PostgreSQL", "MySQL",
    "MongoDB", "Redis", "Apache Spark", "Hadoop", "Kafka", "AWS",
    "Amazon Web Services", "Azure", "Google Cloud", "Docker", "Kubernetes",
    "Jenkins", "Git", "Maven", "Gradle", "JUnit", "Selenium", "Cypress",
    "Test Automation", "Machine Learning", "Data Engineering", "CI/CD",
    "Terraform", "Ansible", "Linux", "Snowflake", "Databricks", "Tableau",
    "Power BI", "ETL", "Airflow",
)


class JobUrlImportError(ValueError):
    pass


def _source_for_hostname(hostname: str):
    hostname = hostname.lower().rstrip(".")
    if hostname in KNOWN_HOSTS:
        return KNOWN_HOSTS[hostname]
    if hostname == "indeed.com" or hostname.endswith(".indeed.com") or re.match(
        r"^(www\.)?indeed\.[a-z.]{2,}$", hostname
    ):
        return "Indeed"
    if (
        hostname == "google.com"
        or hostname.endswith(".google.com")
        or re.match(r"^(www\.)?google\.[a-z.]{2,}$", hostname)
    ):
        return "Google Jobs"
    return "Employer Site"


def _public_ip_addresses(hostname: str):
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise JobUrlImportError("The job provider hostname could not be resolved.") from error
    addresses = {record[4][0].split("%")[0] for record in records}
    if not addresses:
        raise JobUrlImportError("The job provider hostname could not be resolved.")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise JobUrlImportError("Private or internal network addresses are not allowed.")
    return addresses


def _validate_http_url(url: str):
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError as error:
        raise JobUrlImportError("Invalid job URL.") from error
    if parsed.scheme.lower() not in ("http", "https"):
        raise JobUrlImportError("Only HTTP and HTTPS job URLs are allowed.")
    if parsed.username or parsed.password:
        raise JobUrlImportError("Job URLs containing credentials are not allowed.")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise JobUrlImportError("The job URL must include a hostname.")
    _public_ip_addresses(hostname)
    return parsed, hostname


def detect_source(job_url: str):
    _, hostname = _validate_http_url(job_url)
    return _source_for_hostname(hostname)


def _generic_normalized_url(parsed):
    hostname = parsed.hostname.lower().rstrip(".")
    port = f":{parsed.port}" if parsed.port else ""
    path = re.sub(r"/+", "/", parsed.path or "/")
    return urlunsplit((parsed.scheme.lower(), hostname + port, path, parsed.query, ""))


def normalize_job_url(job_url: str):
    parsed, hostname = _validate_http_url(job_url)
    source = _source_for_hostname(hostname)
    path = re.sub(r"/+", "/", parsed.path or "/")
    query = parse_qs(parsed.query)

    if source == "LinkedIn":
        match = re.search(r"/jobs/view/(\d+)", path)
        job_id = match.group(1) if match else (query.get("currentJobId") or [None])[0]
        if not job_id or not str(job_id).isdigit():
            raise JobUrlImportError("Select one job first, then copy the URL containing currentJobId.")
        return source, f"https://www.linkedin.com/jobs/view/{job_id}/", str(job_id)

    if source == "Indeed":
        job_id = (query.get("jk") or [None])[0]
        if path.rstrip("/").lower() != "/viewjob" or not job_id:
            raise JobUrlImportError("Select one Indeed job and copy its direct viewjob URL.")
        host = "www.indeed.com" if hostname == "indeed.com" else hostname
        return source, f"https://{host}/viewjob?{urlencode({'jk': job_id})}", str(job_id)

    if source == "Dice":
        match = re.match(r"^/(?:job-detail|jobs/detail|job)/([^/?#]+)", path, re.IGNORECASE)
        if not match:
            raise JobUrlImportError("Select one Dice job and copy its direct job-detail URL.")
        normalized_path = path.rstrip("/")
        return source, f"https://www.dice.com{normalized_path}", match.group(1)

    if source == "ZipRecruiter":
        invalid = path.rstrip("/").lower() in ("/jobs", "/jobs/search", "/jobs/search-results")
        if invalid or not re.match(r"^/jobs/[^/]+", path, re.IGNORECASE):
            raise JobUrlImportError("Select one ZipRecruiter job and copy its direct job URL.")
        normalized_path = path.rstrip("/")
        return source, f"https://www.ziprecruiter.com{normalized_path}", normalized_path.split("/")[-1]

    if source == "Remotive":
        if not re.match(r"^/remote-jobs/[^/]+", path, re.IGNORECASE):
            raise JobUrlImportError("Select one Remotive job and copy its direct job URL.")
        normalized_path = path.rstrip("/")
        match = re.search(r"-(\d+)$", normalized_path)
        return source, f"https://remotive.com{normalized_path}", (
            match.group(1) if match else normalized_path.split("/")[-1]
        )

    if source == "Google Jobs":
        outbound = (query.get("url") or query.get("adurl") or [None])[0]
        if outbound:
            outbound_source, outbound_url, outbound_id = normalize_job_url(outbound)
            if outbound_source != "Google Jobs":
                return outbound_source, outbound_url, outbound_id
        job_id = (query.get("htidocid") or query.get("jobid") or [None])[0]
        return source, _generic_normalized_url(parsed), str(job_id or "")

    path_id = path.rstrip("/").split("/")[-1] if path.rstrip("/") else ""
    return source, _generic_normalized_url(parsed), path_id


def _new_session():
    session = requests.Session()
    session.trust_env = False
    session.max_redirects = MAX_REDIRECTS
    return session


def _safe_fetch(session: requests.Session, url: str):
    current_url = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        _validate_http_url(current_url)
        response = session.get(
            current_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json",
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        logger.info("job_import_fetch status=%s url=%s", response.status_code, current_url)
        if response.is_redirect or response.is_permanent_redirect:
            destination = response.headers.get("Location")
            response.close()
            if not destination:
                raise JobUrlImportError("The provider returned an invalid redirect.")
            current_url = urljoin(current_url, destination)
            continue
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
        logger.info(
            "job_import_response status=%s final_url=%s content_type=%s",
            response.status_code, current_url, content_type,
        )
        if content_type not in ("text/html", "application/xhtml+xml", "application/json"):
            response.close()
            raise JobUrlImportError("The provider returned an unsupported content type.")
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                response.close()
                raise JobUrlImportError("The provider response was too large to preview safely.")
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        status_code = response.status_code
        response.close()
        return (
            b"".join(chunks).decode(encoding, errors="replace"),
            content_type,
            current_url,
            status_code,
        )
    raise JobUrlImportError("The provider redirected too many times.")


def safe_fetch_job_page(url: str, session: requests.Session = None):
    """Return a structured result for expected network and provider failures."""
    owns_session = session is None
    active_session = session or _new_session()
    try:
        body, content_type, final_url, status_code = _safe_fetch(active_session, url)
        return {
            "ok": True,
            "status_code": status_code,
            "final_url": final_url,
            "html": body,
            "content_type": content_type,
            "blocked": False,
            "reason": "",
        }
    except requests.HTTPError as error:
        response = error.response
        status_code = response.status_code if response is not None else 0
        final_url = response.url if response is not None else url
        blocked = status_code in (401, 403, 429, 503)
        return {
            "ok": False,
            "status_code": status_code,
            "final_url": final_url,
            "html": "",
            "content_type": "",
            "blocked": blocked,
            "reason": f"Provider returned HTTP {status_code}.",
        }
    except requests.TooManyRedirects:
        return {
            "ok": False, "status_code": 0, "final_url": url, "html": "",
            "content_type": "", "blocked": True,
            "reason": "The provider redirected too many times.",
        }
    except requests.Timeout:
        return {
            "ok": False, "status_code": 0, "final_url": url, "html": "",
            "content_type": "", "blocked": True,
            "reason": "The provider timed out during automatic extraction.",
        }
    except requests.ConnectionError:
        return {
            "ok": False, "status_code": 0, "final_url": url, "html": "",
            "content_type": "", "blocked": True,
            "reason": "The provider did not expose a reachable public page.",
        }
    except JobUrlImportError as error:
        return {
            "ok": False, "status_code": 0, "final_url": url, "html": "",
            "content_type": "", "blocked": True, "reason": str(error),
        }
    finally:
        if owns_session:
            active_session.close()


def _parse_json_scripts(html_text: str):
    try:
        document = lxml_html.fromstring(html_text)
    except (ValueError, etree.ParserError):
        return [], None
    values = []
    scripts = document.xpath(
        "//script[@type='application/ld+json' or @type='application/json' or @id='__NEXT_DATA__']"
    )
    for script in scripts:
        raw = (script.text or script.text_content() or "").strip()
        if not raw:
            continue
        try:
            values.append(json.loads(unescape(raw)))
        except (TypeError, ValueError) as error:
            logger.debug("job_import_json_parse_failed error=%s", type(error).__name__)
    return values, document


def _jobposting_type(value):
    item_type = value.get("@type") if isinstance(value, dict) else None
    return item_type == "JobPosting" or (
        isinstance(item_type, list) and "JobPosting" in item_type
    )


def find_job_posting(value):
    if isinstance(value, list):
        for item in value:
            found = find_job_posting(item)
            if found:
                return found
    elif isinstance(value, dict):
        if _jobposting_type(value):
            return value
        for item in value.values():
            if isinstance(item, (dict, list)):
                found = find_job_posting(item)
                if found:
                    return found
    return None


def find_embedded_job(value):
    if isinstance(value, list):
        for item in value:
            found = find_embedded_job(item)
            if found:
                return found
    elif isinstance(value, dict):
        title = value.get("title") or value.get("jobTitle")
        description = value.get("description") or value.get("jobDescription")
        if title and description and len(str(description)) >= 100:
            return value
        for item in value.values():
            if isinstance(item, (dict, list)):
                found = find_embedded_job(item)
                if found:
                    return found
    return None


def _text(value):
    if isinstance(value, list):
        return ", ".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "").strip()
    return str(value or "").strip()


def _location_text(value):
    if isinstance(value, list):
        return ", ".join(filter(None, (_location_text(item) for item in value)))
    if not isinstance(value, dict):
        return _text(value)
    address = value.get("address") or value
    if not isinstance(address, dict):
        return _text(address)
    return ", ".join(filter(None, (
        _text(address.get("addressLocality")),
        _text(address.get("addressRegion")),
        _text(address.get("addressCountry")),
        _text(value.get("name")),
    )))


def clean_job_title(title: str, company: str = "", location: str = ""):
    cleaned = re.sub(
        r"\s*(?:\||-)\s*(?:LinkedIn|Dice(?:\.com)?|Indeed(?:\.com)?|ZipRecruiter)\s*$",
        "",
        unescape(str(title or "")),
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\b(?:Job Opening|Apply Now)\b", "", cleaned, flags=re.IGNORECASE).strip(" |-")
    hiring = re.match(r"^(.+?)\s+hiring\s+(.+?)\s+in\s+(.+?)$", cleaned, re.IGNORECASE)
    if hiring:
        parsed_company, parsed_title, parsed_location = (part.strip() for part in hiring.groups())
        if (not company or parsed_company.casefold() == company.casefold()) and (
            not location or parsed_location.casefold() == location.casefold()
        ):
            return parsed_title
    if company and cleaned.casefold().startswith(company.casefold() + " "):
        cleaned = cleaned[len(company):].lstrip(" |-")
    if location and cleaned.casefold().endswith(" in " + location.casefold()):
        cleaned = cleaned[:-(len(location) + 4)].rstrip(" |-")
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_linkedin_browser_title(title: str):
    cleaned = re.sub(r"\s*\|\s*LinkedIn\s*$", "", unescape(title or ""), flags=re.IGNORECASE)
    match = re.match(r"^(.+?)\s+hiring\s+(.+?)\s+in\s+(.+?)$", cleaned.strip(), re.IGNORECASE)
    if not match:
        return {}
    company, job_title, location = (part.strip() for part in match.groups())
    return {"company": company, "title": job_title, "location": location}


def clean_job_description(raw_description: str):
    raw = unescape(str(raw_description or "")).strip()
    if not raw:
        return ""
    for heading in (
        "Job Description", "Must Have Technical/Functional Skills",
        "Primary Skill", "Secondary Skill", "Required Skills",
        "Preferred Skills", "Technical Skills", "Qualifications",
        "Responsibilities",
    ):
        raw = re.sub(
            rf"({re.escape(heading)})(?=[A-Z])",
            rf"<br>\1<br>",
            raw,
            flags=re.IGNORECASE,
        )
    try:
        root = lxml_html.fragment_fromstring(raw, create_parent="div")
    except (ValueError, etree.ParserError):
        return re.sub(r"\s+", " ", raw).strip()
    for node in root.xpath(".//script|.//style|.//nav|.//footer|.//form|.//noscript"):
        node.drop_tree()
    block_tags = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "br"}
    lines, current = [], []

    def flush():
        text = re.sub(r"\s+", " ", " ".join(current)).strip()
        current.clear()
        if text:
            lines.append(text)

    def walk(node):
        if isinstance(node.tag, str) and node.tag.lower() in block_tags:
            flush()
        if node.text and node.text.strip():
            current.append(node.text.strip())
        for child in node:
            walk(child)
            if child.tail and child.tail.strip():
                current.append(child.tail.strip())
        if isinstance(node.tag, str) and node.tag.lower() in block_tags:
            flush()

    walk(root)
    flush()
    result = []
    for line in lines:
        normalized = line.strip(" \t|")
        lower = normalized.casefold()
        if not normalized or any(lower == item or lower.startswith(item + " ") for item in BOILERPLATE_LINES):
            continue
        if not result or result[-1].casefold() != lower:
            result.append(normalized)
    cleaned = "\n\n".join(result).strip()
    if len(cleaned) < 40 and any(marker in cleaned.casefold() for marker in BLOCKED_MARKERS):
        return ""
    return cleaned


def extract_skills(explicit_skills="", description=""):
    explicit = clean_job_description(_text(explicit_skills))
    found = []
    combined = f"{explicit}\n{description}"
    for skill in SKILL_VOCABULARY:
        pattern = re.escape(skill).replace(r"\ ", r"\s+")
        if re.search(rf"(?<!\w){pattern}(?!\w)", combined, re.IGNORECASE):
            canonical = "AWS" if skill == "Amazon Web Services" else skill
            if canonical.casefold() not in {item.casefold() for item in found}:
                found.append(canonical)
    if explicit:
        for part in re.split(r"[,;|\n]+", explicit):
            part = re.sub(r"\s+", " ", part).strip(" -•")
            if 1 < len(part) <= 50 and len(part.split()) <= 6:
                if part.casefold() not in {item.casefold() for item in found}:
                    found.append(part)
    if "Spring Boot" in found and "Spring" in found:
        found.remove("Spring")
    return ", ".join(found[:40])


def detect_blocked_or_invalid_page(html, final_url, status_code, page_title):
    if status_code >= 400:
        return f"Provider returned HTTP {status_code}."
    raw_sample = f"{page_title} {html[:10000]}".casefold()
    for marker in BLOCKED_MARKERS:
        if marker in raw_sample:
            return "The provider blocked automatic extraction."
    visible = clean_job_description(html)
    sample = f"{page_title} {visible[:3000]}".casefold()
    if not visible:
        return "The provider returned an empty or unusable page."
    for marker in INVALID_MARKERS:
        if marker in sample:
            return "The job page is unavailable or expired."
    path = urlsplit(final_url).path.casefold()
    if any(part in path for part in ("/login", "/signin", "/search")) and "viewjob" not in path:
        return "The provider redirected to a login or search page."
    return ""


def _meta(document, key):
    values = document.xpath(
        f"//meta[translate(@property,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{key.lower()}']/@content"
        f" | //meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{key.lower()}']/@content"
    )
    return values[0].strip() if values else ""


def _posting_details(posting):
    organization = posting.get("hiringOrganization") or {}
    location = posting.get("jobLocation") or posting.get("applicantLocationRequirements") or ""
    description = clean_job_description(posting.get("description"))
    explicit_skills = (
        posting.get("skills")
        or posting.get("qualifications")
        or posting.get("experienceRequirements")
        or ""
    )
    return {
        "title": _text(posting.get("title")),
        "company": _text(organization),
        "location": _location_text(location),
        "work_mode": "Remote" if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE" else "",
        "job_type": _text(posting.get("employmentType")).replace("_", " ").title(),
        "description": description,
        "skills_required": extract_skills(explicit_skills, description),
        "direct_url": _text(posting.get("url")),
        "date_posted": _text(posting.get("datePosted")),
    }


def _embedded_details(job):
    company = job.get("company") or job.get("companyName") or job.get("hiringOrganization") or ""
    location = job.get("location") or job.get("jobLocation") or ""
    description = clean_job_description(job.get("description") or job.get("jobDescription"))
    skills = job.get("skills") or job.get("skillList") or job.get("qualifications") or ""
    return {
        "title": _text(job.get("title") or job.get("jobTitle")),
        "company": _text(company),
        "location": _location_text(location),
        "work_mode": _text(job.get("workMode") or job.get("workplaceType")),
        "job_type": _text(job.get("employmentType") or job.get("jobType")).replace("_", " ").title(),
        "description": description,
        "skills_required": extract_skills(skills, description),
        "direct_url": _text(job.get("url") or job.get("jobUrl") or job.get("canonicalUrl")),
        "date_posted": _text(job.get("datePosted") or job.get("postedDate")),
    }


def _static_details(document, source):
    title = _meta(document, "og:title") or _meta(document, "twitter:title")
    description = clean_job_description(
        _meta(document, "og:description") or _meta(document, "description")
    )
    if not (
        len(description) >= 120
        and any(
            marker in description.casefold()
            for marker in ("responsibilities", "qualifications", "requirements", "job description", "experience")
        )
    ):
        description = ""
    company = _meta(document, "og:site_name")
    location = ""
    if source == "LinkedIn":
        browser = parse_linkedin_browser_title(title)
        title = browser.get("title", title)
        company = browser.get("company", company)
        location = browser.get("location", "")
        selectors = {
            "title": "//h1[contains(@class,'top-card-layout__title')]/text()",
            "company": "//*[contains(@class,'topcard__org-name-link')]/text()",
            "location": "//*[contains(@class,'topcard__flavor--bullet')]/text()",
            "description": "//*[contains(@class,'show-more-less-html__markup')]",
        }
        for field, xpath in selectors.items():
            nodes = document.xpath(xpath)
            if not nodes:
                continue
            value = nodes[0] if isinstance(nodes[0], str) else etree.tostring(nodes[0], encoding="unicode")
            if field == "description":
                description = clean_job_description(value)
            elif field == "title":
                title = str(value).strip()
            elif field == "company":
                company = str(value).strip()
            elif field == "location":
                location = str(value).strip()
    if not description:
        nodes = document.xpath(
            "//*[@itemprop='description']"
            " | //*[@data-testid='job-description']"
            " | //*[contains(@class,'job-description')]"
            " | //*[contains(@class,'jobDescription')]"
        )
        if nodes:
            description = clean_job_description(
                etree.tostring(nodes[0], encoding="unicode")
            )
    title = clean_job_title(title, company, location)
    return {
        "title": title,
        "company": company,
        "location": location,
        "work_mode": "",
        "job_type": "",
        "description": description,
        "skills_required": extract_skills("", description),
    }


def _extract_html_details(html_text, source, final_url, status_code):
    values, document = _parse_json_scripts(html_text)
    if document is None:
        return {}, "The provider returned malformed HTML.", []
    page_title = " ".join(document.xpath("//title/text()")).strip()
    logger.info("job_import_page title=%s final_url=%s", page_title[:200], final_url)
    posting = next((found for value in values if (found := find_job_posting(value))), None)
    embedded = next((found for value in values if (found := find_embedded_job(value))), None)
    raw_sample = f"{page_title} {html_text[:10000]}".casefold()
    blocked_marker = next((marker for marker in BLOCKED_MARKERS if marker in raw_sample), "")
    if blocked_marker:
        logger.info("job_import_page_blocked reason=%s", blocked_marker)
        return {}, "The provider blocked automatic extraction.", ["page validation"]
    if posting:
        return _posting_details(posting), "", ["JSON-LD"]
    if embedded:
        return _embedded_details(embedded), "", ["embedded application JSON"]
    invalid_reason = detect_blocked_or_invalid_page(html_text, final_url, status_code, page_title)
    if invalid_reason:
        return {}, invalid_reason, ["page validation"]

    methods = ["JSON-LD", "embedded application JSON", "Open Graph", "static HTML"]
    return _static_details(document, source), "", methods


def _remotive_api_preview(session, normalized_url):
    try:
        fetch = safe_fetch_job_page("https://remotive.com/api/remote-jobs", session)
        if not fetch["ok"] or fetch["content_type"] != "application/json":
            return None
        target_path = urlsplit(normalized_url).path.rstrip("/")
        for job in json.loads(fetch["html"]).get("jobs", []):
            if urlsplit(job.get("url") or "").path.rstrip("/") != target_path:
                continue
            tags = job.get("tags") or []
            description = clean_job_description(job.get("description"))
            return {
                "title": job.get("title") or "",
                "company": job.get("company_name") or "",
                "location": job.get("candidate_required_location") or "Remote",
                "work_mode": "Remote",
                "job_type": str(job.get("job_type") or "").replace("_", " ").title(),
                "description": description,
                "skills_required": extract_skills(tags, description),
            }
    except (JobUrlImportError, requests.RequestException, ValueError) as error:
        logger.info("job_import_remotive_api_failed error=%s", type(error).__name__)
    return None


def build_job_preview(job_url: str):
    source, normalized_url, external_job_id = normalize_job_url(job_url)
    logger.info("job_import_start provider=%s normalized_url=%s", source, normalized_url)
    session = _new_session()
    details, blocked_reason, attempted = {}, "", []

    if source == "Google Jobs":
        blocked_reason = (
            "Google Jobs did not expose a stable employer job URL. "
            "Paste the employer's direct job link or complete the details manually."
        )
        attempted = ["Google outbound URL detection"]
    elif source == "Remotive":
        attempted.append("Remotive API")
        details = _remotive_api_preview(session, normalized_url) or {}

    if not details and source != "Google Jobs":
        fetch = safe_fetch_job_page(normalized_url, session)
        if fetch["ok"]:
            final_url = fetch["final_url"]
            if fetch["content_type"] in ("text/html", "application/xhtml+xml"):
                if final_url.rstrip("/") != normalized_url.rstrip("/"):
                    try:
                        redirected_source, redirected_url, redirected_id = normalize_job_url(final_url)
                        if redirected_source != "Google Jobs":
                            source, normalized_url = redirected_source, redirected_url
                            external_job_id = redirected_id or external_job_id
                    except JobUrlImportError:
                        pass
                details, blocked_reason, html_methods = _extract_html_details(
                    fetch["html"], source, final_url, fetch["status_code"]
                )
                attempted.extend(html_methods)
                direct_url = details.pop("direct_url", "") if details else ""
                if direct_url:
                    try:
                        direct_source, direct_normalized, direct_id = normalize_job_url(direct_url)
                        if direct_source != "Google Jobs":
                            source, normalized_url = direct_source, direct_normalized
                            external_job_id = direct_id or external_job_id
                    except JobUrlImportError:
                        pass
        else:
            blocked_reason = fetch["reason"] or "The provider returned a blocked or dynamic page."
            logger.info(
                "job_import_fetch_unavailable provider=%s status=%s reason=%s",
                source, fetch["status_code"], blocked_reason,
            )
    session.close()

    fields = {field: str(details.get(field) or "").strip() for field in PREVIEW_FIELDS}
    fields["title"] = clean_job_title(fields["title"], fields["company"], fields["location"])
    fields["description"] = clean_job_description(fields["description"])
    fields["skills_required"] = extract_skills(fields["skills_required"], fields["description"])
    if not fields["location"]:
        fields["location"] = "Not specified"

    fields_fetched = [
        field for field in PREVIEW_FIELDS
        if fields[field] and not (field == "location" and fields[field] == "Not specified")
    ]
    fields_missing = [field for field in PREVIEW_FIELDS if field not in fields_fetched]
    requires_manual = any(field not in fields_fetched for field in REQUIRED_FIELDS)

    if not fields_fetched:
        status = "manual_required"
    elif requires_manual:
        status = "partial"
    else:
        status = "complete"

    if status == "partial" and not blocked_reason:
        blocked_reason = "Provider did not expose all job details publicly."
    elif status == "manual_required" and not blocked_reason:
        blocked_reason = "The provider returned a blocked or dynamic page."

    if source == "Google Jobs" and status == "manual_required":
        message = (
            "Open the job's Apply or employer link and paste that direct URL, "
            "or complete the job details manually."
        )
    elif status == "manual_required":
        message = (
            "The job URL was identified, but automatic extraction was unavailable. "
            "Paste the missing job details."
        )
    elif status == "partial":
        message = "Job identified. Complete the missing details before saving."
    else:
        message = "Job details fetched successfully."

    logger.info(
        "job_import_complete provider=%s fields_fetched=%s fields_missing=%s blocked_reason=%s methods=%s",
        source, fields_fetched, fields_missing, blocked_reason or "none", attempted,
    )
    return {
        "success": True,
        "status": status,
        "source": source,
        "save_source": SOURCE_VALUES[source],
        "normalized_job_url": normalized_url,
        "external_job_id": external_job_id,
        **fields,
        "requires_manual_details": requires_manual,
        "fields_fetched": fields_fetched,
        "fields_missing": fields_missing,
        "extraction_methods_attempted": attempted,
        "blocked_reason": blocked_reason or None,
        "message": message,
    }
