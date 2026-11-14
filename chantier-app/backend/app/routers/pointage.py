import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Pointage, Role, Utilisateur
from app.schemas import FeuilleJour, PointageIn, PointageResultat, Recap
from app.security import current_user, get_chantier_for
from app.services import pointage as svc
from app.services.erreurs import ErreurMetier

router = APIRouter(prefix="/api/chantiers/{chantier_id}/pointage", tags=["Pointage"])


@router.get("", response_model=FeuilleJour)
def feuille(
    chantier_id: str, jour: date | None = None, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)
):
    """La feuille de pointage d'un jour : tous les ouvriers affectés et leur pointage s'il existe."""
    chantier = get_chantier_for(db, user, chantier_id)
    jour = jour or svc.aujourdhui()
    pointages = {
        p.ouvrier_id: p
        for p in db.scalars(select(Pointage).where(Pointage.chantier_id == chantier.id, Pointage.date == jour))
    }
    ouvriers = svc.ouvriers_affectes(db, chantier.id, jour)
    try:
        svc.verifier_date(user, jour)
        modifiable = True
    except ErreurMetier:
        modifiable = False
    return {
        "date": jour,
        "modifiable": modifiable,
        "lignes": [{"ouvrier": o, "pointage": pointages.get(o.id)} for o in ouvriers],
    }


@router.post("", response_model=PointageResultat)
def pointer(chantier_id: str, data: PointageIn, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)):
    chantier = get_chantier_for(db, user, chantier_id)
    resultat = svc.pointer(
        db,
        user,
        chantier,
        jour=data.date,
        lignes=[svc.LignePointage(**line.model_dump()) for line in data.lignes],
        latitude=data.latitude,
        longitude=data.longitude,
    )
    db.commit()
    return {
        "enregistres": resultat.enregistres,
        "refus": resultat.refus,
        "hors_zone": resultat.hors_zone,
        "distance_m": resultat.distance_m,
    }


def _periode(du: date | None, au: date | None) -> tuple[date, date]:
    au = au or svc.aujourdhui()
    du = du or au.replace(day=1)
    return du, au


@router.get("/recap", response_model=Recap)
def recap(
    chantier_id: str,
    du: date | None = None,
    au: date | None = None,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(current_user),
):
    chantier = get_chantier_for(db, user, chantier_id)
    du, au = _periode(du, au)
    data = svc.recapitulatif(db, chantier, du, au)
    if user.role == Role.CHEF:  # le chef voit les jours et heures, pas les salaires
        for row in data["lignes"]:
            row["taux_journalier"] = 0
            row["cout"] = 0
        data["total_cout"] = 0
    return data


@router.get("/recap.csv")
def recap_csv(
    chantier_id: str,
    du: date | None = None,
    au: date | None = None,
    db: Session = Depends(get_db),
    user: Utilisateur = Depends(current_user),
):
    chantier = get_chantier_for(db, user, chantier_id)
    du, au = _periode(du, au)
    data = svc.recapitulatif(db, chantier, du, au)
    voir_cout = user.role != Role.CHEF
    devise = get_settings().currency

    buf = io.StringIO()
    buf.write("﻿")  # pour qu'Excel lise correctement les accents
    w = csv.writer(buf, delimiter=";")
    entete = ["Matricule", "Ouvrier", "Métier", "Jours", "Heures", "Absences", "Retards", "Pointages hors zone"]
    if voir_cout:
        entete += [f"Taux journalier ({devise})", f"Coût ({devise})"]
    w.writerow(entete)
    for r in data["lignes"]:
        row = [r["matricule"] or "", r["nom"], r["metier"] or "", r["jours"], r["heures"], r["absences"],
               r["retards"], r["hors_zone"]]
        if voir_cout:
            row += [r["taux_journalier"], r["cout"]]
        w.writerow([str(v).replace(".", ",") if not isinstance(v, str) else v for v in row])
    nom = f"pointage_{chantier.nom}_{du:%Y%m%d}_{au:%Y%m%d}.csv".replace(" ", "_")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nom.encode("ascii", "ignore").decode()}"'},
    )


@router.get("/semaine")
def semaine(
    chantier_id: str, jour: date | None = None, db: Session = Depends(get_db), user: Utilisateur = Depends(current_user)
):
    """Grille de la semaine (lundi → dimanche) : statut de chaque ouvrier chaque jour."""
    chantier = get_chantier_for(db, user, chantier_id)
    jour = jour or svc.aujourdhui()
    lundi = jour - timedelta(days=jour.weekday())
    jours = [lundi + timedelta(days=i) for i in range(7)]
    pointages = db.scalars(
        select(Pointage).where(Pointage.chantier_id == chantier.id, Pointage.date >= jours[0], Pointage.date <= jours[-1])
    ).all()
    grille: dict[str, dict[str, str]] = {}
    for p in pointages:
        grille.setdefault(p.ouvrier_id, {})[p.date.isoformat()] = p.statut.value
    return {
        "jours": [d.isoformat() for d in jours],
        "ouvriers": [
            {"id": o.id, "nom": svc.nom_ouvrier(o), "metier": o.metier, "statuts": grille.get(o.id, {})}
            for o in svc.ouvriers_affectes(db, chantier.id)
        ],
    }
