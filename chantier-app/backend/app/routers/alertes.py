from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (
    Affectation,
    Alerte,
    Chantier,
    DemandeMateriau,
    Gravite,
    Pointage,
    Role,
    StatutDemande,
    StatutPointage,
    Utilisateur,
)
from app.schemas import AlerteOut, TableauDeBord
from app.security import can_access, current_user
from app.services.pointage import aujourdhui

router = APIRouter(prefix="/api", tags=["Alertes et tableau de bord"])


def _mes_chantiers(user: Utilisateur):
    stmt = select(Chantier.id).where(Chantier.actif.is_(True))
    if user.role == Role.CONDUCTEUR:
        stmt = stmt.where(Chantier.conducteur_id == user.id)
    elif user.role == Role.CHEF:
        stmt = stmt.where(Chantier.chef_id == user.id)
    return stmt


@router.get("/alertes", response_model=list[AlerteOut])
def alertes(
    chantier_id: str | None = None,
    non_lues: bool = False,
    limite: int = 100,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(current_user),
):
    stmt = select(Alerte).where(Alerte.chantier_id.in_(_mes_chantiers(user)))
    if chantier_id:
        stmt = stmt.where(Alerte.chantier_id == chantier_id)
    if non_lues:
        stmt = stmt.where(Alerte.lue.is_(False))
    return db.scalars(stmt.order_by(Alerte.cree_le.desc()).limit(min(limite, 500))).all()


@router.post("/alertes/{alerte_id}/lue", response_model=AlerteOut)
def marquer_lue(alerte_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    alerte = db.get(Alerte, alerte_id)
    if alerte is None or not can_access(user, alerte.chantier):
        raise HTTPException(404, "Alerte introuvable")
    if user.role == Role.CHEF:
        # Le chef ne peut pas faire disparaître les alertes qui le concernent.
        raise HTTPException(403, "Seul le bureau peut clôturer une alerte")
    alerte.lue = True
    db.commit()
    return alerte


@router.get("/tableau-de-bord", response_model=TableauDeBord)
def tableau_de_bord(db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    mes = _mes_chantiers(user)
    count = lambda stmt: db.scalar(select(func.count()).select_from(stmt.subquery())) or 0  # noqa: E731
    return TableauDeBord(
        chantiers_actifs=count(mes),
        alertes_non_lues=count(select(Alerte.id).where(Alerte.chantier_id.in_(mes), Alerte.lue.is_(False))),
        alertes_critiques=count(
            select(Alerte.id).where(
                Alerte.chantier_id.in_(mes), Alerte.lue.is_(False), Alerte.gravite == Gravite.CRITIQUE
            )
        ),
        demandes_en_attente=count(
            select(DemandeMateriau.id).where(
                DemandeMateriau.chantier_id.in_(mes), DemandeMateriau.statut == StatutDemande.EN_ATTENTE
            )
        ),
        presents_aujourdhui=count(
            select(Pointage.id).where(
                Pointage.chantier_id.in_(mes),
                Pointage.date == aujourdhui(),
                Pointage.statut.in_(
                    [StatutPointage.PRESENT, StatutPointage.RETARD, StatutPointage.DEMI_JOURNEE]
                ),
            )
        ),
        ouvriers_affectes=count(select(Affectation.ouvrier_id).where(Affectation.chantier_id.in_(mes)).distinct()),
        devise=get_settings().currency,
    )
