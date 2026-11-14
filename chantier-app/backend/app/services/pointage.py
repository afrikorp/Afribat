"""Pointage des ouvriers par le chef de chantier.

Règles :
- seuls les ouvriers affectés au chantier peuvent être pointés ;
- un ouvrier est pointé une seule fois par jour, tous chantiers confondus
  (un second pointage ailleurs est refusé et signalé : « ouvrier fantôme ») ;
- pas de pointage dans le futur ; le chef ne peut corriger que les jours récents,
  au-delà seul le bureau peut modifier ;
- la position GPS du téléphone est comparée à celle du chantier : un pointage fait
  loin du chantier est signalé.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Affectation,
    Chantier,
    Gravite,
    Ouvrier,
    Pointage,
    Role,
    StatutPointage,
    TypeAlerte,
    Utilisateur,
)
from app.services.alertes import creer_alerte
from app.services.erreurs import ErreurMetier

JOURS = {
    StatutPointage.PRESENT: Decimal("1"),
    StatutPointage.RETARD: Decimal("1"),
    StatutPointage.DEMI_JOURNEE: Decimal("0.5"),
    StatutPointage.ABSENT: Decimal("0"),
    StatutPointage.CONGE: Decimal("0"),
    StatutPointage.MALADIE: Decimal("0"),
}
TRAVAILLE = {StatutPointage.PRESENT, StatutPointage.RETARD, StatutPointage.DEMI_JOURNEE}


def aujourdhui() -> date:
    return datetime.now(get_settings().tz).date()


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Distance à vol d'oiseau (formule de haversine)."""
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)))


def calcul_heures(arrivee: time | None, depart: time | None, pause_minutes: int) -> Decimal:
    if arrivee is None or depart is None:
        return Decimal("0")
    debut = datetime.combine(date.min, arrivee)
    fin = datetime.combine(date.min, depart)
    if fin <= debut:
        raise ErreurMetier("L'heure de départ doit être après l'heure d'arrivée", 422)
    minutes = (fin - debut).total_seconds() / 60 - max(0, pause_minutes)
    return Decimal(str(round(max(0.0, minutes) / 60, 2)))


def verifier_date(user: Utilisateur, jour: date) -> None:
    today = aujourdhui()
    if jour > today:
        raise ErreurMetier("Impossible de pointer un jour futur", 422)
    if user.role == Role.CHEF:
        limite = today - timedelta(days=get_settings().jours_correction_pointage)
        if jour < limite:
            raise ErreurMetier(
                f"Le pointage du {jour.strftime('%d/%m/%Y')} est clôturé. "
                "Demandez la correction au conducteur de travaux.",
                403,
            )


def ouvriers_affectes(db: Session, chantier_id: str, jour: date | None = None) -> list[Ouvrier]:
    stmt = (
        select(Ouvrier)
        .join(Affectation, Affectation.ouvrier_id == Ouvrier.id)
        .where(Affectation.chantier_id == chantier_id, Ouvrier.actif.is_(True))
        .order_by(Ouvrier.nom, Ouvrier.prenom)
    )
    ouvriers = []
    for o in db.scalars(stmt):
        if jour is not None:
            aff = db.scalar(
                select(Affectation).where(Affectation.chantier_id == chantier_id, Affectation.ouvrier_id == o.id)
            )
            if (aff.date_debut and jour < aff.date_debut) or (aff.date_fin and jour > aff.date_fin):
                continue
        ouvriers.append(o)
    return ouvriers


@dataclass
class LignePointage:
    ouvrier_id: str
    statut: StatutPointage
    heure_arrivee: time | None = None
    heure_depart: time | None = None
    pause_minutes: int = 60
    note: str | None = None


@dataclass
class ResultatPointage:
    enregistres: list[Pointage] = field(default_factory=list)
    refus: list[dict] = field(default_factory=list)
    hors_zone: bool = False
    distance_m: int | None = None


def nom_ouvrier(o: Ouvrier) -> str:
    return f"{o.prenom} {o.nom}".strip() if o.prenom else o.nom


def pointer(
    db: Session,
    user: Utilisateur,
    chantier: Chantier,
    *,
    jour: date,
    lignes: list[LignePointage],
    latitude: float | None,
    longitude: float | None,
) -> ResultatPointage:
    verifier_date(user, jour)
    if not lignes:
        raise ErreurMetier("Aucun ouvrier à pointer", 422)

    resultat = ResultatPointage()
    if latitude is not None and longitude is not None and chantier.latitude is not None:
        resultat.distance_m = distance_m(latitude, longitude, chantier.latitude, chantier.longitude)
        resultat.hors_zone = resultat.distance_m > chantier.rayon_m

    affectes = {o.id: o for o in ouvriers_affectes(db, chantier.id, jour)}
    deja_vus: set[str] = set()
    for ligne in lignes:
        ouvrier = db.get(Ouvrier, ligne.ouvrier_id)
        if ouvrier is None:
            resultat.refus.append({"ouvrier_id": ligne.ouvrier_id, "raison": "Ouvrier introuvable"})
            continue
        if ligne.ouvrier_id in deja_vus:
            resultat.refus.append({"ouvrier_id": ouvrier.id, "raison": "Ouvrier saisi deux fois"})
            continue
        deja_vus.add(ligne.ouvrier_id)
        if ouvrier.id not in affectes:
            resultat.refus.append(
                {"ouvrier_id": ouvrier.id, "raison": f"{nom_ouvrier(ouvrier)} n'est pas affecté à ce chantier"}
            )
            continue

        travaille = ligne.statut in TRAVAILLE
        arrivee = ligne.heure_arrivee if travaille else None
        depart = ligne.heure_depart if travaille else None
        try:
            heures = calcul_heures(arrivee, depart, ligne.pause_minutes)
        except ErreurMetier as e:
            resultat.refus.append({"ouvrier_id": ouvrier.id, "raison": f"{nom_ouvrier(ouvrier)} : {e.message}"})
            continue

        existant = db.scalar(select(Pointage).where(Pointage.ouvrier_id == ouvrier.id, Pointage.date == jour))
        if existant is not None and existant.chantier_id != chantier.id:
            autre = db.get(Chantier, existant.chantier_id)
            raison = (
                f"{nom_ouvrier(ouvrier)} est déjà pointé le {jour.strftime('%d/%m/%Y')} "
                f"sur le chantier « {autre.nom} »"
            )
            resultat.refus.append({"ouvrier_id": ouvrier.id, "raison": raison})
            creer_alerte(
                db,
                chantier_id=chantier.id,
                type=TypeAlerte.POINTAGE_DOUBLE,
                gravite=Gravite.CRITIQUE,
                titre=f"Double pointage refusé : {nom_ouvrier(ouvrier)}",
                detail=f"{raison}. Tentative de {user.nom} sur « {chantier.nom} ».",
                lien_type="pointage",
                lien_id=existant.id,
            )
            continue

        p = existant or Pointage(ouvrier_id=ouvrier.id, chantier_id=chantier.id, date=jour)
        p.statut = ligne.statut
        p.heure_arrivee = arrivee
        p.heure_depart = depart
        p.pause_minutes = ligne.pause_minutes
        p.heures = heures
        p.jours = JOURS[ligne.statut]
        p.note = ligne.note
        p.latitude = latitude
        p.longitude = longitude
        p.distance_m = resultat.distance_m
        p.hors_zone = resultat.hors_zone
        p.pointe_par_id = user.id
        if existant is None:
            db.add(p)
        resultat.enregistres.append(p)

    db.flush()
    if resultat.hors_zone and resultat.enregistres:
        creer_alerte(
            db,
            chantier_id=chantier.id,
            type=TypeAlerte.POINTAGE_HORS_ZONE,
            gravite=Gravite.ATTENTION,
            titre=(
                f"Pointage du {jour.strftime('%d/%m/%Y')} fait à {resultat.distance_m} m du chantier"
            ),
            detail=(
                f"{user.nom} a pointé {len(resultat.enregistres)} ouvrier(s) à {resultat.distance_m} m "
                f"du chantier (zone autorisée : {chantier.rayon_m} m)."
            ),
            lien_type="pointage",
            lien_id=resultat.enregistres[0].id,
        )
    return resultat


def recapitulatif(db: Session, chantier: Chantier, du: date, au: date) -> dict:
    if au < du:
        raise ErreurMetier("La date de fin doit être après la date de début", 422)
    pointages = db.scalars(
        select(Pointage).where(Pointage.chantier_id == chantier.id, Pointage.date >= du, Pointage.date <= au)
    ).all()
    par_ouvrier: dict[str, dict] = {}
    for p in pointages:
        o = p.ouvrier
        row = par_ouvrier.setdefault(
            o.id,
            {
                "ouvrier_id": o.id,
                "matricule": o.matricule,
                "nom": nom_ouvrier(o),
                "metier": o.metier,
                "taux_journalier": Decimal(o.taux_journalier),
                "jours": Decimal("0"),
                "heures": Decimal("0"),
                "absences": 0,
                "retards": 0,
                "hors_zone": 0,
            },
        )
        row["jours"] += Decimal(p.jours)
        row["heures"] += Decimal(p.heures)
        row["absences"] += p.statut == StatutPointage.ABSENT
        row["retards"] += p.statut == StatutPointage.RETARD
        row["hors_zone"] += bool(p.hors_zone)
    lignes = sorted(par_ouvrier.values(), key=lambda r: r["nom"])
    for row in lignes:
        row["cout"] = (row["jours"] * row["taux_journalier"]).quantize(Decimal("0.01"))
    return {
        "du": du,
        "au": au,
        "lignes": lignes,
        "total_jours": sum((r["jours"] for r in lignes), Decimal("0")),
        "total_heures": sum((r["heures"] for r in lignes), Decimal("0")),
        "total_cout": sum((r["cout"] for r in lignes), Decimal("0")),
    }
