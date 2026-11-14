from sqlalchemy.orm import Session

from app.models import Alerte, Gravite, TypeAlerte


def creer_alerte(
    db: Session,
    *,
    chantier_id: str,
    type: TypeAlerte,
    gravite: Gravite,
    titre: str,
    detail: str | None = None,
    lien_type: str | None = None,
    lien_id: str | None = None,
) -> Alerte:
    alerte = Alerte(
        chantier_id=chantier_id,
        type=type,
        gravite=gravite,
        titre=titre,
        detail=detail,
        lien_type=lien_type,
        lien_id=lien_id,
    )
    db.add(alerte)
    return alerte


def gravite_depassement(pourcentage: float) -> Gravite:
    """Plus le dépassement par rapport au métré est grand, plus l'alerte est grave."""
    if pourcentage >= 20:
        return Gravite.CRITIQUE
    if pourcentage >= 5:
        return Gravite.ATTENTION
    return Gravite.INFO
