"""
AI service — async wrappers around OpenAI API.

Models:
  gpt-4o-mini — fast/cheap tasks (skill suggestions, clarifying questions)
  gpt-4o      — generation tasks (resume generation, improvement, parsing)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from bot.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_RETRYABLE = (APITimeoutError, RateLimitError)
_MAX_RETRIES = 2


async def _chat(
    *,
    model: str,
    messages: list[dict] | None = None,
    system: str | None = None,
    user: str | None = None,
    temperature: float = 0.4,
    max_tokens: int = 2000,
    response_format: dict[str, str] | None = None,
) -> str:
    """Send a chat completion request with up to _MAX_RETRIES retries on transient errors.

    Accepts either:
      - messages: explicit list of message dicts
      - system + user: shorthand that builds the messages list
    """
    client = _get_client()

    if messages is None:
        if system is None or user is None:
            raise ValueError("Either 'messages' or both 'system' and 'user' must be provided.")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    kwargs: dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if response_format:
        kwargs["response_format"] = response_format

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 2):  # attempts: 1, 2, 3
        try:
            response = await client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except _RETRYABLE as exc:
            last_exc = exc
            logger.warning("OpenAI transient error (attempt %d/%d): %s", attempt, _MAX_RETRIES + 1, exc)
            if attempt == _MAX_RETRIES + 1:
                break
        except APIError as exc:
            logger.error("OpenAI API error: %s", exc)
            raise

    raise last_exc  # type: ignore[misc]


def _parse_json_list(raw: str) -> list[str]:
    """Parse a JSON array from the model response, tolerating markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [str(item) for item in result]
    except json.JSONDecodeError:
        pass
    # Fallback: try to extract a JSON array with a search
    import re
    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return [str(item) for item in result]
        except json.JSONDecodeError:
            pass
    logger.warning("Could not parse JSON list from response: %r", raw[:200])
    return []


def _parse_json_dict(raw: str) -> dict:
    """Parse a JSON object from the model response, tolerating markdown fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        result = json.loads(raw)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    import re
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    logger.warning("Could not parse JSON dict from response: %r", raw[:200])
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def detect_gender(full_name: str) -> str:
    """Returns 'male' or 'female' based on Russian full name."""
    response = await _chat(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Определи пол человека по имени. Отвечай только одним словом: male или female."},
            {"role": "user", "content": full_name}
        ],
        max_tokens=10,
        temperature=0,
    )
    result = response.strip().lower()
    return "female" if "female" in result else "male"


async def get_skill_suggestions(profession: str, existing_skills: list[str]) -> list[str]:
    """Return 10-15 relevant tools/technologies for *profession*.

    Uses gpt-4o-mini at low temperature for fast, consistent results.
    Already-known skills are excluded to avoid duplicates.
    """
    system = (
        "You are an expert career consultant and technical recruiter. "
        "Your task: given a job title/profession, return a JSON array of 10 to 15 "
        "specific tools, technologies, methodologies, or hard skills that are most "
        "commonly required or valued for that role in the current job market. "
        "Rules:\n"
        "- Return ONLY a valid JSON array of strings, no extra text.\n"
        "- Include concrete tool/technology names (e.g. 'Figma', 'Python', 'Jira'), not vague concepts.\n"
        "- Do NOT include any skill already listed in the existing_skills array.\n"
        "- Skills should be in the same language as the profession label (Russian names for Russian roles).\n"
        "- Prefer tools with high market demand."
    )
    existing_str = ", ".join(existing_skills) if existing_skills else "none"
    user = (
        f"Profession: {profession}\n"
        f"Already listed skills (exclude these): [{existing_str}]\n\n"
        "Return 10-15 relevant tools/skills as a JSON array."
    )
    raw = await _chat(
        model="gpt-4o-mini",
        system=system,
        user=user,
        temperature=0.2,
        max_tokens=600,
    )
    return _parse_json_list(raw)


async def suggest_position_titles(profile_data: dict) -> list[str]:
    """Suggest 3 market-relevant Russian job titles based on the candidate's profile.

    Returns a list of exactly 3 strings.
    """
    system = (
        "You are a senior Russian HR specialist and career coach. "
        "Based on the candidate profile provided, suggest exactly 3 job titles "
        "that best match their experience and skills. "
        "Rules:\n"
        "- Titles must be real, commonly used titles on hh.ru (the main Russian job board).\n"
        "- Write titles in Russian.\n"
        "- Order from most to least fitting.\n"
        "- Return ONLY a valid JSON array of 3 strings, no extra text."
    )
    user = f"Candidate profile:\n{json.dumps(profile_data, ensure_ascii=False, indent=2)}"
    raw = await _chat(
        model="gpt-4o-mini",
        system=system,
        user=user,
        temperature=0.3,
        max_tokens=200,
    )
    titles = _parse_json_list(raw)
    return titles[:3] if titles else ["Специалист", "Эксперт", "Менеджер"]


async def parse_resume_file(text: str) -> dict:
    """Parse raw resume text and return a structured dict.

    Expected output keys:
      name, contacts (dict), summary, work_experiences (list), skills (list),
      education (list), languages (list), certifications (list)
    """
    system = (
        "You are a resume parsing engine. Extract structured data from the raw resume text. "
        "Return a single valid JSON object with these keys:\n"
        "  name: string (full name)\n"
        "  contacts: object with optional keys: phone, email, telegram, linkedin, city\n"
        "  summary: string (professional summary / objective, if present)\n"
        "  work_experiences: array of objects with keys:\n"
        "    company (string), position (string), start_date (string), end_date (string or 'по настоящее время'),\n"
        "    description (string), achievements (array of strings)\n"
        "  skills: array of strings\n"
        "  education: array of objects with keys: institution, degree, field, year_end\n"
        "  languages: array of objects with keys: language, level\n"
        "  certifications: array of strings\n\n"
        "Rules:\n"
        "- Return ONLY valid JSON, no markdown fences, no extra text.\n"
        "- If a field is absent, use null or empty array.\n"
        "- Preserve original language of the resume (Russian or English).\n"
        "- Normalize dates to 'MM.YYYY' format where possible."
    )
    user = f"Resume text to parse:\n\n{text}"
    raw = await _chat(
        model="gpt-4o",
        system=system,
        user=user,
        temperature=0.1,
        max_tokens=3000,
    )
    result = _parse_json_dict(raw)
    # Ensure required keys exist with sensible defaults
    result.setdefault("name", "")
    result.setdefault("contacts", {})
    result.setdefault("summary", "")
    result.setdefault("work_experiences", [])
    result.setdefault("skills", [])
    result.setdefault("education", [])
    result.setdefault("languages", [])
    result.setdefault("certifications", [])
    return result


async def evaluate_parsed_resume(profile_data: dict) -> str:
    """Critique the parsed resume from the ATS point of view."""
    system = (
        "You are an ATS-expert and strict HR manager. The user uploaded their resume. "
        "Review the parsed JSON data. Identify 2-3 specific weaknesses out of these: "
        "1. Lack of numbers/metrics in work experience. "
        "2. Vague responsibilities. "
        "3. Missing key skills. "
        "Write a short, friendly message in Russian using formal 'Вы' address. "
        "Praise what's good, but clearly point out the problems. "
        "Keep it under 4 sentences. Do NOT rewrite the resume, just give the critique."
    )
    user = f"Parsed resume data:\n\n{json.dumps(profile_data, ensure_ascii=False)}"
    return await _chat(
        model="gpt-4o-mini",
        system=system,
        user=user,
        temperature=0.3,
        max_tokens=300,
    )


def _build_resume_system_prompt(gender: str = "male") -> str:
    """Build the resume generation system prompt with gender-appropriate О СЕБЕ instructions."""
    gender_note = ""
    if gender == "female":
        gender_note = (
            "\nGENDER NOTE for О СЕБЕ section:\n"
            "- Use feminine adjective forms for the candidate: "
            "«Опытная специалист», «Ответственная», «Квалифицированная», etc.\n"
            "- Action verbs in О СЕБЕ should also be feminine where applicable.\n"
        )
    else:
        gender_note = (
            "\nGENDER NOTE for О СЕБЕ section:\n"
            "- Use masculine adjective forms for the candidate: "
            "«Опытный специалист», «Ответственный», «Квалифицированный», etc.\n"
        )

    return f"""\
You are a professional Russian-language resume writer specialising in hh.ru.
Your task: write a complete, ATS-optimised resume for the given position.

STRICT QUALITY RULES — follow all of them without exception:

STRUCTURE (in this exact order):
1. О СЕБЕ — 3 to 5 sentences. Hook the hiring manager. Mention years of experience,
   core expertise, and 1-2 key achievements with numbers.
2. ОПЫТ РАБОТЫ — reverse-chronological. For each role:
   - Header: Company name | Position | Date range (MM.YYYY – MM.YYYY or "по настоящее время")
   - 3 to 6 bullet points per role
3. КЛЮЧЕВЫЕ НАВЫКИ — comma-separated list, grouped by category if useful
4. ОБРАЗОВАНИЕ — institution, degree, year
{gender_note}
BULLET POINT RULES (most important):
- Every bullet: Action verb (past tense Russian) + Task/scope + Measurable result + Tools used
- TARGET: ≥70% of bullets must contain a specific number, %, time period, or money amount
- Approved strong action verbs: Увеличил, Сократил, Разработал, Внедрил, Оптимизировал,
  Запустил, Руководил, Автоматизировал, Реструктурировал, Обучил, Привлёк, Снизил,
  Ускорил, Выстроил, Масштабировал, Согласовал, Закрыл, Провёл, Настроил, Интегрировал
- FORBIDDEN vague bullets: "Занимался различными задачами", "Помогал команде",
  "Выполнял поручения", "Участвовал в проектах", "Работал над задачами"
- If no exact metric is available, use relative improvement ("на 30%+") or scope
  ("для команды из 15 человек", "на рынке 5 регионов")

LENGTH & FORMAT:
- Target ~800-1000 words total (≈1 A4 page)
- No tables, no columns — plain text suitable for copy-paste to hh.ru
- Section headers in ALL CAPS
- Use "•" as bullet character
- Dates must be consistent and plausible (end date ≥ start date, no future dates)

LANGUAGE:
- Write entirely in Russian
- Professional, formal tone
- No first-person pronouns ("я", "мой") — use impersonal action verbs
"""


async def generate_resume(profile_data: dict, position_title: str, gender: str = "male") -> str:
    """Generate a full professional resume text ready for hh.ru.

    Follows strict quality rules defined in _build_resume_system_prompt().
    Returns formatted plain text.
    """
    system_prompt = _build_resume_system_prompt(gender)
    user = (
        f"Desired position: {position_title}\n\n"
        f"Candidate profile data:\n{json.dumps(profile_data, ensure_ascii=False, indent=2)}\n\n"
        "Write a complete resume following all quality rules. "
        "Make sure ≥70% of experience bullets contain specific metrics or numbers. "
        "Return only the resume text, no extra commentary."
    )
    return await _chat(
        model="gpt-4o",
        system=system_prompt,
        user=user,
        temperature=0.5,
        max_tokens=2500,
    )


async def improve_resume(current_resume: str, instruction: str) -> str:
    """Improve an existing resume according to the user's instruction.

    Examples of instructions:
      "сделай короче", "формальнее", "перепиши опыт в Яндекс", "добавь больше цифр"
    Returns the improved resume text.
    """
    system = (
        "You are a professional Russian-language resume editor. "
        "The user will provide their current resume and an instruction for how to improve it. "
        "Apply the instruction faithfully while preserving the overall structure and quality. "
        "Maintain all quality rules:\n"
        "- Keep ≥70% of bullets with specific metrics if they exist; add placeholders if needed.\n"
        "- Keep strong action verbs (Увеличил, Разработал, Внедрил, etc.).\n"
        "- Remove vague bullets if found.\n"
        "- Keep the resume in Russian, formal tone.\n"
        "- The О СЕБЕ section header must remain 'О СЕБЕ' (not SUMMARY).\n"
        "- Return ONLY the improved resume text, no commentary."
    )
    user = (
        f"Instruction: {instruction}\n\n"
        f"Current resume:\n{current_resume}"
    )
    return await _chat(
        model="gpt-4o",
        system=system,
        user=user,
        temperature=0.5,
        max_tokens=2500,
    )


async def clarify_achievement(vague_text: str, attempt: int) -> str:
    """Generate a clarifying question to elicit specific metrics from the user.

    attempt=1 — general open question about the result
    attempt=2 — suggest specific measurable formats
    attempt=3 — accept as-is, return empty string

    Returns the question text, or "" if we should accept the answer.
    """
    if attempt >= 3:
        return ""

    if attempt == 1:
        system = (
            "You are a resume coach. The user described a work achievement in vague terms. "
            "Ask ONE short, friendly question in Russian using formal 'Вы' address to find out "
            "the specific result or impact. "
            "Keep the question under 2 sentences. Return only the question."
        )
        user = f"Vague achievement: {vague_text}\n\nAsk a clarifying question to get a measurable result."
    else:  # attempt == 2
        system = (
            "You are a resume coach. The user still hasn't given specific numbers for their achievement. "
            "Ask a follow-up question in Russian using formal 'Вы' address that suggests concrete formats "
            "they could use: percentages, absolute numbers, time saved, money saved/earned, team size, etc. "
            "Give 2-3 specific examples as options. Keep it friendly and under 3 sentences. "
            "Return only the question."
        )
        user = (
            f"Vague achievement: {vague_text}\n\n"
            "They didn't give numbers yet. Suggest specific measurable formats and ask them to pick one."
        )

    return await _chat(
        model="gpt-4o-mini",
        system=system,
        user=user,
        temperature=0.4,
        max_tokens=200,
    )


# ---------------------------------------------------------------------------
# Compatibility aliases used by the interview and resume handlers
# ---------------------------------------------------------------------------


async def suggest_skills(profession: str) -> list[str]:
    """Alias: return skill suggestions for a profession (no existing skills filter)."""
    return await get_skill_suggestions(profession, existing_skills=[])


async def generate_resume_text(interview_data: dict, gender: str = "male") -> str:
    """Build a full resume from raw FSM interview data dict.

    Assembles a profile_data dict compatible with generate_resume() and calls it.
    """
    profile_data = {
        "name": interview_data.get("full_name", ""),
        "city": interview_data.get("city", ""),
        "summary": interview_data.get("summary", ""),
        "work_experiences": interview_data.get("work_experiences", []),
        "skills": interview_data.get("skills", []),
        "education": interview_data.get("education", ""),
        "extras": interview_data.get("extras", ""),
    }
    position_title = interview_data.get("desired_position", "Специалист")
    return await generate_resume(profile_data, position_title, gender=gender)


async def edit_resume(current_content: str, command: str, user_input: str) -> str:
    """Apply an editing command to an existing resume.

    Commands:
      shorter        — make the resume shorter
      formal         — make the tone more formal
      rewrite_block  — rewrite the block named in user_input
      add_skill      — append user_input skills to the skills section
    """
    command_map = {
        "shorter": "Сделай резюме короче, убери лишнее, сохрани всю важную информацию.",
        "formal": "Сделай тон более официальным и формальным.",
        "rewrite_block": f"Перепиши блок «{user_input}» полностью, сохранив факты.",
        "add_skill": f"Добавь следующие навыки в раздел навыков: {user_input}.",
    }
    instruction = command_map.get(command, user_input or command)
    return await improve_resume(current_content, instruction)


async def adapt_resume_for_position(current_content: str, position: str) -> str:
    """Adapt an existing resume for a new target position."""
    instruction = (
        f"Адаптируй резюме для позиции «{position}»: "
        "скорректируй заголовок и раздел «О себе», "
        "расставь акценты в опыте и навыках так, чтобы они соответствовали этой роли."
    )
    return await improve_resume(current_content, instruction)
