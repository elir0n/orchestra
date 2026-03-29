from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from agents.referral_finder.config import ReferralFinderConfig
from agents.referral_finder.email_finder.pipeline import find_email
from agents.referral_finder.generator import generate_referral_email
from agents.referral_finder.notifier import OutreachResult, send_results
from agents.referral_finder.search.linkedin_search import Person, find_candidates
from shared.base_agent import BaseAgent
from shared.models import AgentResult, AgentRunContext, AgentStatus

logger = logging.getLogger(__name__)


class ReferralFinderAgent(BaseAgent[ReferralFinderConfig, list[dict]]):
    name = "referral-finder"
    description = "Find engineers at a company via LinkedIn and generate personalized referral request emails"
    version = "0.1.0"

    # ------------------------------------------------------------------
    # CLI wiring
    # ------------------------------------------------------------------

    @classmethod
    def build_arg_parser(cls, subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--company", required=True,
            help='Target company name (e.g. "Stripe")',
        )
        subparser.add_argument(
            "--your-name", required=True, dest="your_name",
            help="Your full name for the email drafts",
        )
        subparser.add_argument(
            "--your-background", required=True, dest="your_background",
            help='Brief professional background, quoted (e.g. "5 years backend Python at a Series B fintech")',
        )
        subparser.add_argument(
            "--roles", nargs="+",
            default=["Software Engineer", "Senior Engineer", "Tech Lead", "Staff Engineer"],
            help="Role keywords to search for (default: Engineer, Senior Engineer, Tech Lead, Staff Engineer)",
        )
        subparser.add_argument(
            "--min-results", type=int, default=3, dest="min_results",
            help="Minimum number of candidates to find before stopping search (default: 3)",
        )
        subparser.add_argument(
            "--max-results", type=int, default=15, dest="max_results",
            help="Maximum number of candidates to process (default: 15)",
        )
        subparser.add_argument(
            "--location", default="Israel",
            help='Location to filter candidates by (default: "Israel")',
        )
        subparser.add_argument(
            "--university", default="Bar Ilan University", dest="university",
            help='Your university — used as a warm-connection hint in emails (default: "Bar Ilan University")',
        )

    @classmethod
    def config_from_args(cls, args: argparse.Namespace) -> ReferralFinderConfig:
        return ReferralFinderConfig(
            company=args.company,
            your_name=args.your_name,
            your_background=args.your_background,
            roles=args.roles,
            location=args.location,
            university=args.university,
            min_results=args.min_results,
            max_results=args.max_results,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        import os
        self._claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(
        self, input: ReferralFinderConfig, ctx: AgentRunContext
    ) -> AgentResult[list[dict]]:
        config = self.config

        if not config.tavily_api_key:
            return AgentResult(
                status=AgentStatus.FAILURE,
                errors=["REFERRAL_FINDER_TAVILY_API_KEY is not set. Add it to your .env file."],
            )

        # --- Step 1: Find candidates ---
        logger.info(f"Searching for people at '{config.company}' in roles: {config.roles}")
        candidates = find_candidates(
            company=config.company,
            roles=config.roles,
            api_key=config.tavily_api_key,
            max_results=config.max_results,
            location=config.location,
        )

        if len(candidates) < config.min_results:
            logger.warning(
                f"Only found {len(candidates)} candidates (min requested: {config.min_results}). "
                "Try different role keywords or check your Tavily API key."
            )

        if not candidates:
            return AgentResult(
                status=AgentStatus.FAILURE,
                errors=["No candidates found. Try different --roles or check your Tavily API key."],
            )

        # --- Step 2: Enrich + generate (concurrent) ---
        logger.info(f"Enriching {len(candidates)} candidates (email + message generation)...")
        results, errors = self._process_candidates(candidates, config)

        # --- Step 3: Notify ---
        from shared.notifications import NotificationService
        from shared.config import OrchestraConfig
        try:
            global_config = OrchestraConfig.from_env()
            notifications = NotificationService.from_config(global_config)
            send_results(
                results=results,
                company=config.company,
                your_name=config.your_name,
                notifications=notifications,
                dry_run=ctx.dry_run,
            )
        except Exception as exc:
            errors.append(f"Notification failed: {exc}")
            logger.error(f"Notification error: {exc}")

        # --- Step 4: Return result ---
        status = AgentStatus.SUCCESS if not errors else AgentStatus.PARTIAL
        found_emails = sum(1 for r in results if r.email_result.email)

        return AgentResult(
            status=status,
            data=[_outreach_to_dict(r) for r in results],
            errors=errors,
            metrics={
                "candidates_found": len(candidates),
                "emails_found": found_emails,
                "emails_not_found": len(results) - found_emails,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
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
                    email_display = result.email_result.email or "no email"
                    logger.info(f"  Processed: {person.name} — {email_display}")
                except Exception as exc:
                    errors.append(f"{person.name}: {exc}")
                    logger.warning(f"  Failed to process {person.name}: {exc}")

        # Sort by whether email was found (found first)
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
        )
        return OutreachResult(
            person=person,
            email_result=email_result,
            generated_message=message,
        )


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
