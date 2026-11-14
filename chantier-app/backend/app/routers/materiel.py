"""Demandes de matériaux, stock du chantier et inventaires."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Chantier,
    DemandeMateriau,
    Inventaire,
    Materiau,
    MouvementStock,
    Role,
    StatutDemande,
    Utilisateur,
)
from app.schemas import (
    DecisionIn,
    DemandeIn,
    DemandeOut,
    InventaireIn,
    InventaireOut,
    MouvementOut,
    ReceptionIn,
    SortieIn,
    StockLigne,
    VerificationIn,
    VerificationOut,
)
from app.security import bureau, can_access, current_user, get_chantier_for
from app.services import stock as svc
from app.services.pointage import aujourdhui

router = APIRouter(prefix="/api", tags=["Matériaux : demandes et stock"])


# --- Demandes -------------------------------------------------------------------


@router.post("/chantiers/{chantier_id}/demandes/verifier", response_model=VerificationOut)
def verifier(
    chantier_id: str, data: VerificationIn, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)
):
    """Aperçu avant envoi : combien il reste à demander par rapport au métré."""
    chantier = get_chantier_for(db, user, chantier_id)
    return svc.verifier_demande(db, chantier, data.ouvrage_id, data.materiau_id, data.quantite).as_dict()


@router.post("/chantiers/{chantier_id}/demandes", response_model=DemandeOut, status_code=201)
def creer(chantier_id: str, data: DemandeIn, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    demande = svc.creer_demande(
        db,
        user,
        chantier,
        ouvrage_id=data.ouvrage_id,
        materiau_id=data.materiau_id,
        quantite=data.quantite,
        motif_depassement=data.motif_depassement,
        commentaire=data.commentaire,
    )
    db.commit()
    return demande


@router.get("/chantiers/{chantier_id}/demandes", response_model=list[DemandeOut])
def lister(
    chantier_id: str,
    statut: StatutDemande | None = None,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(current_user),
):
    chantier = get_chantier_for(db, user, chantier_id)
    stmt = select(DemandeMateriau).where(DemandeMateriau.chantier_id == chantier.id)
    if statut:
        stmt = stmt.where(DemandeMateriau.statut == statut)
    return db.scalars(stmt.order_by(DemandeMateriau.numero.desc())).all()


@router.get("/demandes/a-valider", response_model=list[DemandeOut])
def a_valider(db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)):
    """Toutes les demandes hors métré qui attendent une décision, sur les chantiers de l'utilisateur."""
    stmt = (
        select(DemandeMateriau)
        .join(Chantier)
        .where(DemandeMateriau.statut == StatutDemande.EN_ATTENTE)
        .order_by(DemandeMateriau.cree_le)
    )
    if user.role == Role.CONDUCTEUR:
        stmt = stmt.where(Chantier.conducteur_id == user.id)
    return db.scalars(stmt).all()


def _demande(db: Session, user: Utilisateur, demande_id: str) -> DemandeMateriau:
    demande = db.get(DemandeMateriau, demande_id)
    if demande is None or not can_access(user, db.get(Chantier, demande.chantier_id)):
        raise HTTPException(404, "Demande introuvable")
    return demande


@router.post("/demandes/{demande_id}/decision", response_model=DemandeOut)
def decider(demande_id: str, data: DecisionIn, db: Session = Depends(get_db), user: Utilisateur = Depends(bureau)):
    demande = _demande(db, user, demande_id)
    svc.decider_demande(db, demande, user, data.approuver, data.commentaire)
    db.commit()
    return demande


@router.post("/demandes/{demande_id}/annuler", response_model=DemandeOut)
def annuler(demande_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    demande = _demande(db, user, demande_id)
    if user.role == Role.CHEF and demande.demandeur_id != user.id:
        raise HTTPException(403, "Vous ne pouvez annuler que vos propres demandes")
    svc.annuler_demande(db, demande, user)
    db.commit()
    return demande


@router.post("/demandes/{demande_id}/reception", response_model=DemandeOut)
def receptionner(
    demande_id: str, data: ReceptionIn, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)
):
    demande = _demande(db, user, demande_id)
    svc.receptionner(db, demande, user, data.quantite_livree, data.note)
    db.commit()
    return demande


# --- Stock ------------------------------------------------------------------


@router.get("/chantiers/{chantier_id}/stock", response_model=list[StockLigne])
def stock(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    lignes = []
    for materiau_id, quantite in svc.stock_theorique(db, chantier.id).items():
        m = db.get(Materiau, materiau_id)
        lignes.append(
            StockLigne(
                materiau=m, quantite=quantite, valeur=(quantite * Decimal(m.prix_unitaire)).quantize(Decimal("0.01"))
            )
        )
    return sorted(lignes, key=lambda line: line.materiau.nom)


@router.get("/chantiers/{chantier_id}/mouvements", response_model=list[MouvementOut])
def mouvements(
    chantier_id: str,
    materiau_id: str | None = None,
    limite: int = 200,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(current_user),
):
    chantier = get_chantier_for(db, user, chantier_id)
    stmt = select(MouvementStock).where(MouvementStock.chantier_id == chantier.id)
    if materiau_id:
        stmt = stmt.where(MouvementStock.materiau_id == materiau_id)
    return db.scalars(stmt.order_by(MouvementStock.cree_le.desc()).limit(min(limite, 1000))).all()


@router.post("/chantiers/{chantier_id}/sorties", response_model=MouvementOut, status_code=201)
def sortie(chantier_id: str, data: SortieIn, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    """Consommation sur un ouvrage, ou retour au dépôt."""
    chantier = get_chantier_for(db, user, chantier_id)
    mouvement = svc.sortir_du_stock(
        db,
        user,
        chantier,
        type=data.type,
        materiau_id=data.materiau_id,
        quantite=data.quantite,
        ouvrage_id=data.ouvrage_id,
        note=data.note,
    )
    db.commit()
    return mouvement


@router.get("/chantiers/{chantier_id}/inventaires", response_model=list[InventaireOut])
def inventaires(chantier_id: str, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    return db.scalars(
        select(Inventaire).where(Inventaire.chantier_id == chantier.id).order_by(Inventaire.cree_le.desc())
    ).all()


@router.post("/chantiers/{chantier_id}/inventaires", response_model=InventaireOut, status_code=201)
def inventaire(
    chantier_id: str, data: InventaireIn, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)
):
    chantier = get_chantier_for(db, user, chantier_id)
    jour = data.date or aujourdhui()
    if jour > aujourdhui():
        raise HTTPException(422, "La date d'inventaire ne peut pas être dans le futur")
    inv = svc.faire_inventaire(
        db,
        user,
        chantier,
        jour=jour,
        lignes=[(line.materiau_id, line.quantite_comptee) for line in data.lignes],
        note=data.note,
    )
    db.commit()
    return inv
