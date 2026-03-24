from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    choosing_path = State()
    coaching_questions = State()
    waiting_desired_position = State()
    waiting_name = State()
    waiting_contacts = State()   # email + phone in one message or separate
    waiting_city = State()
    upload_resume_prompt = State()
    processing_upload = State()


class InterviewStates(StatesGroup):
    summary = State()
    work_experience_company = State()
    work_experience_role = State()
    work_experience_dates = State()
    work_experience_responsibilities = State()
    work_experience_achievements = State()
    work_experience_confirm = State()
    skills_input = State()
    education_input = State()
    extras_input = State()


class ResumeStates(StatesGroup):
    viewing_draft = State()
    editing = State()
    selecting_position_title = State()
