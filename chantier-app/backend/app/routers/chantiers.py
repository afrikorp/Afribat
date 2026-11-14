"""Chantiers, ouvrages, métré (besoins en matériaux) et affectation des ouvriers."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Affectation,
    BesoinMateriau,
    Chantier,
    DemandeMateriau,
    Materiau,
    MouvementStock,
    Ouvrage,
    Ouvrier,
    Role,
    Utilisateur,
)
from app.schemas import (
    AffectationIn,
    BesoinIn,
    BesoinOut,
    BesoinUpdate,
    ChantierCreate,
    ChantierOut,
    ChantierUpdate,
    OuvrageIn,
    OuvrageOut,
    OuvrierOut,
    SuiviLigne,
)
from app.security import admin_only, bureau, current_user, get_chantier_for
from app.services import pointage as svc_pointage
from app.services import stock as svc_stock

router = APIRouter(prefix="/api/chantiers", tags=["Chantiers et métré"])


def _check_users(db: Session, chef_id: str | None, conducteur_id: str | None) -> None:
    for uid, role, label in ((chef_id, Role.CHEF, "chef de chantier"), (conducteur_id, Role.CONDUCTEUR, "conducteur")):
        if uid is None:
            continue
        user = db.get(Utilisateur, uid)
        if user is None or not user.actif or user.role not in (role, Role.ADMIN):
            raise HTTPException(422, f"Le {label} choisi n'est pas valide")


@router.get("", response_model=list[ChantierOut])
def lister(actifs: bool = True, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    stmt = select(Chantier).order_by(Chantier.nom)
    if actifs:
        stmt = stmt.where(Chantier.actif.is_(True))
    if user.role == Role.CONDUCTEUR:
        stmt = stmt.where(Chantier.conducteur_id == user.id)
    elif user.role == Role.CHEF:
        stmt = stmt.where(Chantier.chef_id == user.id)
    return db.scalars(stmt).all()


@router.post("", response_model=ChantierOut, status_code=201)
def creer(data: ChantierCreate, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)):
    _check_users(db, data.chef_id, data.conducteur_id)
    chantier = Chantier(**data.model_dump())
    if user.role == Role.CONDUCTEUR and chantier.conducteur_id is None:
        chantier.conducteur_id = user.id
    db.add(chantier)
    db.commit()
    return chantier


@router.get("/{chantier_id}", response_model=ChantierOut)
def detail(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    return get_chantier_for(db, user, chantier_id)


@router.patch("/{chantier_id}", response_model=ChantierOut)
def modifier(
    chantier_id: str, data: ChantierUpdate, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)
):
    chantier = get_chantier_for(db, user, chantier_id)
    changes = data.model_dump(exclude_unset=True)
    if user.role != Role.ADMIN and "conducteur_id" in changes:
        raise HTTPException(403, "Seul l'administrateur peut changer le conducteur de travaux")
    _check_users(db, changes.get("chef_id"), changes.get("conducteur_id"))
    for k, v in changes.items():
        setattr(chantier, k, v)
    db.commit()
    return chantier


@router.delete("/{chantier_id}", status_code=204)
def supprimer(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(admin_only)):
    chantier = get_chantier_for(db, user, chantier_id)
    if db.scalar(select(MouvementStock.id).where(MouvementStock.chantier_id == chantier.id).limit(1)):
        raise HTTPException(409, "Ce chantier a un historique de stock : archivez-le (actif = non) au lieu de le supprimer")
    db.delete(chantier)
    db.commit()


# --- Ouvrages -------------------------------------------------------------------


@router.get("/{chantier_id}/ouvrages", response_model=list[OuvrageOut])
def ouvrages(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    return db.scalars(
        select(Ouvrage).where(Ouvrage.chantier_id == chantier.id).order_by(Ouvrage.code, Ouvrage.nom)
    ).all()


@router.post("/{chantier_id}/ouvrages", response_model=OuvrageOut, status_code=201)
def creer_ouvrage(
    chantier_id: str, data: OuvrageIn, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)
):
    chantier = get_chantier_for(db, user, chantier_id)
    ouvrage = Ouvrage(chantier_id=chantier.id, **data.model_dump())
    db.add(ouvrage)
    db.commit()
    return ouvrage


def _ouvrage(db: Session, user: Utilisateur, chantier_id: str, ouvrage_id: str) -> Ouvrage:
    chantier = get_chantier_for(db, user, chantier_id)
    ouvrage = db.get(Ouvrage, ouvrage_id)
    if ouvrage is None or ouvrage.chantier_id != chantier.id:
        raise HTTPException(404, "Ouvrage introuvable")
    return ouvrage


@router.patch("/{chantier_id}/ouvrages/{ouvrage_id}", response_model=OuvrageOut)
def modifier_ouvrage(
    chantier_id: str,
    ouvrage_id: str,
    data: OuvrageIn,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(bureau),
):
    ouvrage = _ouvrage(db, user, chantier_id, ouvrage_id)
    for k, v in data.model_dump().items():
        setattr(ouvrage, k, v)
    db.commit()
    return ouvrage


@router.delete("/{chantier_id}/ouvrages/{ouvrage_id}", status_code=204)
def supprimer_ouvrage(
    chantier_id: str, ouvrage_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)
):
    ouvrage = _ouvrage(db, user, chantier_id, ouvrage_id)
    if db.scalar(select(DemandeMateriau.id).where(DemandeMateriau.ouvrage_id == ouvrage.id).limit(1)):
        raise HTTPException(409, "Des demandes de matériaux existent pour cet ouvrage : suppression impossible")
    db.delete(ouvrage)
    db.commit()


# --- Métré ------------------------------------------------------------------


def _besoin_out(b: BesoinMateriau) -> BesoinOut:
    return BesoinOut(
        id=b.id,
        ouvrage_id=b.ouvrage_id,
        materiau_id=b.materiau_id,
        quantite_nette=b.quantite_nette,
        taux_perte_pct=b.taux_perte_pct,
        note_calcul=b.note_calcul,
        quantite_autorisee=svc_stock.quantite_autorisee(b),
        materiau=b.materiau,
    )


@router.get("/{chantier_id}/metre", response_model=list[BesoinOut])
def metre(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    besoins = db.scalars(
        select(BesoinMateriau).join(Ouvrage).where(Ouvrage.chantier_id == chantier.id)
    ).all()
    return [_besoin_out(b) for b in besoins]


@router.post("/{chantier_id}/metre", response_model=BesoinOut, status_code=201)
def ajouter_besoin(
    chantier_id: str, data: BesoinIn, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)
):
    _ouvrage(db, user, chantier_id, data.ouvrage_id)
    if db.get(Materiau, data.materiau_id) is None:
        raise HTTPException(404, "Matériau introuvable")
    besoin = BesoinMateriau(**data.model_dump())
    db.add(besoin)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ce matériau est déjà au métré de cet ouvrage : modifiez la ligne existante") from None
    return _besoin_out(besoin)


def _besoin(db: Session, user: Utilisateur, chantier_id: str, besoin_id: str) -> BesoinMateriau:
    chantier = get_chantier_for(db, user, chantier_id)
    besoin = db.get(BesoinMateriau, besoin_id)
    if besoin is None or besoin.ouvrage.chantier_id != chantier.id:
        raise HTTPException(404, "Ligne de métré introuvable")
    return besoin


@router.patch("/{chantier_id}/metre/{besoin_id}", response_model=BesoinOut)
def modifier_besoin(
    chantier_id: str,
    besoin_id: str,
    data: BesoinUpdate,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(bureau),
):
    besoin = _besoin(db, user, chantier_id, besoin_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(besoin, k, v)
    db.commit()
    return _besoin_out(besoin)


@router.delete("/{chantier_id}/metre/{besoin_id}", status_code=204)
def supprimer_besoin(
    chantier_id: str, besoin_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)
):
    besoin = _besoin(db, user, chantier_id, besoin_id)
    db.delete(besoin)
    db.commit()


@router.get("/{chantier_id}/suivi-materiaux", response_model=list[SuiviLigne])
def suivi(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    return svc_stock.suivi_materiaux(db, chantier.id)


# --- Équipe -------------------------------------------------------------------


@router.get("/{chantier_id}/equipe", response_model=list[OuvrierOut])
def equipe(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    return svc_pointage.ouvriers_affectes(db, chantier.id)


@router.post("/{chantier_id}/equipe", status_code=201)
def affecter(
    chantier_id: str, data: AffectationIn, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)
):
    chantier = get_chantier_for(db, user, chantier_id)
    ouvrier = db.get(Ouvrier, data.ouvrier_id)
    if ouvrier is None or not ouvrier.actif:
        raise HTTPException(404, "Ouvrier introuvable")
    db.add(Affectation(chantier_id=chantier.id, **data.model_dump()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Cet ouvrier est déjà affecté à ce chantier") from None
    return {"ok": True}


@router.delete("/{chantier_id}/equipe/{ouvrier_id}", status_code=204)
def retirer(chantier_id: str, ouvrier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)):
    chantier = get_chantier_for(db, user, chantier_id)
    aff = db.scalar(
        select(Affectation).where(Affectation.chantier_id == chantier.id, Affectation.ouvrier_id == ouvrier_id)
    )
    if aff is None:
        raise HTTPException(404, "Affectation introuvable")
    db.delete(aff)
    db.commit()
