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
1. О СЕБЕ — 3 to 5 sentences. Hook the hiring manager. Mention years of experience
   and core expertise. Only include achievements with numbers if the candidate explicitly provided them.
2. ОПЫТ РАБОТЫ — reverse-chronological. For each role:
   - Header: Company name | Position | Date range (MM.YYYY – MM.YYYY or "по настоящее время")
   - 3 to 6 bullet points per role
3. КЛЮЧЕВЫЕ НАВЫКИ — comma-separated list, grouped by category if useful
4. ОБРАЗОВАНИЕ — institution, degree, year
{gender_note}
BULLET POINT RULES (most important):
- Every bullet: Action verb (past tense Russian) + Task/scope + Result (with metric IF the candidate provided one)
- ONLY use numbers, percentages, and metrics that were EXPLICITLY stated by the candidate in their input.
  DO NOT invent or estimate any figures. If no metric was given, describe the scope or outcome in words.
- Approved strong action verbs: Увеличил, Сократил, Разработал, Внедрил, Оптимизировал,
  Запустил, Руководил, Автоматизировал, Реструктурировал, Обучил, Привлёк, Снизил,
  Ускорил, Выстроил, Масштабировал, Согласовал, Закрыл, Провёл, Настроил, Интегрировал
- FORBIDDEN vague bullets: "Занимался различными задачами", "Помогал команде",
  "Выполнял поручения", "Участвовал в проектах", "Работал над задачами"
- FORBIDDEN: inventing metrics not present in the input ("увеличил на 25%", "сократил на 15%"
  unless the candidate explicitly stated these numbers)

LENGTH & FORMAT:
- Target ~800-1000 words total (≈1 A4 page)
- No tables, no columns — plain text suitable for copy-paste to hh.ru
- Section headers in ALL CAPS
- Use "•" as bullet character
- Dates must be consistent and plausible (end date ≥ start date, no future dates)

CAREER TRANSITION RULES:
- Compare the candidate's desired_position with their work experience roles.
- If past roles differ significantly from the target position, emphasize transferable skills in bullet points.
- Reframe responsibilities to highlight relevance to the target role. For example:
  - Sales experience for a CSM role → emphasize client relationship management, retention, needs analysis
  - Developer experience for a PM role → emphasize technical understanding, cross-team communication, requirement analysis
  - Teacher experience for an HR role → emphasize training, assessment, communication skills
- Do NOT fabricate experience. Only reframe what was actually provided by the candidate.
- The professional summary ("О себе") should bridge past experience with the target role naturally.

LANGUAGE:
- Write entirely in Russian
- Professional, formal tone
- No first-person pronouns ("я", "мой") — use impersonal action verbs
- If candidate data contains placeholders in square brackets like [УКАЖИТЕ ЦИФРУ/ФАКТ] — omit that part or rephrase without specific numbers. NEVER include square brackets in the final resume.
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


async def reformulate_achievement(raw_text: str, role: str = "", company: str = "") -> str:
    """Reformulate casual achievement text into professional resume bullet style."""
    context_parts = []
    if role:
        context_parts.append(f"Должность: {role}")
    if company:
        context_parts.append(f"Компания: {company}")
    context = "\n".join(context_parts)

    return await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты — эксперт по резюме. Пользователь описал своё достижение разговорным языком. "
            "Перепиши его в профессиональном стиле для резюме.\n\n"
            "Правила:\n"
            "- Используй прошедшее время, безличную форму (без «я»).\n"
            "- Начни с сильного глагола действия (Увеличил, Сократил, Разработал, Внедрил, Оптимизировал и т.д.).\n"
            "- Сохрани ВСЕ числа и проценты из исходного текста в точности.\n"
            "- НЕ придумывай новые цифры или метрики.\n"
            "- Пиши на русском языке.\n"
            "- Верни только переформулированный текст достижения, без пояснений."
        ),
        user=(
            f"{context}\n\nДостижение кандидата (разговорный стиль):\n{raw_text}\n\n"
            "Перепиши в профессиональном стиле для резюме:"
        ),
        temperature=0.3,
        max_tokens=300,
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


async def validate_resume_data(interview_data: dict) -> list[str]:
    """Check if interview data contains coherent, real-looking professional content.

    Returns a list of human-readable warnings (in Russian). Empty list means data looks valid.
    Uses gpt-4o-mini for speed/cost.
    """
    import json as _json

    check_data = {
        "summary": interview_data.get("summary", ""),
        "work_experiences": [
            {
                "company": j.get("company", ""),
                "role": j.get("role", ""),
                "responsibilities": j.get("responsibilities", ""),
                "achievements": j.get("achievements", ""),
            }
            for j in (interview_data.get("work_experiences") or [])
        ],
        "skills": interview_data.get("skills", []),
        "education": interview_data.get("education", ""),
    }

    raw = await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты проверяешь данные резюме на достоверность и осмысленность. "
            "Пользователь заполнял поля, и нужно определить, содержат ли они реальную профессиональную информацию, "
            "а не тестовый мусор, бессмыслицу или шуточные ответы.\n\n"
            "Проверь:\n"
            "1. company — выглядит ли название компании как реальное (не 'asdf', 'тест', 'компания', 'хз' и т.п.)?\n"
            "2. role — выглядит ли должность реально?\n"
            "3. responsibilities — описывают ли реальные рабочие обязанности, а не бессмыслицу или шутки?\n"
            "4. summary — связный профессиональный текст, а не случайный набор слов?\n\n"
            "Верни ТОЛЬКО валидный JSON-объект с одним ключом 'issues' — массив строк на русском языке. "
            "Каждая строка — конкретная проблема. Если проблем нет — верни пустой массив. "
            "Пример: {\"issues\": [\"Название компании выглядит ненастоящим: 'протирал штаны'\", "
            "\"Обязанности не описывают реальную работу\"]}"
        ),
        user=_json.dumps(check_data, ensure_ascii=False),
        temperature=0.1,
        max_tokens=400,
    )
    result = _parse_json_dict(raw)
    issues = result.get("issues", [])
    return [str(i) for i in issues] if isinstance(issues, list) else []


async def parse_skills_from_text(text: str) -> list[str]:
    """Extract individual skills from free-form text using AI.

    Handles comma-separated lists, numbered lists, bullet points,
    plain sentences, and any other format the user might paste.
    Falls back to regex split if AI fails.
    """
    raw = await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты помогаешь составлять резюме. Пользователь вводит свои навыки в произвольном формате. "
            "Извлеки из текста список отдельных навыков — программы, технологии, методологии, инструменты, "
            "профессиональные компетенции. Каждый элемент списка — отдельный навык (не предложение). "
            "Верни ТОЛЬКО валидный JSON-массив строк, без лишнего текста и без markdown-обёртки.\n"
            "Примеры:\n"
            "Вход: 'Python, SQL, Excel' → [\"Python\", \"SQL\", \"Excel\"]\n"
            "Вход: '1. Python\\n2. SQL\\n3. Excel' → [\"Python\", \"SQL\", \"Excel\"]\n"
            "Вход: 'Знаю Python и SQL, умею в Excel и Jira' → [\"Python\", \"SQL\", \"Excel\", \"Jira\"]"
        ),
        user=text,
        temperature=0.1,
        max_tokens=400,
    )
    skills = _parse_json_list(raw)
    if not skills:
        import re
        parts = re.split(r"[,;\n•\-–—]+", text)
        skills = [p.strip() for p in parts if p.strip() and len(p.strip()) < 60]
    return skills


async def check_education_completeness(text: str) -> str | None:
    """Return a clarifying question if education text is missing key details, or None if complete enough.

    Checks for: university/institution name, graduation year, degree level.
    Uses gpt-4o-mini for speed/cost.
    """
    text_lower = text.strip().lower()
    # If the user explicitly says "no education" — accept as-is
    if text_lower in ("нет", "no", "не помню", "-", "—"):
        return None

    raw = await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты помогаешь составлять резюме. Пользователь ввёл информацию об образовании. "
            "Проверь, содержит ли текст три ключевых элемента:\n"
            "1. Название учебного заведения (ВУЗ, колледж, школа)\n"
            "2. Год окончания (или период обучения)\n"
            "3. Уровень/степень (бакалавр, магистр, специалист, среднее специальное и т.п.) "
            "или направление/специальность\n\n"
            "Если все три элемента присутствуют или текст явно говорит об отсутствии образования — "
            "верни ТОЛЬКО слово: OK\n"
            "Если чего-то не хватает — верни ОДИН короткий уточняющий вопрос на русском (на «Вы»), "
            "чтобы получить недостающие детали. Вопрос должен заканчиваться фразой: "
            "'Или напишите «ок», чтобы оставить как есть.'\n"
            "Верни только вопрос или OK, без лишнего текста."
        ),
        user=f"Текст об образовании: {text}",
        temperature=0.3,
        max_tokens=200,
    )
    result = raw.strip()
    if result.upper() in ("OK", "ОК"):
        return None
    return result


async def check_responsibilities_quality(text: str, role: str = "") -> str | None:
    """Return a clarifying question if responsibilities are too vague, or None if good enough.

    Uses gpt-4o-mini for speed/cost.
    """
    role_context = f" для должности «{role}»" if role else ""
    raw = await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты эксперт по резюме. Пользователь описал свои обязанности" + role_context + ". "
            "Оцени, достаточно ли конкретно описаны обязанности, или они слишком расплывчатые и общие. "
            "Примеры расплывчатых формулировок: «работал с клиентами», «выполнял задачи», "
            "«участвовал в проектах», «решал вопросы», «занимался различными задачами».\n\n"
            "Если обязанности описаны слишком обще — задай ОДИН конкретный уточняющий вопрос на русском "
            "(обращение на «Вы»), чтобы помочь кандидату раскрыть детали: инструменты, масштаб, конкретные задачи.\n"
            "Если обязанности достаточно конкретны — верни пустую строку.\n\n"
            "Верни ТОЛЬКО вопрос или пустую строку, без лишнего текста."
        ),
        user=f"Обязанности:\n{text}",
        temperature=0.3,
        max_tokens=200,
    )
    result = raw.strip()
    return result if result else None


async def check_skills_relevance(skills: list[str], desired_position: str) -> list[str] | None:
    """Return a list of suggested skills if current skills seem misaligned with target position, or None.

    Uses gpt-4o-mini for speed/cost.
    """
    raw = await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты эксперт по подбору персонала. Пользователь указал навыки для резюме. "
            "Сравни их с целевой позицией и определи, не хватает ли важных навыков.\n\n"
            "Если навыки в целом подходят для позиции — верни пустой JSON-массив [].\n"
            "Если есть явные пробелы (не хватает ключевых навыков для этой роли) — верни JSON-массив "
            "из 3–5 конкретных навыков, которые стоит добавить. Навыки должны быть конкретными "
            "(инструменты, технологии, методологии), а не общими словами.\n\n"
            "Верни ТОЛЬКО валидный JSON-массив строк, без лишнего текста."
        ),
        user=(
            f"Целевая позиция: {desired_position}\n"
            f"Указанные навыки: {', '.join(skills)}"
        ),
        temperature=0.2,
        max_tokens=300,
    )
    suggestions = _parse_json_list(raw)
    # Filter out skills that the user already has (case-insensitive)
    existing_lower = {s.lower() for s in skills}
    suggestions = [s for s in suggestions if s.lower() not in existing_lower]
    return suggestions if suggestions else None


async def generate_summary_help(
    position: str,
    name: str = "",
    work_experiences: list | None = None,
) -> str:
    """Generate a draft 'О себе' section based on the user's profile data.

    Leaves [PLACEHOLDER] markers for numbers and specifics the user should fill in.
    """
    context_parts = [f"Желаемая позиция: {position}"]
    if name:
        context_parts.append(f"Имя: {name}")
    if work_experiences:
        last_job = work_experiences[0]
        role = last_job.get("role", "") or last_job.get("position", "")
        company = last_job.get("company", "")
        if role or company:
            context_parts.append(f"Последнее место работы: {role} в {company}")
    context = "\n".join(context_parts)

    return await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты помогаешь составлять раздел «О себе» для резюме на hh.ru. "
            "Напиши черновик в 3–4 предложениях. Тон: профессиональный, конкретный. "
            "Там, где нужны конкретные цифры или факты — оставь метку [УКАЖИТЕ ЦИФРУ/ФАКТ]. "
            "Не используй местоимения «я», «мой». Пиши на русском. "
            "Верни только текст раздела, без заголовков и пояснений."
        ),
        user=f"Данные кандидата:\n{context}\n\nНапиши черновик раздела «О себе»:",
        temperature=0.6,
        max_tokens=250,
    )


# ---------------------------------------------------------------------------
# Intent classifier for interview text input
# ---------------------------------------------------------------------------

_INTENT_SYSTEM = (
    "Ты классификатор намерений пользователя в чат-боте для составления резюме. "
    "Пользователь отвечает на вопрос интервью. Определи его намерение.\n\n"
    "Варианты:\n"
    "- direct_answer — пользователь отвечает на вопрос по существу (даже кратко)\n"
    "- help_request — просит пример, помощь, не знает что писать (\"пример\", \"помогите\", \"не знаю\")\n"
    "- generate_for_me — просит бота придумать/сгенерировать/написать за него "
    "(\"подумай\", \"придумай\", \"напиши за меня\", \"сгенерируй\", \"что обычно пишут\")\n"
    "- question — задаёт вопрос о процессе (\"зачем это?\", \"что тут писать?\", \"а можно пропустить?\")\n"
    "- skip — хочет пропустить этот шаг (\"пропустить\", \"дальше\", \"потом\", \"не хочу\")\n\n"
    "ВАЖНО:\n"
    "- Короткие ответы типа \"нет\", \"не помню\" — это direct_answer, НЕ skip.\n"
    "- Если пользователь описывает свой опыт, навыки, достижения — это direct_answer.\n"
    "- Если пользователь просит бота ПРИДУМАТЬ контент на основе должности — это generate_for_me.\n\n"
    "Верни JSON: {\"intent\": \"<одно из пяти значений>\"}"
)


async def classify_intent(
    text: str,
    current_field: str,
    context: dict | None = None,
) -> str:
    """Classify user intent in interview context.

    Returns one of: direct_answer, help_request, generate_for_me, question, skip.
    On any error returns 'direct_answer' (graceful fallback).
    """
    try:
        ctx_parts = [f"Текущий вопрос: {current_field}"]
        if context:
            if context.get("role"):
                ctx_parts.append(f"Должность: {context['role']}")
            if context.get("company"):
                ctx_parts.append(f"Компания: {context['company']}")
            if context.get("desired_position"):
                ctx_parts.append(f"Желаемая позиция: {context['desired_position']}")

        user_prompt = f"Контекст: {'; '.join(ctx_parts)}\nОтвет пользователя: {text}"

        raw = await _chat(
            model="gpt-4o-mini",
            system=_INTENT_SYSTEM,
            user=user_prompt,
            temperature=0,
            max_tokens=80,
        )
        result = _parse_json_dict(raw)
        intent = result.get("intent", "direct_answer")
        valid = {"direct_answer", "help_request", "generate_for_me", "question", "skip"}
        return intent if intent in valid else "direct_answer"
    except Exception as exc:
        logger.warning("Intent classification failed: %s", exc)
        return "direct_answer"


# ---------------------------------------------------------------------------
# Generate content for a specific interview field
# ---------------------------------------------------------------------------

async def generate_field_content(field: str, context: dict) -> str:
    """Generate draft content for a specific interview field.

    field: 'responsibilities', 'achievements', 'company', 'summary', etc.
    context: dict with 'role', 'company', 'desired_position', 'dates', etc.
    """
    role = context.get("role") or context.get("desired_position") or "специалист"
    company = context.get("company", "")

    if field == "responsibilities":
        system = (
            "Ты помогаешь составлять резюме. Сгенерируй типичные обязанности для указанной должности. "
            "Напиши 4–6 конкретных пунктов через точку с запятой. Тон: профессиональный. "
            "Пиши на русском. Не используй местоимения. Только текст обязанностей, без пояснений."
        )
        user = f"Должность: {role}" + (f", компания: {company}" if company else "")
    elif field == "achievements":
        system = (
            "Ты помогаешь составлять резюме. Сгенерируй 2–3 примера достижений для указанной должности. "
            "Где нужны конкретные цифры — оставь метку [ЧИСЛО]. НИКОГДА не придумывай цифры. "
            "Формат: каждое достижение с новой строки, начинай с глагола прошедшего времени. "
            "Пиши на русском."
        )
        user = f"Должность: {role}" + (f", компания: {company}" if company else "")
    elif field == "summary":
        # Delegate to existing function
        return await generate_summary_help(
            position=context.get("desired_position", role),
            name=context.get("full_name", ""),
            work_experiences=context.get("work_experiences"),
        )
    else:
        system = (
            "Ты помогаешь составлять резюме. Пользователь просит помочь заполнить поле. "
            "Дай краткий пример того, что можно написать. Пиши на русском."
        )
        user = f"Поле: {field}, должность: {role}"

    return await _chat(
        model="gpt-4o-mini",
        system=system,
        user=user,
        temperature=0.6,
        max_tokens=500,
    )


# ---------------------------------------------------------------------------
# Parse free-form work experience
# ---------------------------------------------------------------------------

async def parse_work_experience_freeform(text: str) -> dict:
    """Parse free-form work experience description into structured fields.

    Returns dict with keys: company, role, dates, responsibilities, achievements.
    Missing fields have value None.
    On failure returns {}.
    """
    raw = await _chat(
        model="gpt-4o-mini",
        system=(
            "Ты извлекаешь структурированные данные об опыте работы из свободного текста. "
            "Извлеки следующие поля:\n"
            "- company: название компании\n"
            "- role: должность\n"
            "- dates: период работы (как написал пользователь)\n"
            "- responsibilities: обязанности (перечисли через точку с запятой)\n"
            "- achievements: достижения (если упомянуты)\n\n"
            "Если поле явно отсутствует в тексте — поставь null. "
            "НЕ придумывай данные, которых нет в тексте. "
            "Верни ТОЛЬКО валидный JSON-объект, без markdown-обёртки."
        ),
        user=text,
        temperature=0.1,
        max_tokens=800,
    )
    result = _parse_json_dict(raw)
    if not result:
        return {}
    # Normalize: ensure all expected keys exist
    fields = ("company", "role", "dates", "responsibilities", "achievements")
    return {k: result.get(k) for k in fields}
