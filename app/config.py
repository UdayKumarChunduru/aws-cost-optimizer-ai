from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    aws_region: str = "us-east-1"

    # an instance is idle only if it is under BOTH thresholds
    idle_cpu_threshold: float = 5.0
    idle_network_threshold: int = 5_000_000
    idle_lookback_days: int = 7

    snapshot_max_age_days: int = 90

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    class Config:
        env_file = ".env"


settings = Settings()
