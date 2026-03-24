"""
Resume service — text formatting utilities for resume output.

Responsible for:
  - Composing the final resume document with header (name + contacts)
    prepended to the AI-generated body.
  - Truncating resume content for safe display inside Telegram messages.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_CONTACT_LABELS: dict[str, str] = {
    "phone":    "Тел.:",
    "email":    "Email:",
    "telegram": "Telegram:",
    "linkedin": "LinkedIn:",
    "city":     "Город:",
}


def _build_header(profile_data: dict) -> str:
    """Build the name + contacts block for the top of the resume."""
    lines: list[str] = []

    name = (profile_data.get("name") or "").strip()
    if name:
        lines.append(name.upper())

    contacts: dict = profile_data.get("contacts") or {}
    contact_parts: list[str] = []
    for key in ("city", "phone", "email", "telegram", "linkedin"):
        value = (contacts.get(key) or "").strip()
        if value:
            label = _CONTACT_LABELS.get(key, key.capitalize() + ":")
            contact_parts.append(f"{label} {value}")

    if contact_parts:
        lines.append("  |  ".join(contact_parts))

    return "\n".join(lines)


def format_resume_text(profile_data: dict, resume_content: str) -> str:
    """Compose the final resume string ready for copy-paste to hh.ru.

    Layout:
      [NAME]
      [contacts line]
      (blank line)
      [AI-generated body]

    Args:
        profile_data:   Structured profile dict (name, contacts, …).
        resume_content: AI-generated resume body (from ai_service.generate_resume).

    Returns:
        A single string with the complete resume.
    """
    header = _build_header(profile_data)
    body = (resume_content or "").strip()

    if header:
        return f"{header}\n\n{body}"
    return body


def format_resume_preview(content: str, max_chars: int = 3000) -> str:
    """Return a safe-for-Telegram preview of the resume.

    Telegram messages have a 4096-character limit; we use a conservative
    default of 3000 to leave room for surrounding UI text.

    If *content* fits within *max_chars*, it is returned unchanged.
    Otherwise, the text is cut at the last newline before the limit
    (to avoid splitting mid-line) and a truncation notice is appended.

    Args:
        content:   Full resume text.
        max_chars: Maximum allowed length (default 3000).

    Returns:
        The original string or a truncated version ending with "…".
    """
    if not content:
        return ""

    if len(content) <= max_chars:
        return content

    # Attempt to cut at a newline boundary for cleaner output
    cutoff = content.rfind("\n", 0, max_chars)
    if cutoff <= 0:
        cutoff = max_chars

    truncated = content[:cutoff].rstrip()
    return truncated + "\n\n…\n_(показан фрагмент; полный текст скопируйте из следующего сообщения)_"
