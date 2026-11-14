"""Catalogue des matériaux et fichier des ouvriers."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Materiau, Ouvrier, Utilisateur
from app.schemas import MateriauIn, MateriauOut, OuvrierIn, OuvrierOut
from app.security import bureau, current_user

router = APIRouter(prefix="/api", tags=["Matériaux et ouvriers"])


def _commit(db: Session, doublon: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, doublon) from None


@router.get("/materiaux", response_model=list[MateriauOut])
def materiaux(q: str | None = None, db: Session = Depends(get_db), _: Utilisateur = Depends(current_user)):
    stmt = select(Materiau).order_by(Materiau.nom)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Materiau.nom.ilike(like), Materiau.reference.ilike(like)))
    return db.scalars(stmt).all()


@router.post("/materiaux", response_model=MateriauOut, status_code=201)
def creer_materiau(data: MateriauIn, db: Session = Depends(get_db), _: Utilisateur = Depends(bureau)):
    m = Materiau(**data.model_dump())
    db.add(m)
    _commit(db, "Cette référence de matériau existe déjà")
    return m


@router.patch("/materiaux/{materiau_id}", response_model=MateriauOut)
def modifier_materiau(
    materiau_id: str, data: MateriauIn, db: Session = Depends(get_db), _: Utilisateur = Depends(bureau)
):
    m = db.get(Materiau, materiau_id)
    if m is None:
        raise HTTPException(404, "Matériau introuvable")
    for k, v in data.model_dump().items():
        setattr(m, k, v)
    _commit(db, "Cette référence de matériau existe déjà")
    return m


@router.get("/ouvriers", response_model=list[OuvrierOut])
def ouvriers(
    actifs: bool = True, db: Session = Depends(get_db), _: Utilisateur = Depends(current_user)
):
    stmt = select(Ouvrier).order_by(Ouvrier.nom, Ouvrier.prenom)
    if actifs:
        stmt = stmt.where(Ouvrier.actif.is_(True))
    return db.scalars(stmt).all()


@router.post("/ouvriers", response_model=OuvrierOut, status_code=201)
def creer_ouvrier(data: OuvrierIn, db: Session = Depends(get_db), _: Utilisateur = Depends(bureau)):
    o = Ouvrier(**data.model_dump())
    db.add(o)
    _commit(db, "Ce matricule est déjà utilisé")
    return o


@router.patch("/ouvriers/{ouvrier_id}", response_model=OuvrierOut)
def modifier_ouvrier(
    ouvrier_id: str, data: OuvrierIn, db: Session = Depends(get_db), _: Utilisateur = Depends(bureau)
):
    o = db.get(Ouvrier, ouvrier_id)
    if o is None:
        raise HTTPException(404, "Ouvrier introuvable")
    for k, v in data.model_dump().items():
        setattr(o, k, v)
    _commit(db, "Ce matricule est déjà utilisé")
    return o
