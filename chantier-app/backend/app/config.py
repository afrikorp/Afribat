"""Configuration de l'application, lue depuis les variables d'environnement (préfixe APP_)."""

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    # PostgreSQL en production, SQLite pour le développement local et les tests.
    database_url: str = "sqlite:///./chantier.db"

    # Clé de signature des jetons de connexion. À CHANGER en production.
    secret_key: str = "dev-only-change-me-dev-only-change-me"
    token_minutes: int = 60 * 12

    # Fuseau horaire utilisé pour « aujourd'hui » (pointage, alertes).
    timezone: str = "UTC"
    currency: str = "EUR"

    # Écart d'inventaire toléré (en % du stock théorique) avant une alerte de fuite.
    tolerance_inventaire_pct: float = 2.0
    # Nombre de jours pendant lesquels un chef de chantier peut encore corriger un pointage.
    jours_correction_pointage: int = 1

    # Origines autorisées pour le frontend en développement.
    cors_origins: list[str] = ["http://localhost:5173"]

    # Compte administrateur créé au premier démarrage s'il n'existe aucun utilisateur.
    admin_email: str = "admin@chantier.local"
    admin_password: str = "admin1234"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
