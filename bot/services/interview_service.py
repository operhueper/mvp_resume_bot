"""
Interview service — business logic for the step-by-step profile interview.

Stages (6 total):
  1. summary       — target position & brief self-description
  2. experience    — work history (iterated per job)
  3. achievements  — key accomplishments per job
  4. skills        — tools & technologies
  5. education     — education & certifications
  6. contacts      — personal details & contacts
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stage metadata
# ---------------------------------------------------------------------------

STAGES = [
    "summary",
    "experience",
    "achievements",
    "skills",
    "education",
    "contacts",
]

STAGE_LABELS: dict[str, str] = {
    "summary":      "Резюме и цель",
    "experience":   "Опыт работы",
    "achievements": "Достижения",
    "skills":       "Навыки и инструменты",
    "education":    "Образование",
    "contacts":     "Контакты",
}


def get_progress_text(stage: str) -> str:
    """Return a human-readable progress string like 'Этап 2/6: Опыт работы'."""
    try:
        idx = STAGES.index(stage) + 1
    except ValueError:
        idx = "?"
    label = STAGE_LABELS.get(stage, stage.capitalize())
    return f"Этап {idx}/{len(STAGES)}: {label}"


# ---------------------------------------------------------------------------
# Interview questions
# ---------------------------------------------------------------------------

def get_stage_questions() -> dict[str, list[str]]:
    """Return all interview questions organised by stage.

    Each stage contains an ordered list of question strings.
    The bot iterates through them one by one, storing answers in state.
    """
    return {
        "summary": [
            "На какую должность вы хотите претендовать? "
            "(Например: «Продуктовый дизайнер», «Backend-разработчик Python», «Руководитель отдела продаж»)",

            "Кратко опишите себя как специалиста: сколько лет опыта, "
            "в какой сфере, чем занимались в последнее время? (2–4 предложения)",
        ],

        "experience": [
            "Назовите вашу последнюю или текущую компанию и должность. "
            "(Например: «ООО Яндекс, Senior Product Manager»)",

            "Какой период вы там работали? (Например: «03.2021 – по настоящее время» или «01.2019 – 12.2020»)",

            "Опишите ваши основные обязанности на этом месте (3–5 пунктов). "
            "Что вы делали каждый день / каждую неделю?",

            "Есть ли у вас другие места работы, которые стоит включить в резюме? "
            "Если да — напишите «да» и мы разберём их по очереди. Если нет — напишите «нет».",
        ],

        "achievements": [
            "Расскажите о вашем главном достижении на последнем месте работы. "
            "Постарайтесь указать конкретный результат: цифры, проценты, сроки.",

            "Есть ли ещё 1–2 достижения, которыми вы гордитесь? "
            "(Можно из любого места работы. Напишите «нет», если нечего добавить.)",
        ],

        "skills": [
            "Перечислите инструменты, программы и технологии, которыми вы владеете. "
            "Через запятую. (Например: Figma, Jira, SQL, Python, Notion, Excel)",

            "Есть ли у вас управленческие или методологические навыки? "
            "(Например: Agile, Scrum, управление командой, бюджетирование, переговоры. "
            "Напишите «нет», если нет.)",
        ],

        "education": [
            "Укажите ваше образование: ВУЗ, специальность и год окончания. "
            "(Например: «МГУ, Прикладная математика, 2016»)",

            "Есть ли у вас дополнительные курсы, сертификаты или специализации? "
            "(Например: «Яндекс Практикум, Data Analyst, 2022». Напишите «нет», если нет.)",
        ],

        "contacts": [
            "Как вас зовут? (Полное имя — Фамилия Имя Отчество или Фамилия Имя)",

            "В каком городе вы находитесь / ищете работу?",

            "Укажите контакты для связи: телефон и email. "
            "(Например: «+7 999 123-45-67, ivan@example.com»)",

            "Хотите добавить Telegram, LinkedIn или ссылку на портфолио? "
            "(Напишите через запятую или «нет»)",
        ],
    }


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------

def build_profile_from_state(interview_state: dict) -> dict:
    """Convert raw interview answers stored in state into structured profile_data.

    interview_state keys (all optional, populated progressively):
      target_position: str
      self_description: str
      work_experiences: list[dict]  — filled by experience/achievements stages
      skills: list[str]
      soft_skills: list[str]
      education: list[dict]
      certifications: list[str]
      name: str
      city: str
      phone: str
      email: str
      telegram: str
      linkedin: str

    Returns a dict suitable for ai_service.generate_resume() and
    ai_service.suggest_position_titles().
    """
    # ---- contacts ----
    contacts: dict[str, str] = {}
    for field in ("phone", "email", "telegram", "linkedin"):
        value = interview_state.get(field, "").strip()
        if value and value.lower() not in ("нет", "no", "-"):
            contacts[field] = value
    city = interview_state.get("city", "").strip()
    if city:
        contacts["city"] = city

    # ---- skills ----
    raw_skills: str | list = interview_state.get("skills", "")
    if isinstance(raw_skills, str):
        skills = [s.strip() for s in raw_skills.replace(";", ",").split(",") if s.strip()]
    else:
        skills = list(raw_skills)

    raw_soft: str | list = interview_state.get("soft_skills", "")
    if isinstance(raw_soft, str):
        if raw_soft.strip().lower() not in ("нет", "no", "-", ""):
            soft_skills = [s.strip() for s in raw_soft.replace(";", ",").split(",") if s.strip()]
        else:
            soft_skills = []
    else:
        soft_skills = list(raw_soft)

    all_skills = skills + soft_skills

    # ---- education ----
    education: list[dict] = interview_state.get("education", [])
    if not education:
        # try to parse from raw text stored during interview
        raw_edu = interview_state.get("education_raw", "").strip()
        if raw_edu and raw_edu.lower() not in ("нет", "no", "-"):
            education = [{"raw": raw_edu}]

    # ---- certifications ----
    certifications: list[str] = interview_state.get("certifications", [])
    if not certifications:
        raw_cert = interview_state.get("certifications_raw", "").strip()
        if raw_cert and raw_cert.lower() not in ("нет", "no", "-"):
            certifications = [c.strip() for c in raw_cert.split(",") if c.strip()]

    # ---- work experiences ----
    work_experiences: list[dict] = interview_state.get("work_experiences", [])

    return {
        "name": interview_state.get("name", "").strip(),
        "contacts": contacts,
        "target_position": interview_state.get("target_position", "").strip(),
        "summary": interview_state.get("self_description", "").strip(),
        "work_experiences": work_experiences,
        "skills": all_skills,
        "education": education,
        "certifications": certifications,
        "languages": interview_state.get("languages", []),
    }


# ---------------------------------------------------------------------------
# Static skill suggestions fallback
# ---------------------------------------------------------------------------

SKILL_SUGGESTIONS_BY_PROFESSION: dict[str, list[str]] = {
    # Product & Management
    "продукт": [
        "Jira", "Confluence", "Notion", "Miro", "Figma", "Amplitude",
        "SQL", "A/B тестирование", "Roadmapping", "User Story Mapping",
        "OKR", "Scrum", "Kanban", "Google Analytics", "Tableau",
    ],
    "менеджер проекта": [
        "Jira", "MS Project", "Confluence", "Trello", "Notion",
        "Scrum", "Agile", "Kanban", "PRINCE2", "Gantt", "MS Excel",
        "Slack", "Zoom", "бюджетирование", "управление рисками",
    ],
    "руководитель": [
        "1С", "MS Excel", "Power BI", "Jira", "Confluence",
        "OKR", "KPI", "бюджетирование", "стратегическое планирование",
        "управление командой", "Scrum", "деловые переговоры",
    ],

    # Design
    "дизайнер": [
        "Figma", "Adobe Photoshop", "Adobe Illustrator", "Sketch",
        "Adobe XD", "Principle", "Zeplin", "InVision", "After Effects",
        "Procreate", "Canva", "Тайпографика", "Прототипирование", "UX Research",
    ],
    "ux": [
        "Figma", "Sketch", "Adobe XD", "Miro", "Maze",
        "UserTesting", "Hotjar", "UX Research", "User Interview",
        "Wireframing", "Prototyping", "Usability Testing", "Axure", "Zeplin",
    ],

    # Development
    "python": [
        "Python", "Django", "FastAPI", "Flask", "PostgreSQL",
        "Redis", "Docker", "Kubernetes", "Git", "SQLAlchemy",
        "Celery", "pytest", "asyncio", "REST API", "CI/CD",
    ],
    "backend": [
        "Python / Node.js / Java / Go", "PostgreSQL", "MySQL", "Redis",
        "Docker", "Kubernetes", "REST API", "gRPC", "Git",
        "CI/CD", "Nginx", "RabbitMQ / Kafka", "Linux", "AWS / GCP",
    ],
    "frontend": [
        "JavaScript", "TypeScript", "React", "Vue.js", "Next.js",
        "HTML5", "CSS3 / SCSS", "Webpack", "Vite", "Git",
        "REST API", "Redux / Zustand", "Tailwind CSS", "Jest", "Figma",
    ],
    "разработчик": [
        "Git", "Docker", "Linux", "REST API", "PostgreSQL",
        "CI/CD", "Unit-тестирование", "Code Review", "Agile / Scrum",
    ],
    "devops": [
        "Kubernetes", "Docker", "Terraform", "Ansible", "Jenkins",
        "GitLab CI", "GitHub Actions", "Prometheus", "Grafana", "AWS",
        "GCP", "Linux", "Bash", "Python", "Nginx",
    ],

    # Data
    "аналитик данных": [
        "SQL", "Python", "pandas", "numpy", "Tableau",
        "Power BI", "Google Analytics", "Excel", "A/B тестирование",
        "Looker", "Redash", "Spark", "Airflow", "Jupyter Notebook",
    ],
    "data scientist": [
        "Python", "pandas", "scikit-learn", "TensorFlow", "PyTorch",
        "SQL", "Jupyter Notebook", "MLflow", "Docker", "Git",
        "Matplotlib / Seaborn", "Spark", "Airflow", "Hadoop",
    ],
    "аналитик": [
        "SQL", "Excel", "Power BI", "Tableau", "Python",
        "Google Analytics", "Яндекс Метрика", "A/B тестирование",
        "Jira", "Confluence", "Looker",
    ],

    # Marketing
    "маркетолог": [
        "Google Analytics", "Яндекс Метрика", "Яндекс Директ", "Google Ads",
        "Facebook Ads", "ВКонтакте Реклама", "SEO", "Email-маркетинг",
        "Mailchimp / Unisender", "CRM", "Tableau", "Excel", "Canva",
    ],
    "smm": [
        "ВКонтакте", "Instagram", "Telegram", "TikTok", "YouTube",
        "SMMplanner", "Canva", "Adobe Photoshop", "Reels / Shorts",
        "Таргетированная реклама", "Аналитика охватов", "Контент-план",
    ],
    "seo": [
        "Google Search Console", "Яндекс Вебмастер", "Ahrefs", "SEMrush",
        "Screaming Frog", "Key Collector", "HTML / CSS", "WordPress",
        "Google Analytics", "Яндекс Метрика", "Excel", "Python (парсинг)",
    ],

    # Sales & Account
    "продажи": [
        "CRM (Bitrix24 / AmoCRM / Salesforce)", "1С", "Excel",
        "Холодные звонки", "Переговоры", "Презентации", "KPI",
        "Воронка продаж", "B2B продажи", "Тендеры", "Delovye переговоры",
    ],
    "менеджер по продажам": [
        "AmoCRM", "Bitrix24", "1С:Торговля", "Excel",
        "Холодные звонки", "Переговоры", "Презентации PowerPoint",
        "Деловая переписка", "KPI", "Воронка продаж",
    ],

    # HR
    "hr": [
        "1С:ЗУП", "HeadHunter", "SuperJob", "Huntflow",
        "Excel", "Trello", "Психологическое тестирование",
        "Onboarding", "Performance Review", "HR-аналитика", "Slack",
    ],
    "рекрутер": [
        "HeadHunter (hh.ru)", "SuperJob", "LinkedIn", "Huntflow",
        "Boolean Search", "DISC / MBTI", "Интервью по компетенциям",
        "Excel", "Telegram", "ATS-системы", "Employer Branding",
    ],

    # Finance & Accounting
    "финансист": [
        "1С:Бухгалтерия", "Excel", "Power BI", "SAP",
        "МСФО", "РСБУ", "Финансовое моделирование", "Бюджетирование",
        "Управленческий учёт", "DCF", "Bloomberg",
    ],
    "бухгалтер": [
        "1С:Бухгалтерия 8", "1С:ЗУП", "Excel", "КонсультантПлюс",
        "Гарант", "СБИС", "ЭДО", "РСБУ", "Налоговый учёт",
        "Банк-клиент", "Казначейство",
    ],
}
