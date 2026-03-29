from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import anthropic

from agents.job_finder.config import JobFinderConfig
from agents.job_finder.cv_tailor.docx_writer import markdown_to_docx, read_docx_text
from agents.job_finder.cv_tailor.tailor import tailor_cv
from agents.job_finder.search.job_search import JobPosting, search_jobs
from shared.base_agent import BaseAgent
from shared.models import AgentResult, AgentRunContext, AgentStatus

logger = logging.getLogger(__name__)


@dataclass
class TailoredCV:
    job: JobPosting
    cv_content: str   # markdown from Claude
    output_path: str  # path to saved .docx (empty on dry-run)


class JobFinderAgent(BaseAgent[JobFinderConfig, list[dict]]):
    name = "job-finder"
    description = "Find jobs in Israel and tailor your CV to each one"
    version = "0.1.0"

    # ------------------------------------------------------------------
    # CLI wiring
    # ------------------------------------------------------------------

    @classmethod
    def build_arg_parser(cls, subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--master-cv", required=True, dest="master_cv",
            help="Path to your master CV file (text, markdown, or .docx)",
        )
        subparser.add_argument(
            "--format-cv", required=True, dest="format_cv",
            help="Path to format/template CV used as structural reference (text, markdown, or .docx)",
        )
        subparser.add_argument(
            "--role", required=True,
            help='Job role to search for (e.g. "Backend Engineer")',
        )
        subparser.add_argument(
            "--location", default="Israel",
            help='Job location (default: "Israel")',
        )
        subparser.add_argument(
            "--startup", action="store_true",
            help="Filter to startup companies only",
        )
        subparser.add_argument(
            "--job-type", default="full-time", dest="job_type",
            choices=["full-time", "part-time", "contract"],
            help="Employment type (default: full-time)",
        )
        subparser.add_argument(
            "--max-jobs", type=int, default=5, dest="max_jobs",
            help="Max number of jobs to process (default: 5)",
        )
        subparser.add_argument(
            "--output-dir", default="./tailored_cvs", dest="output_dir",
            help="Directory to save tailored CVs (default: ./tailored_cvs)",
        )
        subparser.add_argument(
            "--claude-model", default="claude-sonnet-4-6", dest="claude_model",
            help="Claude model for CV tailoring (default: claude-sonnet-4-6)",
        )

    @classmethod
    def config_from_args(cls, args: argparse.Namespace) -> JobFinderConfig:
        return JobFinderConfig(
            master_cv_path=args.master_cv,
            format_cv_path=args.format_cv,
            role=args.role,
            location=args.location,
            startup=args.startup,
            job_type=args.job_type,
            max_jobs=args.max_jobs,
            output_dir=args.output_dir,
            dry_run=getattr(args, "dry_run", False),
            claude_model=args.claude_model,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        self._claude = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(
        self, input: JobFinderConfig, ctx: AgentRunContext
    ) -> AgentResult[list[dict]]:
        config = self.config

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
            f"(startup={config.startup}, type={config.job_type})"
        )
        jobs = search_jobs(
            role=config.role,
            api_key=config.tavily_api_key,
            location=config.location,
            job_type=config.job_type,
            startup=config.startup,
            max_jobs=config.max_jobs,
        )

        if not jobs:
            return AgentResult(
                status=AgentStatus.FAILURE,
                errors=["No jobs found. Try different --role or relax --startup filter."],
            )

        logger.info(f"Found {len(jobs)} jobs. Tailoring CVs...")

        # --- Step 3: Prepare output directory ---
        dry = ctx.dry_run or config.dry_run
        if not dry:
            Path(config.output_dir).mkdir(parents=True, exist_ok=True)

        # --- Step 4: Tailor CVs concurrently ---
        tailored, errors = self._process_jobs(jobs, master_cv, format_cv, config, ctx)

        # --- Step 5: Email results ---
        if not dry and tailored:
            self._send_email(tailored, config)

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
                "cvs_tailored": len(tailored),
                "cvs_failed": len(errors),
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
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

        return TailoredCV(job=job, cv_content=cv_content, output_path=output_path)

    def _send_email(
        self,
        tailored: list[TailoredCV],
        config: JobFinderConfig,
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

        subject = f"Job Finder: {len(tailored)} tailored CV(s) for \"{config.role}\""
        lines = [f"Found {len(tailored)} job(s) matching your search.\n"]
        for t in tailored:
            lines.append(f"- {t.job.title} at {t.job.company}")
            lines.append(f"  {t.job.url}")
            lines.append("")
        body = "\n".join(lines)

        try:
            notifications.send_email_with_attachments(subject, body, attachments)
            logger.info(f"Results emailed with {len(attachments)} attachment(s).")
        except Exception as exc:
            logger.warning(f"Failed to send email: {exc}")


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _read_cv_file(path: str) -> str:
    """Read a CV file. Supports .docx (extracts text) and plain text/markdown."""
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


def _save_docx(
    cv_content: str,
    job: JobPosting,
    role: str,
    output_dir: str,
    idx: int,
) -> str:
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
