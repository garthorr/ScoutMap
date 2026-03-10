from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://fundraiser:fundraiser@db:5432/fundraiser"
    app_title: str = "Fundraising App"

    class Config:
        env_file = ".env"


settings = Settings()
