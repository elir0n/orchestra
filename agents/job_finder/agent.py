from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from agents.job_finder.config import JobFinderConfig
from agents.job_finder.cv_tailor.docx_writer import markdown_to_docx, read_docx_text
from agents.job_finder.cv_tailor.tailor import tailor_cv
from agents.job_finder.search.job_search import ALL_SITES, SUPPORTED_SITES, JobPosting, search_jobs
from agents.job_finder.search.seen_jobs import SeenJobsCache
from agents.referral_finder.email_finder.pipeline import find_email
from agents.referral_finder.generator import generate_referral_email
from agents.referral_finder.notifier import OutreachResult
from agents.referral_finder.search.linkedin_search import find_candidates
from shared.base_agent import BaseAgent
from shared.models import AgentResult, AgentRunContext, AgentStatus

logger = logging.getLogger(__name__)


@dataclass
class TailoredCV:
    job: JobPosting
    cv_content: str   # markdown from Claude
    output_path: str  # path to saved .docx (empty on dry-run)


@dataclass
class CompanyReferralResult:
    company: str
    contacts: list[OutreachResult] = field(default_factory=list)
    error: str | None = None


class JobFinderAgent(BaseAgent[JobFinderConfig, list[dict]]):
    name = "job-finder"
    description = "Find jobs in Israel and tailor your CV to each one, then find referral contacts"
    version = "0.1.0"

    # ------------------------------------------------------------------
    # CLI wiring
    # ------------------------------------------------------------------

    @classmethod
    def build_arg_parser(cls, subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--master-cv", default=None, dest="master_cv",
            help="Path to your master CV file (text, markdown, or .docx) (or set JOB_FINDER_MASTER_CV_PATH in .env)",
        )
        subparser.add_argument(
            "--format-cv", default=None, dest="format_cv",
            help="Path to format/template CV (or set JOB_FINDER_FORMAT_CV_PATH in .env)",
        )
        subparser.add_argument(
            "--role", default="",
            help='Job role to search for (e.g. "Backend Engineer"). '
                 'Leave empty to search broadly across all tech roles.',
        )
        subparser.add_argument(
            "--location", default=None,
            help='Job location (default: "Israel", or set JOB_FINDER_LOCATION in .env)',
        )
        subparser.add_argument(
            "--startup", action="store_true",
            help="Filter to startup companies only",
        )
        subparser.add_argument(
            "--job-type", default=None, dest="job_type",
            nargs="+", choices=["full-time", "part-time", "contract"],
            help="Employment type(s) — pass one or more (e.g. --job-type full-time part-time). "
                 "Env: JOB_FINDER_JOB_TYPE=full-time,part-time",
        )
        subparser.add_argument(
            "--max-jobs", type=int, default=5, dest="max_jobs",
            help="Max number of jobs to process (default: 5)",
        )
        subparser.add_argument(
            "--output-dir", default=None, dest="output_dir",
            help="Directory to save tailored CVs (default: ./tailored_cvs, or set JOB_FINDER_OUTPUT_DIR in .env)",
        )
        subparser.add_argument(
            "--claude-model", default="claude-sonnet-4-6", dest="claude_model",
            help="Claude model for CV tailoring (default: claude-sonnet-4-6)",
        )
        subparser.add_argument(
            "--remote", action="store_true",
            help="Filter for remote/work-from-home roles",
        )
        subparser.add_argument(
            "--no-referral", action="store_false", dest="referral",
            help="Skip the automatic referral contact search",
        )
        subparser.add_argument(
            "--ignore-seen", action="store_true", dest="ignore_seen",
            help="Process all jobs even if already seen in a previous run",
        )
        subparser.add_argument(
            "--reset-seen", action="store_true", dest="reset_seen",
            help="Wipe the seen-jobs cache before running",
        )
        subparser.add_argument(
            "--seen-jobs-path", default=None, dest="seen_jobs_path",
            help="Path to the seen-jobs JSON cache file (default: ~/.orchestra/job_finder_seen.json)",
        )
        subparser.add_argument(
            "--sites",
            nargs="+",
            default=None,
            choices=ALL_SITES,
            metavar="SITE",
            help=(
                "Limit search to specific sites. Choices: "
                + ", ".join(ALL_SITES)
                + ". Default: all sites."
            ),
        )

    @classmethod
    def config_from_args(cls, args: argparse.Namespace) -> JobFinderConfig:
        config = JobFinderConfig(
            role=args.role,
            startup=args.startup,
            max_jobs=args.max_jobs,
            dry_run=getattr(args, "dry_run", False),
            claude_model=args.claude_model,
            remote=args.remote,
            referral=args.referral,
        )
        if args.master_cv is not None:
            config.master_cv_path = args.master_cv
        if args.format_cv is not None:
            config.format_cv_path = args.format_cv
        if args.location is not None:
            config.location = args.location
        if args.job_type is not None:
            config.job_types = args.job_type  # list[str] from nargs="+"
        if args.output_dir is not None:
            config.output_dir = args.output_dir
        if args.sites is not None:
            config.sites = args.sites
        config.ignore_seen = args.ignore_seen
        config.reset_seen = args.reset_seen
        if args.seen_jobs_path is not None:
            config.seen_jobs_path = args.seen_jobs_path
        return config

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        self._claude = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )
        self._seen_cache = SeenJobsCache(self.config.seen_jobs_path)
        self._seen_cache.load()
        if self.config.reset_seen:
            self._seen_cache.reset()

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(
        self, input: JobFinderConfig, ctx: AgentRunContext
    ) -> AgentResult[list[dict]]:
        config = self.config

        missing = [
            f for f, v in [
                ("master_cv_path (--master-cv or JOB_FINDER_MASTER_CV_PATH)", config.master_cv_path),
                ("format_cv_path (--format-cv or JOB_FINDER_FORMAT_CV_PATH)", config.format_cv_path),
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
                errors=["JOB_FINDER_TAVILY_API_KEY is not set. Add it to your .env file."],
            )

        # --- Step 1: Read CV files ---
        try:
            master_cv = _read_cv_file(config.master_cv_path)
            format_cv = _read_cv_file(config.format_cv_path)
        except OSError as exc:
            return AgentResult(
                status=AgentStatus.FAILURE,
                errors=[f"Could not read CV file: {exc}"],
            )

        # --- Step 2: Search for jobs ---
        logger.info(
            f"Searching for '{config.role}' jobs in {config.location} "
            f"(startup={config.startup}, types={config.job_types}, remote={config.remote})"
        )
        jobs = search_jobs(
            role=config.role,
            api_key=config.tavily_api_key,
            location=config.location,
            job_types=config.job_types,
            startup=config.startup,
            max_jobs=config.max_jobs,
            remote=config.remote,
            sites=config.sites,
        )

        if not jobs:
            return AgentResult(
                status=AgentStatus.FAILURE,
                errors=["No jobs found. Try different --role or relax --startup filter."],
            )

        skipped_seen = 0
        if not config.ignore_seen:
            before = len(jobs)
            jobs = [j for j in jobs if not self._seen_cache.contains(j.url)]
            skipped_seen = before - len(jobs)
            if skipped_seen:
                logger.info(
                    f"Skipped {skipped_seen} already-seen job(s). "
                    "Use --ignore-seen to include them or --reset-seen to start fresh."
                )
            if not jobs:
                return AgentResult(
                    status=AgentStatus.SUCCESS,
                    data=[],
                    metrics={"jobs_found": before, "jobs_skipped_seen": skipped_seen,
                             "cvs_tailored": 0, "cvs_failed": 0,
                             "companies_searched_for_referrals": 0, "referral_contacts_found": 0},
                )

        logger.info(f"Found {len(jobs)} jobs. Tailoring CVs...")

        # --- Step 3: Prepare output directory ---
        dry = ctx.dry_run or config.dry_run
        if not dry:
            Path(config.output_dir).mkdir(parents=True, exist_ok=True)

        # --- Step 4: Tailor CVs concurrently ---
        tailored, errors = self._process_jobs(jobs, master_cv, format_cv, config, ctx)

        # --- Step 5: Referral pipeline (auto-triggered, skippable via --no-referral) ---
        referral_results: dict[str, CompanyReferralResult] = {}
        if config.referral:
            companies_with_jobs = [
                t for t in tailored if t.job.company and t.job.company != "Unknown"
            ]
            if companies_with_jobs:
                unique_count = len(dict.fromkeys(t.job.company for t in companies_with_jobs))
                logger.info(f"Running referral search for {unique_count} company/companies...")
                referral_results = self._find_referrals(companies_with_jobs, config)

        # --- Step 6: Send merged email (or print on dry-run) ---
        if dry:
            _print_referral_results(referral_results)
        elif tailored:
            self._send_email(tailored, config, referral_results)

        status = (
            AgentStatus.SUCCESS if not errors
            else (AgentStatus.PARTIAL if tailored else AgentStatus.FAILURE)
        )

        return AgentResult(
            status=status,
            data=[_cv_to_dict(t) for t in tailored],
            errors=errors,
            metrics={
                "jobs_found": len(jobs),
                "jobs_skipped_seen": skipped_seen,
                "cvs_tailored": len(tailored),
                "cvs_failed": len(errors),
                "companies_searched_for_referrals": len(referral_results),
                "referral_contacts_found": sum(
                    len(r.contacts) for r in referral_results.values()
                ),
            },
        )

    # ------------------------------------------------------------------
    # CV processing
    # ------------------------------------------------------------------

    def _process_jobs(
        self,
        jobs: list[JobPosting],
        master_cv: str,
        format_cv: str,
        config: JobFinderConfig,
        ctx: AgentRunContext,
    ) -> tuple[list[TailoredCV], list[str]]:
        results: list[TailoredCV] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_job = {
                executor.submit(
                    self._process_one, job, idx, master_cv, format_cv, config, ctx
                ): job
                for idx, job in enumerate(jobs, start=1)
            }
            for future in as_completed(future_to_job):
                job = future_to_job[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(f"  Tailored CV for: {job.title} at {job.company}")
                except Exception as exc:
                    msg = f"{job.title} at {job.company}: {exc}"
                    errors.append(msg)
                    logger.warning(f"  Failed to tailor CV: {msg}")

        results.sort(key=lambda r: r.job.company.lower())
        return results, errors

    def _process_one(
        self,
        job: JobPosting,
        idx: int,
        master_cv: str,
        format_cv: str,
        config: JobFinderConfig,
        ctx: AgentRunContext,
    ) -> TailoredCV:
        cv_content = tailor_cv(
            job=job,
            master_cv=master_cv,
            format_cv=format_cv,
            client=self._claude,
            model=config.claude_model,
        )

        dry = ctx.dry_run or config.dry_run
        output_path = ""

        if dry:
            _print_cv(job, cv_content, idx)
        else:
            output_path = _save_docx(cv_content, job, config.role, config.output_dir, idx)

        self._seen_cache.add(job)
        self._seen_cache.save()

        return TailoredCV(job=job, cv_content=cv_content, output_path=output_path)

    # ------------------------------------------------------------------
    # Referral pipeline
    # ------------------------------------------------------------------

    def _find_referrals(
        self,
        tailored: list[TailoredCV],
        config: JobFinderConfig,
    ) -> dict[str, CompanyReferralResult]:
        tavily_key = os.environ.get("REFERRAL_FINDER_TAVILY_API_KEY", "")
        if not tavily_key:
            logger.warning(
                "Referral search skipped: REFERRAL_FINDER_TAVILY_API_KEY is not set."
            )
            unique = list(dict.fromkeys(
                t.job.company for t in tailored if t.job.company and t.job.company != "Unknown"
            ))
            return {
                c: CompanyReferralResult(c, [], "REFERRAL_FINDER_TAVILY_API_KEY not set")
                for c in unique
            }

        github_token = os.environ.get("REFERRAL_FINDER_GITHUB_TOKEN", "")
        smtp_domain = os.environ.get("REFERRAL_FINDER_SMTP_FROM_DOMAIN", "example.com")
        your_name = os.environ.get("REFERRAL_FINDER_YOUR_NAME", "")
        your_background = os.environ.get("REFERRAL_FINDER_YOUR_BACKGROUND", "")
        location = os.environ.get("REFERRAL_FINDER_LOCATION", "Israel")
        university = os.environ.get("REFERRAL_FINDER_UNIVERSITY", "Bar Ilan University")
        roles = ["Software Engineer", "Senior Engineer", "Tech Lead", "Staff Engineer"]

        # Build a company → job-title map so each email mentions the specific role
        company_role: dict[str, str] = {}
        for t in tailored:
            if t.job.company and t.job.company != "Unknown":
                company_role.setdefault(t.job.company, t.job.title)

        results: dict[str, CompanyReferralResult] = {}
        for company, job_title in company_role.items():
            logger.info(f"  Searching referral contacts for: {company}")
            try:
                candidates = find_candidates(
                    company=company,
                    roles=roles,
                    api_key=tavily_key,
                    max_results=5,
                    location=location,
                )
                contacts: list[OutreachResult] = []
                with ThreadPoolExecutor(max_workers=3) as ex:
                    futures = {
                        ex.submit(
                            self._referral_one,
                            person, your_name, your_background,
                            location, university,
                            github_token, smtp_domain, config.claude_model,
                            job_title,
                        ): person
                        for person in candidates
                    }
                    for f in as_completed(futures):
                        try:
                            contacts.append(f.result())
                        except Exception as exc:
                            logger.warning(f"    Contact enrichment failed: {exc}")

                contacts.sort(key=lambda c: (c.email_result.email is None, c.person.name))
                results[company] = CompanyReferralResult(company, contacts)
                logger.info(f"    Found {len(contacts)} contact(s) at {company}")
            except Exception as exc:
                logger.warning(f"  Referral search failed for {company}: {exc}")
                results[company] = CompanyReferralResult(company, [], str(exc))

        return results

    def _referral_one(
        self,
        person,
        your_name: str,
        your_background: str,
        location: str,
        university: str,
        github_token: str,
        smtp_domain: str,
        model: str,
        target_role: str = "",
    ) -> OutreachResult:
        email_result = find_email(person, github_token, smtp_domain)
        message = generate_referral_email(
            person=person,
            your_name=your_name,
            your_background=your_background,
            client=self._claude,
            model=model,
            location=location,
            university=university,
            snippet=person.role_hint,
            target_role=target_role,
        )
        return OutreachResult(person, email_result, message)

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    def _send_email(
        self,
        tailored: list[TailoredCV],
        config: JobFinderConfig,
        referral_results: dict[str, CompanyReferralResult],
    ) -> None:
        from shared.config import OrchestraConfig
        from shared.notifications import NotificationService

        attachments = [Path(t.output_path) for t in tailored if t.output_path]
        if not attachments:
            return

        try:
            global_config = OrchestraConfig.from_env()
            notifications = NotificationService.from_config(global_config)
        except Exception as exc:
            logger.warning(f"Could not initialise notification service: {exc}")
            return

        subject = f"Job Finder: {len(tailored)} CV(s) for \"{config.role}\""
        body = _build_email_body(tailored, config, referral_results)

        try:
            notifications.send_email_with_attachments(subject, body, attachments)
            logger.info(f"Results emailed with {len(attachments)} attachment(s).")
        except Exception as exc:
            logger.warning(f"Failed to send email: {exc}")


# ---------------------------------------------------------------------------
# Email body builder
# ---------------------------------------------------------------------------

def _build_email_body(
    tailored: list[TailoredCV],
    config: JobFinderConfig,
    referral_results: dict[str, CompanyReferralResult],
) -> str:
    divider = "━" * 60
    lines: list[str] = []

    # Header
    tags = [config.location, "/".join(config.job_types)]
    if config.remote:
        tags.append("REMOTE")
    if config.startup:
        tags.append("STARTUPS ONLY")
    lines += [
        f"Search: {config.role} | {' | '.join(tags)}",
        f"Date: {datetime.date.today().strftime('%Y-%m-%d')}",
        "",
        divider,
        "JOBS FOUND  (tailored CV attached for each)",
        divider,
        "",
    ]

    for i, t in enumerate(tailored, start=1):
        cv_file = Path(t.output_path).name if t.output_path else "(dry-run)"
        lines += [
            f"[{i}]  {t.job.title} — {t.job.company}",
            f"     Type: {t.job.job_type or 'unknown'}  |  Startup: {'Yes' if t.job.is_startup else 'No'}",
            f"     URL: {t.job.url}",
            f"     CV:  {cv_file}",
            "",
        ]

    if referral_results:
        lines += [
            divider,
            "REFERRAL CONTACTS",
            divider,
            "",
        ]
        for company, result in referral_results.items():
            if result.error:
                lines += [f"### {company}  — search failed: {result.error}", ""]
                continue

            count = len(result.contacts)
            lines += [f"### {company}  ({count} contact{'s' if count != 1 else ''} found)", ""]

            if not result.contacts:
                lines += ["  No contacts found. Try searching LinkedIn directly.", ""]
                continue

            for contact in result.contacts:
                p = contact.person
                er = contact.email_result
                email_line = (
                    f"  Email: {er.email}  (via {er.source}, confidence: {er.confidence})"
                    if er.email
                    else "  Email: NOT FOUND — connect via LinkedIn first"
                )
                lines += [
                    f"  {p.name} — {p.role_hint or 'Engineer'}",
                    f"  LinkedIn: {p.linkedin_url}",
                    email_line,
                    "",
                    "  Suggested message:",
                    "  " + "─" * 50,
                ]
                for msg_line in contact.generated_message.splitlines():
                    lines.append(f"  {msg_line}")
                lines += ["  " + "─" * 50, ""]

    lines += [divider, "Tailored CV files are attached.", divider]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _read_cv_file(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() == ".docx":
        return read_docx_text(str(p))
    return p.read_text(encoding="utf-8")


def _sanitize(text: str, max_len: int = 40) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s[:max_len]


def _output_filename(company: str, role: str, idx: int) -> str:
    date_str = datetime.date.today().strftime("%Y%m%d")
    return f"{_sanitize(company)}_{_sanitize(role)}_{date_str}_{idx:02d}.docx"


def _save_docx(cv_content: str, job: JobPosting, role: str, output_dir: str, idx: int) -> str:
    filename = _output_filename(job.company, role, idx)
    output_path = str(Path(output_dir) / filename)
    markdown_to_docx(cv_content, output_path)
    logger.info(f"    Saved: {output_path}")
    return output_path


def _print_cv(job: JobPosting, cv_content: str, idx: int) -> None:
    divider = "=" * 70
    print(f"\n{divider}")
    print(f"DRY RUN — CV #{idx}: {job.title} at {job.company}")
    print(f"URL: {job.url}")
    print(f"Startup: {job.is_startup} | Type: {job.job_type}")
    print(divider)
    print(cv_content)
    print(divider)


def _print_referral_results(referral_results: dict[str, CompanyReferralResult]) -> None:
    if not referral_results:
        return
    divider = "=" * 70
    print(f"\n{divider}")
    print("DRY RUN — REFERRAL CONTACTS")
    print(divider)
    for company, result in referral_results.items():
        if result.error:
            print(f"\n### {company} — search failed: {result.error}")
            continue
        print(f"\n### {company}  ({len(result.contacts)} contact(s))")
        for contact in result.contacts:
            p = contact.person
            er = contact.email_result
            email_str = er.email or "NOT FOUND"
            print(f"  {p.name} | {email_str} | {p.linkedin_url}")
            print(f"  {contact.generated_message[:200]}...")
    print(f"\n{divider}")


def _cv_to_dict(t: TailoredCV) -> dict:
    return {
        "title": t.job.title,
        "company": t.job.company,
        "url": t.job.url,
        "is_startup": t.job.is_startup,
        "job_type": t.job.job_type,
        "output_path": t.output_path,
        "cv_preview": t.cv_content[:200],
    }
