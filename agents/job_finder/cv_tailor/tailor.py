from __future__ import annotations

import logging

import anthropic

from agents.job_finder.search.job_search import JobPosting

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert CV writer and ATS optimization specialist.
Your task is to tailor a master CV for a specific job posting.

Rules — follow ALL of them without exception:
1. Output a single-page CV in markdown. Do not add a preamble or explanation.
2. Match the structure and section headings of the provided FORMAT CV exactly.
3. Maximize keyword overlap with the job description for ATS optimization.
   Incorporate the job's exact phrasing where it honestly applies.
4. Keep only the most relevant projects (2-4 max). Remove unrelated ones.
5. Rewrite every bullet point to highlight the skills and outcomes the job cares about.
   Use strong action verbs. Quantify impact wherever possible.
6. Emphasize technologies and concepts explicitly mentioned in the job description.
7. ATS formatting rules: simple headings (##), bullet points (-), no tables,
   no columns, no icons, no special characters except standard punctuation.
8. Do NOT invent experience that is not in the master CV.
   You may reframe and emphasize, but never fabricate.
9. Output ONLY the markdown CV — no commentary before or after.
"""


def tailor_cv(
    job: JobPosting,
    master_cv: str,
    format_cv: str,
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-6",
) -> str:
    """
    Call Claude to tailor master_cv to the given job posting.
    Returns the tailored CV as a markdown string.
    Raises on API error (caller handles per-job errors).
    """
    user_prompt = f"""\
<JOB_POSTING>
Title: {job.title}
Company: {job.company}
Location: {job.location}
Type: {job.job_type or "unspecified"}
URL: {job.url}

Job Description:
{job.description[:6000]}
</JOB_POSTING>

<MASTER_CV>
{master_cv}
</MASTER_CV>

<FORMAT_CV>
{format_cv}
</FORMAT_CV>

Using the structure of FORMAT_CV as your template, tailor the MASTER_CV for the job above.
Output only the final markdown CV."""

    message = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text.strip()
