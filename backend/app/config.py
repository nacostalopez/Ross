from pydantic_settings import BaseSettings

# Secrets that ship with an insecure but functional default so local dev
# works with zero setup. If ENVIRONMENT=production and one of these is still
# equal to its dev default, Settings.validate() below refuses to start —
# better a loud crash at boot than silently running prod on a known secret.
_INSECURE_DEFAULTS = {
    "jwt_secret": "dev-only-secret-change-me",
    "credentials_encryption_key": "Qxsu0Kb675R0dWY-WeAE-1hvcXLTQj74_pJrlsr_kG4=",
}


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://escal:escal@localhost:5432/escal"

    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # Used to build links in outgoing emails (e.g. invite links).
    frontend_url: str = "http://localhost:3100"

    # Fernet key for encrypting store_credentials at rest. Dev-only default —
    # generate a real one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credentials_encryption_key: str = "Qxsu0Kb675R0dWY-WeAE-1hvcXLTQj74_pJrlsr_kG4="

    environment: str = "development"  # "development" | "test" | "production"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"

    def validate_production_ready(self) -> None:
        """Refuse to boot with dev-only secrets when ENVIRONMENT=production."""
        if self.environment != "production":
            return
        leaked = [
            field for field, insecure_value in _INSECURE_DEFAULTS.items()
            if getattr(self, field) == insecure_value
        ]
        if leaked:
            raise RuntimeError(
                "Refusing to start with ENVIRONMENT=production while these settings "
                f"still hold their insecure development defaults: {', '.join(leaked)}. "
                "Set real values via environment variables."
            )


settings = Settings()
