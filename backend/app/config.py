from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://scoutmap:scoutmap@db:5432/scoutmap"
    app_title: str = "ScoutMap"

    # Auth
    allowed_emails: str = ""  # comma-separated seed list, e.g. "admin@example.com,*@myorg.org"
    session_expiry_hours: int = 72
    auth_code_expiry_minutes: int = 10

    # SMTP (optional — codes logged to console when not configured)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "scoutmap@localhost"
    smtp_use_tls: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
