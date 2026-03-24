from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    openai_api_key: str
    admin_telegram_id: int
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str
    admin_secret: str = "change_me"

    class Config:
        env_file = ".env"


settings = Settings()
