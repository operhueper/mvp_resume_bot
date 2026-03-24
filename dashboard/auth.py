from itsdangerous import URLSafeTimedSerializer
import os

SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "fallback-secret-change-me")
serializer = URLSafeTimedSerializer(SECRET_KEY)


def create_session_token(username: str) -> str:
    return serializer.dumps(username, salt="session")


def verify_session_token(token: str) -> str | None:
    try:
        return serializer.loads(token, salt="session", max_age=86400 * 7)
    except Exception:
        return None
