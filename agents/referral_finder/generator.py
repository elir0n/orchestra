from __future__ import annotations

import logging

import anthropic

from agents.referral_finder.search.linkedin_search import Person

logger = logging.getLogger(__name__)


def generate_referral_email(
    person: Person,
    your_name: str,
    your_background: str,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5-20251001",
    location: str = "Israel",
    university: str = "",
    snippet: str = "",
) -> str:
    """
    Generate a personalized referral request email using Claude.
    Returns the plain-text email body (no subject line).
    """
    system_prompt = (
        "You are a professional email writer helping a job seeker request a referral. "
        "Write concise, warm, and specific emails. "
        "Rules: under 150 words, no subject line, no generic openers like "
        "'I hope this message finds you well' or 'I came across your profile' as the very first words. "
        "Output only the email body — no explanations, no labels, no subject."
    )

    # Build shared-context hints to warm up the connection
    shared_context_lines = []
    if location:
        shared_context_lines.append(f"- Both sender and recipient are based in {location} (mention this as a natural shared connection)")
    if university and snippet and university.lower() in snippet.lower():
        shared_context_lines.append(f"- Recipient appears to have studied at {university}, same as the sender — mention this briefly as a personal connection")
    elif university:
        shared_context_lines.append(f"- Sender studied at {university} — mention only if it feels natural, do not force it")
    shared_context = "\n".join(shared_context_lines)

    user_prompt = f"""Write a referral request email with these details:

RECIPIENT:
- Name: {person.name}
- Role: {person.role_hint or "engineer"} at {person.company}
- LinkedIn: {person.linkedin_url}

SENDER:
- Name: {your_name}
- Background: {your_background}

SHARED CONTEXT (use to personalize the opening):
{shared_context}

The email should:
1. Open by leveraging the shared context above (shared country, possibly shared university)
2. Mention the sender's relevant background in 1-2 sentences
3. Make a clear, polite ask for a referral or a quick chat about open roles
4. Close naturally without sounding desperate

Write directly — no preamble."""

    try:
        message = client.messages.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.error(f"Claude API error generating email for {person.name}: {exc}")
        return f"[Email generation failed: {exc}]"
