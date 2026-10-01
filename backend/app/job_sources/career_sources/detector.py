"""ATS detection from host/path and explicit public career links, never guesses."""
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class Detection:
    provider: str
    url: str
    board: str = ""


def detect(url: str) -> Detection:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    board = parts[0] if parts else ""
    matches = (
        ("greenhouse", ("greenhouse.io",)), ("lever", ("lever.co",)),
        ("workday", ("myworkdayjobs.com",)), ("ashby", ("ashbyhq.com",)),
        ("smartrecruiters", ("smartrecruiters.com",)), ("icims", ("icims.com",)),
        ("successfactors", ("successfactors.com", "successfactors.eu", "successfactors.jobs", "jobs2web.com", "hcm.ondemand.com")),
    )
    for provider, hosts in matches:
        if any(host == suffix or host.endswith("." + suffix) for suffix in hosts):
            if provider == "greenhouse" and board == "embed":
                board = parse_qs(parsed.query).get("for", [""])[0]
            if provider == "workday":
                board = next((p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)), "")
            if provider in {"icims", "successfactors"}:
                # These providers namespace tenants by hostname, not the
                # first path segment (which often changes on detail pages).
                board = host
            return Detection(provider, url, board)
    return Detection("custom", url)


def embedded_board(html: str, page_url: str) -> Detection | None:
    # Only explicit links/iframes. Scripts, descriptions and arbitrary strings
    # are not evidence that a company uses a different board.
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("iframe[src], a[href]"):
        url = urljoin(page_url, node.get("src") or node.get("href") or "")
        detected = detect(url)
        if detected.provider != "custom" and detected.board:
            return detected
    return None


def same_board(board_url: str, job_url: str) -> bool:
    """Recognize public ATS host aliases without crossing employer tenants."""
    board, target = detect(board_url), detect(job_url)
    hosts = {urlsplit(board_url).hostname, urlsplit(job_url).hostname}
    aliases = {
        "greenhouse": {"boards.greenhouse.io", "job-boards.greenhouse.io"},
        "smartrecruiters": {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"},
    }
    if board.provider == "custom":
        return len(hosts) == 1
    return (board.provider == target.provider and board.board == target.board
            and (len(hosts) == 1 or hosts <= aliases.get(board.provider, set())))
