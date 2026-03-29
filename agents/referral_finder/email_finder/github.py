from __future__ import annotations

import logging
import time
from difflib import SequenceMatcher

import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_HEADERS_BASE = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _headers(token: str) -> dict[str, str]:
    h = dict(_HEADERS_BASE)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _company_matches(gh_company: str | None, target: str) -> bool:
    if not gh_company:
        return False
    return target.lower() in gh_company.lower() or gh_company.lower() in target.lower()


def find_email_via_github(
    first_name: str,
    last_name: str,
    company: str,
    github_token: str = "",
) -> str | None:
    """
    Try to find the person's email via their GitHub profile or commits.
    Returns the email string or None.
    """
    full_name = f"{first_name} {last_name}"
    headers = _headers(github_token)

    # --- Step 1: Search users by name + company ---
    query = f"fullname:{first_name} {last_name} type:user"
    try:
        resp = requests.get(
            f"{GITHUB_API}/search/users",
            params={"q": query, "per_page": 5},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        users = resp.json().get("items", [])
    except Exception as exc:
        logger.debug(f"GitHub user search failed for {full_name}: {exc}")
        return None

    if not users:
        return None

    # Respect GitHub secondary rate limit
    time.sleep(0.5)

    # --- Step 2: Fetch full profile for each candidate, score them ---
    best_user = None
    best_score = 0.0

    for user in users[:5]:
        username = user.get("login", "")
        try:
            profile_resp = requests.get(
                f"{GITHUB_API}/users/{username}",
                headers=headers,
                timeout=10,
            )
            profile_resp.raise_for_status()
            profile = profile_resp.json()
        except Exception:
            continue

        time.sleep(0.3)

        # Score: name similarity (0-1) + company match bonus (0.3)
        gh_name = profile.get("name") or ""
        name_score = _name_similarity(full_name, gh_name)
        company_bonus = 0.3 if _company_matches(profile.get("company"), company) else 0.0
        score = name_score + company_bonus

        logger.debug(
            f"  GitHub candidate {username!r}: name={gh_name!r}, "
            f"company={profile.get('company')!r}, score={score:.2f}"
        )

        if score > best_score:
            best_score = score
            best_user = (username, profile)

    if best_score < 0.7 or best_user is None:
        logger.debug(f"No confident GitHub match for {full_name} (best score: {best_score:.2f})")
        return None

    username, profile = best_user

    # --- Step 3: Check profile email ---
    if profile.get("email"):
        logger.info(f"Found email for {full_name} via GitHub profile: {profile['email']}")
        return profile["email"]

    # --- Step 4: Mine email from commit metadata ---
    email = _email_from_commits(username, headers)
    if email:
        logger.info(f"Found email for {full_name} via GitHub commits: {email}")
    return email


def _email_from_commits(username: str, headers: dict[str, str]) -> str | None:
    """Scan the user's recent public repos for commit author emails."""
    try:
        repos_resp = requests.get(
            f"{GITHUB_API}/users/{username}/repos",
            params={"type": "owner", "sort": "pushed", "per_page": 5},
            headers=headers,
            timeout=10,
        )
        repos_resp.raise_for_status()
        repos = repos_resp.json()
    except Exception as exc:
        logger.debug(f"Could not fetch repos for {username}: {exc}")
        return None

    for repo in repos[:3]:
        repo_name = repo.get("name", "")
        time.sleep(0.3)
        try:
            commits_resp = requests.get(
                f"{GITHUB_API}/repos/{username}/{repo_name}/commits",
                params={"author": username, "per_page": 1},
                headers=headers,
                timeout=10,
            )
            commits_resp.raise_for_status()
            commits = commits_resp.json()
        except Exception:
            continue

        if commits and isinstance(commits, list):
            commit = commits[0]
            email = commit.get("commit", {}).get("author", {}).get("email", "")
            if email and "noreply" not in email and "@" in email:
                return email

    return None
