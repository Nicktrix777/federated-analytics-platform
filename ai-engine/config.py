from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # LLM provider selection
    llm_provider: str = Field(
        default="openai", description="LLM provider: 'openai' or 'anthropic'"
    )

    # OpenAI settings
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_model: str = Field(default="gpt-4o", description="OpenAI model name")


    # Anthropic settings
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    anthropic_model: str = Field(
        default="claude-3-5-sonnet-20241022", description="Claude model name"
    )

    # PostgreSQL metadata DB
    postgres_meta_host: str = Field(default="localhost")
    postgres_meta_port: int = Field(default=5432)
    postgres_meta_db: str = Field(default="analytics_meta")
    postgres_meta_user: str = Field(default="meta_user")
    postgres_meta_password: str = Field(default="meta_pass_2024")

    # Trino connection (for schema introspection)
    trino_host: str = Field(default="localhost", description="Trino coordinator hostname")
    trino_port: int = Field(default=8080, description="Trino HTTP port")

    # Service port
    port: int = Field(default=8082)

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
