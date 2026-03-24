from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    choosing_path = State()
    coaching_questions = State()
    waiting_desired_position = State()
    waiting_name = State()
    # waiting_contacts removed — contacts collected manually by user after resume
    waiting_city = State()
    upload_resume_prompt = State()
    processing_upload = State()


class InterviewStates(StatesGroup):
    block_selection = State()  # navigation hub: choose which block to edit/fill
    generation_confirm = State()  # waiting for user to confirm after validation warning
    summary = State()
    work_experience_freeform = State()  # free-form WE input (alternative to step-by-step)
    work_experience_company = State()
    work_experience_role = State()
    work_experience_dates = State()
    work_experience_responsibilities = State()
    work_experience_achievements = State()
    achievements_confirm = State()  # waiting after AI reformulation shown
    work_experience_confirm = State()
    skills_input = State()
    education_input = State()
    extras_input = State()

class ImprovementStates(StatesGroup):
    reviewing_parsed_data = State()


class ResumeStates(StatesGroup):
    viewing_draft = State()
    editing = State()
    selecting_position_title = State()
