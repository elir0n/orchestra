from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.referral_finder.email_finder.pipeline import EmailResult
from agents.referral_finder.search.linkedin_search import Person
from shared.notifications import NotificationService

logger = logging.getLogger(__name__)

_DIVIDER = "=" * 60


@dataclass
class OutreachResult:
    person: Person
    email_result: EmailResult
    generated_message: str


def build_notification_body(
    results: list[OutreachResult],
    company: str,
    your_name: str,
) -> str:
    found = [r for r in results if r.email_result.email]
    not_found = [r for r in results if not r.email_result.email]

    lines: list[str] = [
        f"Referral Outreach Results — {company}",
        f"Run by: {your_name}",
        f"Candidates: {len(results)} total | {len(found)} with email | {len(not_found)} email not found",
        "",
        _DIVIDER,
        "",
    ]

    for r in results:
        email_line = (
            f"Email:    {r.email_result.email}  "
            f"(via {r.email_result.source}, confidence: {r.email_result.confidence})"
            if r.email_result.email
            else "Email:    NOT FOUND — connect via LinkedIn first"
        )

        lines += [
            f"=== {r.person.name} — {r.person.role_hint or 'Engineer'} at {r.person.company} ===",
            f"LinkedIn: {r.person.linkedin_url}",
            email_line,
            "",
            "Suggested message:",
            "---",
            r.generated_message,
            "---",
            "",
            _DIVIDER,
            "",
        ]

    return "\n".join(lines)


def send_results(
    results: list[OutreachResult],
    company: str,
    your_name: str,
    notifications: NotificationService,
    dry_run: bool = False,
) -> None:
    found_count = sum(1 for r in results if r.email_result.email)
    subject = f"Referral Outreach — {len(results)} candidates at {company} ({found_count} emails found)"
    body = build_notification_body(results, company, your_name)

    if dry_run:
        print("\n" + "=" * 70)
        print("DRY RUN — notification email NOT sent. Would have sent:")
        print(f"Subject: {subject}")
        print("=" * 70)
        print(body)
        return

    notifications.send_email(subject=subject, body=body)
    logger.info(f"Notification sent: {subject}")
