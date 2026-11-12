from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
class Settings(BaseSettings):
    app_name: str = "TIMBOT – AI Customer Support & Sales Automation"
    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./timbot.db"
    redis_url: str | None = None
    frontend_origins: str = "http://localhost:5173"
    ai_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_chat_model: str = "llama3.2:1b"
    ollama_embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = Field(default=768, gt=0)
    ai_request_timeout_seconds: float = Field(default=120.0, gt=0)
    ai_max_retries: int = Field(default=2, ge=0, le=5)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    @property
    def origins(self):
        return [item.strip() for item in self.frontend_origins.split(",") if item.strip()]
settings = Settings()
