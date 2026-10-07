"""Production start-up checks on the settings."""

import pytest
from cryptography.fernet import Fernet

from app.config import Settings


def _production(**overrides):
    values = {
        "environment": "production",
        "jwt_secret": "a-real-secret-for-production",
        "credentials_encryption_key": Fernet.generate_key().decode(),
        "cors_allowed_origins": "https://ross.ar,https://www.ross.ar",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_with_real_secrets_and_origins_starts():
    _production().validate_production_ready()


def test_production_refuses_the_dev_jwt_secret():
    with pytest.raises(RuntimeError, match="jwt_secret"):
        _production(jwt_secret="dev-only-secret-change-me").validate_production_ready()


def test_production_refuses_cors_open_to_any_origin():
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        _production(cors_allowed_origins="*").validate_production_ready()


def test_development_keeps_cors_open_by_default():
    settings = Settings(_env_file=None, environment="development")
    settings.validate_production_ready()
    assert settings.cors_origins == ["*"]


def test_origins_are_split_and_trimmed():
    settings = _production(cors_allowed_origins=" https://ross.ar , https://www.ross.ar ,")
    assert settings.cors_origins == ["https://ross.ar", "https://www.ross.ar"]
