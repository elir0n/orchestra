from __future__ import annotations

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

from agents.referral_finder.config import ReferralFinderConfig
from agents.referral_finder.email_finder.pipeline import EmailResult, find_email
from agents.referral_finder.generator import generate_referral_email
from agents.referral_finder.notifier import OutreachResult, send_results
from agents.referral_finder.search.linkedin_search import (
    RECRUITER_ROLES,
    Person,
    find_candidates,
)
from agents.referral_finder.search.seen_contacts import SeenContactsCache
from shared.base_agent import BaseAgent
from shared.models import AgentResult, AgentRunContext, AgentStatus

logger = logging.getLogger(__name__)

_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


class ReferralFinderAgent(BaseAgent[ReferralFinderConfig, list[dict]]):
    name = "referral-finder"
    description = "Find engineers at a company via LinkedIn and generate personalized referral request emails"
    version = "0.2.0"

    # ------------------------------------------------------------------
    # CLI wiring
    # ------------------------------------------------------------------

    @classmethod
    def build_arg_parser(cls, subparser: argparse.ArgumentParser) -> None:
        # Company targeting — --company (single, legacy) OR --companies (multi) OR --companies-file
        company_group = subparser.add_mutually_exclusive_group(required=True)
        company_group.add_argument(
            "--company",
            help='Single target company (e.g. "Stripe")',
        )
        company_group.add_argument(
            "--companies",
            nargs="+",
            metavar="COMPANY",
            help='One or more target companies (e.g. --companies Wix Google Stripe)',
        )
        company_group.add_argument(
            "--companies-file",
            dest="companies_file",
            metavar="FILE",
            help='Path to a text file with one company name per line',
        )

        subparser.add_argument(
            "--your-name", default=None, dest="your_name",
            help="Your full name for the email drafts (or set REFERRAL_FINDER_YOUR_NAME in .env)",
        )
        subparser.add_argument(
            "--your-background", default=None, dest="your_background",
            help='Brief professional background, quoted (or set REFERRAL_FINDER_YOUR_BACKGROUND in .env)',
        )
        subparser.add_argument(
            "--target-role", default=None, dest="target_role",
            help='Specific role you are applying for — makes emails more precise '
                 '(e.g. "Backend Engineer"). Env: REFERRAL_FINDER_TARGET_ROLE',
        )
        subparser.add_argument(
            "--roles", nargs="+",
            default=["Software Engineer", "Senior Engineer", "Tech Lead", "Staff Engineer"],
            help="LinkedIn role keywords to search (default: Software/Senior Engineer, Tech Lead, Staff Engineer)",
        )
        subparser.add_argument(
            "--include-recruiters", action="store_true", dest="include_recruiters",
            help="Also search for Recruiters, Talent Acquisition, and Engineering Managers",
        )
        subparser.add_argument(
            "--seniority", nargs="+", default=[],
            choices=["intern", "junior", "mid", "senior", "staff", "lead", "manager"],
            metavar="LEVEL",
            help="Only include people whose role matches a seniority tier "
                 "(choices: intern junior mid senior staff lead manager). "
                 "Default: no filter.",
        )
        subparser.add_argument(
            "--min-results", type=int, default=3, dest="min_results",
            help="Minimum candidates to find before stopping per company (default: 3)",
        )
        subparser.add_argument(
            "--max-results", type=int, default=15, dest="max_results",
            help="Maximum candidates to process per company (default: 15)",
        )
        subparser.add_argument(
            "--location", default=None,
            help='Location filter (default: "Israel", or set REFERRAL_FINDER_LOCATION in .env)',
        )
        subparser.add_argument(
            "--university", default=None,
            help='Your university — warm-connection hint in emails (or set REFERRAL_FINDER_UNIVERSITY in .env)',
        )
        subparser.add_argument(
            "--min-email-confidence", default="low", dest="min_email_confidence",
            choices=["low", "medium", "high"],
            help="Only include contacts whose email confidence meets this threshold (default: low = all)",
        )
        subparser.add_argument(
            "--output-file", default="", dest="output_file",
            metavar="FILE",
            help="Write all results to a JSON file in addition to (or instead of) email",
        )
        subparser.add_argument(
            "--ignore-seen", action="store_true", dest="ignore_seen",
            help="Process all contacts even if already seen in a previous run",
        )
        subparser.add_argument(
            "--reset-seen", action="store_true", dest="reset_seen",
            help="Wipe the seen-contacts cache before running",
        )
        subparser.add_argument(
            "--seen-contacts-path", default=None, dest="seen_contacts_path",
            help="Path to seen-contacts JSON cache (default: ~/.orchestra/referral_finder_seen.json)",
        )

    @classmethod
    def config_from_args(cls, args: argparse.Namespace) -> ReferralFinderConfig:
        # Resolve company list
        if args.company:
            companies = [args.company]
        elif args.companies:
            companies = args.companies
        else:
            path = Path(args.companies_file)
            companies = [
                line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]

        config = ReferralFinderConfig(
            companies=companies,
            roles=args.roles,
            include_recruiters=args.include_recruiters,
            seniority=args.seniority,
            min_results=args.min_results,
            max_results=args.max_results,
            min_email_confidence=args.min_email_confidence,
            output_file=args.output_file,
            ignore_seen=args.ignore_seen,
            reset_seen=args.reset_seen,
        )
        if args.your_name is not None:
            config.your_name = args.your_name
        if args.your_background is not None:
            config.your_background = args.your_background
        if args.target_role is not None:
            config.target_role = args.target_role
        if args.location is not None:
            config.location = args.location
        if args.university is not None:
            config.university = args.university
        if args.seen_contacts_path is not None:
            config.seen_contacts_path = args.seen_contacts_path
        return config

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        import os
        self._claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._seen_cache = SeenContactsCache(self.config.seen_contacts_path)
        self._seen_cache.load()
        if self.config.reset_seen:
            self._seen_cache.reset()

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(
        self, input: ReferralFinderConfig, ctx: AgentRunContext
    ) -> AgentResult[list[dict]]:
        config = self.config

        missing = [
            f for f, v in [
                ("your_name (--your-name or REFERRAL_FINDER_YOUR_NAME)", config.your_name),
                ("your_background (--your-background or REFERRAL_FINDER_YOUR_BACKGROUND)", config.your_background),
            ] if not v
        ]
        if missing:
            return AgentResult(
                status=AgentStatus.FAILURE,
                errors=[f"Missing required config: {', '.join(missing)}"],
            )

        if not config.tavily_api_key:
            return AgentResult(
                status=AgentStatus.FAILURE,
                errors=["REFERRAL_FINDER_TAVILY_API_KEY is not set. Add it to your .env file."],
            )

        # Build effective role list
        roles = list(config.roles)
        if config.include_recruiters:
            roles = roles + [r for r in RECRUITER_ROLES if r not in roles]

        all_results: list[OutreachResult] = []
        all_errors: list[str] = []
        total_skipped_seen = 0

        for company in config.companies:
            logger.info(f"--- Company: {company} ---")
            results, errors, skipped = self._run_for_company(company, roles, config, ctx)
            all_results.extend(results)
            all_errors.extend(errors)
            total_skipped_seen += skipped

        if total_skipped_seen:
            logger.info(
                f"Skipped {total_skipped_seen} already-contacted person(s) total. "
                "Use --ignore-seen to include them or --reset-seen to start fresh."
            )

        # --- Output file ---
        if config.output_file:
            _write_output_file(all_results, config.output_file)

        # --- Notification ---
        from shared.config import OrchestraConfig
        from shared.notifications import NotificationService

        companies_label = ", ".join(config.companies)
        try:
            global_config = OrchestraConfig.from_env()
            notifications = NotificationService.from_config(global_config)
            send_results(
                results=all_results,
                company=companies_label,
                your_name=config.your_name,
                notifications=notifications,
                dry_run=ctx.dry_run,
            )
        except Exception as exc:
            all_errors.append(f"Notification failed: {exc}")
            logger.error(f"Notification error: {exc}")

        status = AgentStatus.SUCCESS if not all_errors else AgentStatus.PARTIAL
        found_emails = sum(1 for r in all_results if r.email_result.email)

        return AgentResult(
            status=status,
            data=[_outreach_to_dict(r) for r in all_results],
            errors=all_errors,
            metrics={
                "companies_processed": len(config.companies),
                "candidates_found": len(all_results),
                "candidates_skipped_seen": total_skipped_seen,
                "emails_found": found_emails,
                "emails_not_found": len(all_results) - found_emails,
            },
        )

    # ------------------------------------------------------------------
    # Per-company pipeline
    # ------------------------------------------------------------------

    def _run_for_company(
        self,
        company: str,
        roles: list[str],
        config: ReferralFinderConfig,
        ctx: AgentRunContext,
    ) -> tuple[list[OutreachResult], list[str], int]:
        # Step 1: Find candidates
        logger.info(f"Searching for people at '{company}' in roles: {roles}")
        candidates = find_candidates(
            company=company,
            roles=roles,
            api_key=config.tavily_api_key,
            max_results=config.max_results,
            location=config.location,
            seniority=config.seniority,
        )

        if len(candidates) < config.min_results:
            logger.warning(
                f"Only found {len(candidates)} candidate(s) at {company} "
                f"(min requested: {config.min_results})."
            )

        if not candidates:
            logger.warning(f"No candidates found for {company}.")
            return [], [f"No candidates found for {company}."], 0

        # Step 2: Filter already-seen
        skipped = 0
        if not config.ignore_seen:
            before = len(candidates)
            candidates = [c for c in candidates if not self._seen_cache.contains(c)]
            skipped = before - len(candidates)

        if not candidates:
            logger.info(f"All candidates at {company} already seen — skipping.")
            return [], [], skipped

        # Step 3: Enrich + generate (concurrent)
        logger.info(f"Enriching {len(candidates)} candidate(s) at {company}...")
        results, errors = self._process_candidates(candidates, config)

        # Step 4: Apply confidence filter
        min_rank = _CONFIDENCE_RANK.get(config.min_email_confidence, 1)
        if min_rank > 1:
            before_filter = len(results)
            results = [
                r for r in results
                if r.email_result.email is None  # keep "no email" contacts (for LinkedIn reach-out)
                or _CONFIDENCE_RANK.get(r.email_result.confidence, 1) >= min_rank
            ]
            dropped = before_filter - len(results)
            if dropped:
                logger.info(f"Dropped {dropped} contact(s) below confidence threshold '{config.min_email_confidence}'.")

        return results, errors, skipped

    # ------------------------------------------------------------------
    # Candidate processing
    # ------------------------------------------------------------------

    def _process_candidates(
        self,
        candidates: list[Person],
        config: ReferralFinderConfig,
    ) -> tuple[list[OutreachResult], list[str]]:
        results: list[OutreachResult] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_person = {
                executor.submit(self._process_one, person, config): person
                for person in candidates
            }
            for future in as_completed(future_to_person):
                person = future_to_person[future]
                try:
                    result = future.result()
                    results.append(result)
                    # Mark as seen immediately (crash-safe)
                    self._seen_cache.add(person)
                    self._seen_cache.save()
                    email_display = result.email_result.email or "no email"
                    logger.info(f"  Processed: {person.name} — {email_display}")
                except Exception as exc:
                    errors.append(f"{person.name}: {exc}")
                    logger.warning(f"  Failed to process {person.name}: {exc}")

        # Sort: contacts with email first, then alphabetically
        results.sort(key=lambda r: (r.email_result.email is None, r.person.name))
        return results, errors

    def _process_one(
        self,
        person: Person,
        config: ReferralFinderConfig,
    ) -> OutreachResult:
        email_result = find_email(
            person=person,
            github_token=config.github_token,
            smtp_from_domain=config.smtp_from_domain,
        )
        message = generate_referral_email(
            person=person,
            your_name=config.your_name,
            your_background=config.your_background,
            client=self._claude,
            model=config.claude_model,
            location=config.location,
            university=config.university,
            snippet=person.role_hint,
            target_role=config.target_role,
        )
        return OutreachResult(
            person=person,
            email_result=email_result,
            generated_message=message,
        )


# ---------------------------------------------------------------------------
# Output file helper
# ---------------------------------------------------------------------------

def _write_output_file(results: list[OutreachResult], path: str) -> None:
    data = [_outreach_to_dict(r) for r in results]
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Results written to {path}")
    except Exception as exc:
        logger.warning(f"Could not write output file {path}: {exc}")


def _outreach_to_dict(r: OutreachResult) -> dict:
    return {
        "name": r.person.name,
        "linkedin_url": r.person.linkedin_url,
        "role_hint": r.person.role_hint,
        "company": r.person.company,
        "email": r.email_result.email,
        "email_source": r.email_result.source,
        "email_confidence": r.email_result.confidence,
        "generated_message": r.generated_message,
    }
