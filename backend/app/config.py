from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://scoutmap:scoutmap@db:5432/scoutmap"
    app_title: str = "ScoutMap"

    class Config:
        env_file = ".env"


settings = Settings()
