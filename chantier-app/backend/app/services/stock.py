"""Contrôle des matériaux : la détection des fuites de stock.

Trois barrières successives :

1. **À la demande** - le chef de chantier ne peut pas demander plus que la quantité
   autorisée par le métré (quantité nette + taux de perte) pour un ouvrage. Au-delà,
   la demande est bloquée, un motif est obligatoire et le bureau doit valider.
   Les demandes en attente comptent aussi : impossible de contourner la limite en
   découpant une grosse demande en plusieurs petites.

2. **À la consommation** - chaque sortie de stock est rattachée à un ouvrage. Si la
   quantité posée dépasse la quantité autorisée, une alerte de surconsommation part.
   On ne peut pas sortir plus que le stock théorique présent sur le chantier.

3. **À l'inventaire** - le stock physique compté est comparé au stock théorique
   (livraisons - consommations - retours). Un manquant au-delà de la tolérance
   déclenche une alerte d'écart d'inventaire, avec sa valeur.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    BesoinMateriau,
    Chantier,
    DemandeMateriau,
    Gravite,
    Inventaire,
    LigneInventaire,
    Materiau,
    MouvementStock,
    Ouvrage,
    StatutDemande,
    TypeAlerte,
    TypeMouvement,
    Utilisateur,
)
from app.services.alertes import creer_alerte, gravite_depassement
from app.services.erreurs import ErreurMetier

ZERO = Decimal("0")
Q3 = Decimal("0.001")
STATUTS_ENGAGES = (StatutDemande.APPROUVEE, StatutDemande.EN_ATTENTE, StatutDemande.LIVREE)


def q(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(Q3, rounding=ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
    """12.500 -> « 12,5 »"""
    text = f"{value.normalize():f}" if value != ZERO else "0"
    return text.replace(".", ",")


def quantite_autorisee(besoin: BesoinMateriau | None) -> Decimal:
    if besoin is None:
        return ZERO
    return q(Decimal(besoin.quantite_nette) * (1 + Decimal(besoin.taux_perte_pct) / 100))


def _ouvrage_du_chantier(db: Session, chantier: Chantier, ouvrage_id: str) -> Ouvrage:
    ouvrage = db.get(Ouvrage, ouvrage_id)
    if ouvrage is None or ouvrage.chantier_id != chantier.id:
        raise ErreurMetier("Ouvrage introuvable sur ce chantier", 404)
    return ouvrage


def _materiau(db: Session, materiau_id: str) -> Materiau:
    materiau = db.get(Materiau, materiau_id)
    if materiau is None:
        raise ErreurMetier("Matériau introuvable", 404)
    return materiau


def get_besoin(db: Session, ouvrage_id: str, materiau_id: str) -> BesoinMateriau | None:
    return db.scalar(
        select(BesoinMateriau).where(
            BesoinMateriau.ouvrage_id == ouvrage_id, BesoinMateriau.materiau_id == materiau_id
        )
    )


def quantite_engagee(db: Session, ouvrage_id: str, materiau_id: str, sauf: str | None = None) -> Decimal:
    """Quantité déjà demandée (approuvée, livrée ou en attente) pour ce matériau sur cet ouvrage."""
    stmt = select(DemandeMateriau).where(
        DemandeMateriau.ouvrage_id == ouvrage_id,
        DemandeMateriau.materiau_id == materiau_id,
        DemandeMateriau.statut.in_(STATUTS_ENGAGES),
    )
    if sauf:
        stmt = stmt.where(DemandeMateriau.id != sauf)
    total = ZERO
    for d in db.scalars(stmt):
        # Une demande livrée compte pour ce qui a réellement été livré.
        if d.statut == StatutDemande.LIVREE and d.quantite_livree is not None:
            total += Decimal(d.quantite_livree)
        else:
            total += Decimal(d.quantite)
    return q(total)


@dataclass
class Verification:
    quantite_autorisee: Decimal
    deja_engage: Decimal
    reste: Decimal
    quantite: Decimal
    depassement: Decimal
    dans_le_metre: bool
    prevu_au_metre: bool
    unite: str

    def as_dict(self) -> dict:
        return {
            "quantite_autorisee": self.quantite_autorisee,
            "deja_engage": self.deja_engage,
            "reste": self.reste,
            "quantite": self.quantite,
            "depassement": self.depassement,
            "dans_le_metre": self.dans_le_metre,
            "prevu_au_metre": self.prevu_au_metre,
            "unite": self.unite,
        }


def verifier_demande(
    db: Session, chantier: Chantier, ouvrage_id: str, materiau_id: str, quantite: Decimal
) -> Verification:
    """Ce que le chef de chantier voit avant d'envoyer sa demande."""
    if quantite <= 0:
        raise ErreurMetier("La quantité doit être supérieure à zéro", 422)
    _ouvrage_du_chantier(db, chantier, ouvrage_id)
    materiau = _materiau(db, materiau_id)
    besoin = get_besoin(db, ouvrage_id, materiau_id)
    autorise = quantite_autorisee(besoin)
    engage = quantite_engagee(db, ouvrage_id, materiau_id)
    reste = max(ZERO, q(autorise - engage))
    quantite = q(quantite)
    depassement = max(ZERO, q(quantite - reste))
    return Verification(
        quantite_autorisee=autorise,
        deja_engage=engage,
        reste=reste,
        quantite=quantite,
        depassement=depassement,
        dans_le_metre=depassement == ZERO,
        prevu_au_metre=besoin is not None,
        unite=materiau.unite,
    )


def _prochain_numero(db: Session, chantier_id: str) -> int:
    dernier = db.scalar(
        select(func.max(DemandeMateriau.numero)).where(DemandeMateriau.chantier_id == chantier_id)
    )
    return (dernier or 0) + 1


def creer_demande(
    db: Session,
    user: Utilisateur,
    chantier: Chantier,
    *,
    ouvrage_id: str,
    materiau_id: str,
    quantite: Decimal,
    motif_depassement: str | None = None,
    commentaire: str | None = None,
) -> DemandeMateriau:
    v = verifier_demande(db, chantier, ouvrage_id, materiau_id, quantite)
    motif = (motif_depassement or "").strip() or None

    if not v.dans_le_metre and motif is None:
        if v.prevu_au_metre:
            message = (
                f"Demande bloquée : il reste {fmt(v.reste)} {v.unite} à demander sur les "
                f"{fmt(v.quantite_autorisee)} {v.unite} prévus au métré. Pour demander plus, "
                "indiquez le motif (casse, erreur de métré, modification…) : la demande sera "
                "envoyée au bureau pour validation."
            )
        else:
            message = (
                "Demande bloquée : ce matériau n'est pas prévu au métré de cet ouvrage. "
                "Indiquez le motif : la demande sera envoyée au bureau pour validation."
            )
        raise ErreurMetier(message, 422, {"code": "depassement_metre", "verification": _jsonable(v)})

    demande = DemandeMateriau(
        numero=_prochain_numero(db, chantier.id),
        chantier_id=chantier.id,
        ouvrage_id=ouvrage_id,
        materiau_id=materiau_id,
        quantite=v.quantite,
        statut=StatutDemande.APPROUVEE if v.dans_le_metre else StatutDemande.EN_ATTENTE,
        quantite_autorisee=v.quantite_autorisee,
        deja_engage=v.deja_engage,
        depassement=v.depassement,
        motif_depassement=motif if not v.dans_le_metre else None,
        commentaire=commentaire,
        demandeur_id=user.id,
    )
    db.add(demande)
    db.flush()

    if not v.dans_le_metre:
        ouvrage = db.get(Ouvrage, ouvrage_id)
        materiau = db.get(Materiau, materiau_id)
        base = v.quantite_autorisee if v.quantite_autorisee > 0 else None
        total_apres = v.deja_engage + v.quantite
        pct = float((total_apres - v.quantite_autorisee) / base * 100) if base else 100.0
        creer_alerte(
            db,
            chantier_id=chantier.id,
            type=TypeAlerte.DEPASSEMENT_METRE,
            gravite=gravite_depassement(pct),
            titre=(
                f"Demande n°{demande.numero} : {fmt(v.depassement)} {v.unite} de {materiau.nom} "
                f"au-delà du métré ({ouvrage.nom})"
            ),
            detail=(
                f"Prévu au métré : {fmt(v.quantite_autorisee)} {v.unite}. Déjà demandé : "
                f"{fmt(v.deja_engage)} {v.unite}. Demandé maintenant : {fmt(v.quantite)} {v.unite}. "
                f"Dépassement : +{pct:.0f} %. Motif du chef de chantier : {motif}"
            ),
            lien_type="demande",
            lien_id=demande.id,
        )
    return demande


def _jsonable(v: Verification) -> dict:
    return {k: (float(val) if isinstance(val, Decimal) else val) for k, val in v.as_dict().items()}


def decider_demande(
    db: Session, demande: DemandeMateriau, validateur: Utilisateur, approuver: bool, commentaire: str | None
) -> DemandeMateriau:
    if demande.statut != StatutDemande.EN_ATTENTE:
        raise ErreurMetier("Cette demande n'est plus en attente de validation")
    if not approuver and not (commentaire or "").strip():
        raise ErreurMetier("Indiquez la raison du refus pour le chef de chantier", 422)
    demande.statut = StatutDemande.APPROUVEE if approuver else StatutDemande.REFUSEE
    demande.validateur_id = validateur.id
    demande.decision_le = datetime.now(UTC)
    demande.commentaire_decision = commentaire
    return demande


def annuler_demande(db: Session, demande: DemandeMateriau, user: Utilisateur) -> None:
    if demande.statut == StatutDemande.LIVREE:
        raise ErreurMetier("Une demande déjà livrée ne peut pas être annulée")
    if demande.statut == StatutDemande.REFUSEE:
        raise ErreurMetier("Cette demande est déjà refusée")
    demande.statut = StatutDemande.REFUSEE
    demande.validateur_id = user.id
    demande.decision_le = datetime.now(UTC)
    demande.commentaire_decision = "Annulée par le demandeur"


def receptionner(
    db: Session, demande: DemandeMateriau, user: Utilisateur, quantite_livree: Decimal, note: str | None
) -> MouvementStock:
    """La livraison est confirmée sur le chantier : le stock augmente."""
    if demande.statut != StatutDemande.APPROUVEE:
        raise ErreurMetier("Seule une demande approuvée peut être réceptionnée")
    quantite_livree = q(quantite_livree)
    if quantite_livree <= 0:
        raise ErreurMetier("La quantité livrée doit être supérieure à zéro", 422)
    if quantite_livree > Decimal(demande.quantite):
        raise ErreurMetier(
            f"Quantité livrée supérieure à la quantité approuvée ({fmt(Decimal(demande.quantite))}). "
            "Faites une nouvelle demande pour le complément.",
            422,
        )
    demande.statut = StatutDemande.LIVREE
    demande.quantite_livree = quantite_livree
    mouvement = MouvementStock(
        chantier_id=demande.chantier_id,
        materiau_id=demande.materiau_id,
        type=TypeMouvement.LIVRAISON,
        quantite=quantite_livree,
        ouvrage_id=demande.ouvrage_id,
        demande_id=demande.id,
        note=note,
        auteur_id=user.id,
    )
    db.add(mouvement)
    return mouvement


def stock_theorique(db: Session, chantier_id: str) -> dict[str, Decimal]:
    rows = db.execute(
        select(MouvementStock.materiau_id, MouvementStock.quantite).where(
            MouvementStock.chantier_id == chantier_id
        )
    )
    stock: dict[str, Decimal] = {}
    for materiau_id, quantite in rows:
        stock[materiau_id] = stock.get(materiau_id, ZERO) + Decimal(quantite)
    return {k: q(v) for k, v in stock.items()}


def consomme_sur_ouvrage(db: Session, ouvrage_id: str, materiau_id: str) -> Decimal:
    total = ZERO
    for (quantite,) in db.execute(
        select(MouvementStock.quantite).where(
            MouvementStock.ouvrage_id == ouvrage_id,
            MouvementStock.materiau_id == materiau_id,
            MouvementStock.type == TypeMouvement.CONSOMMATION,
        )
    ):
        total += -Decimal(quantite)
    return q(total)


def sortir_du_stock(
    db: Session,
    user: Utilisateur,
    chantier: Chantier,
    *,
    type: TypeMouvement,
    materiau_id: str,
    quantite: Decimal,
    ouvrage_id: str | None,
    note: str | None,
) -> MouvementStock:
    if type not in (TypeMouvement.CONSOMMATION, TypeMouvement.RETOUR):
        raise ErreurMetier("Type de sortie invalide", 422)
    quantite = q(quantite)
    if quantite <= 0:
        raise ErreurMetier("La quantité doit être supérieure à zéro", 422)
    materiau = _materiau(db, materiau_id)
    if type == TypeMouvement.CONSOMMATION:
        if not ouvrage_id:
            raise ErreurMetier("Indiquez l'ouvrage sur lequel le matériau a été posé", 422)
        _ouvrage_du_chantier(db, chantier, ouvrage_id)

    en_stock = stock_theorique(db, chantier.id).get(materiau_id, ZERO)
    if quantite > en_stock:
        raise ErreurMetier(
            f"Stock insuffisant : il n'y a que {fmt(en_stock)} {materiau.unite} de {materiau.nom} "
            "sur le chantier selon les livraisons enregistrées.",
            422,
        )

    mouvement = MouvementStock(
        chantier_id=chantier.id,
        materiau_id=materiau_id,
        type=type,
        quantite=-quantite,
        ouvrage_id=ouvrage_id,
        note=note,
        auteur_id=user.id,
    )
    db.add(mouvement)
    db.flush()

    if type == TypeMouvement.CONSOMMATION:
        besoin = get_besoin(db, ouvrage_id, materiau_id)
        autorise = quantite_autorisee(besoin)
        consomme = consomme_sur_ouvrage(db, ouvrage_id, materiau_id)
        if consomme > autorise:
            ouvrage = db.get(Ouvrage, ouvrage_id)
            exces = q(consomme - autorise)
            pct = float(exces / autorise * 100) if autorise > 0 else 100.0
            creer_alerte(
                db,
                chantier_id=chantier.id,
                type=TypeAlerte.SURCONSOMMATION,
                gravite=gravite_depassement(pct),
                titre=f"Surconsommation de {materiau.nom} sur {ouvrage.nom} : +{fmt(exces)} {materiau.unite}",
                detail=(
                    f"Posé au total : {fmt(consomme)} {materiau.unite} pour {fmt(autorise)} "
                    f"{materiau.unite} prévus au métré (+{pct:.0f} %)."
                ),
                lien_type="mouvement",
                lien_id=mouvement.id,
            )
    return mouvement


def faire_inventaire(
    db: Session,
    user: Utilisateur,
    chantier: Chantier,
    *,
    jour: date,
    lignes: list[tuple[str, Decimal]],
    note: str | None,
) -> Inventaire:
    if not lignes:
        raise ErreurMetier("Saisissez au moins un matériau compté", 422)
    tolerance = Decimal(str(get_settings().tolerance_inventaire_pct))
    theorique = stock_theorique(db, chantier.id)
    inventaire = Inventaire(chantier_id=chantier.id, date=jour, auteur_id=user.id, note=note)
    db.add(inventaire)
    db.flush()

    manquants: list[str] = []
    valeur_totale = ZERO
    pire_pct = 0.0
    vus: set[str] = set()
    for materiau_id, compte in lignes:
        if materiau_id in vus:
            raise ErreurMetier("Un même matériau est saisi deux fois dans l'inventaire", 422)
        vus.add(materiau_id)
        materiau = _materiau(db, materiau_id)
        compte = q(compte)
        if compte < 0:
            raise ErreurMetier("Une quantité comptée ne peut pas être négative", 422)
        attendu = theorique.get(materiau_id, ZERO)
        ecart = q(compte - attendu)
        valeur = (ecart * Decimal(materiau.prix_unitaire)).quantize(Decimal("0.01"))
        inventaire.lignes.append(
            LigneInventaire(
                materiau_id=materiau_id,
                quantite_theorique=attendu,
                quantite_comptee=compte,
                ecart=ecart,
                valeur_ecart=valeur,
            )
        )
        if ecart != ZERO:
            db.add(
                MouvementStock(
                    chantier_id=chantier.id,
                    materiau_id=materiau_id,
                    type=TypeMouvement.AJUSTEMENT,
                    quantite=ecart,
                    inventaire_id=inventaire.id,
                    note=f"Inventaire du {jour.strftime('%d/%m/%Y')}",
                    auteur_id=user.id,
                )
            )
        if ecart < 0:
            pct = float(-ecart / attendu * 100) if attendu > 0 else 100.0
            if Decimal(str(pct)) > tolerance:
                manquants.append(
                    f"{materiau.nom} : {fmt(-ecart)} {materiau.unite} manquants "
                    f"sur {fmt(attendu)} attendus (-{pct:.0f} %, {fmt(-valeur)} {get_settings().currency})"
                )
                valeur_totale += -valeur
                pire_pct = max(pire_pct, pct)

    if manquants:
        creer_alerte(
            db,
            chantier_id=chantier.id,
            type=TypeAlerte.ECART_INVENTAIRE,
            gravite=gravite_depassement(pire_pct),
            titre=(
                f"Inventaire du {jour.strftime('%d/%m/%Y')} : {len(manquants)} matériau(x) manquant(s), "
                f"{fmt(valeur_totale)} {get_settings().currency}"
            ),
            detail="\n".join(manquants),
            lien_type="inventaire",
            lien_id=inventaire.id,
        )
    return inventaire


def suivi_materiaux(db: Session, chantier_id: str) -> list[dict]:
    """Tableau de bord du métré : prévu / demandé / livré / posé, ligne par ligne."""
    besoins = db.scalars(
        select(BesoinMateriau).join(Ouvrage).where(Ouvrage.chantier_id == chantier_id).order_by(Ouvrage.nom)
    ).all()
    demandes = db.scalars(select(DemandeMateriau).where(DemandeMateriau.chantier_id == chantier_id)).all()
    conso = db.execute(
        select(MouvementStock.ouvrage_id, MouvementStock.materiau_id, MouvementStock.quantite).where(
            MouvementStock.chantier_id == chantier_id, MouvementStock.type == TypeMouvement.CONSOMMATION
        )
    ).all()

    cles: dict[tuple[str, str], dict] = {}

    def ligne(ouvrage_id: str, materiau_id: str) -> dict:
        key = (ouvrage_id, materiau_id)
        if key not in cles:
            cles[key] = {
                "ouvrage_id": ouvrage_id,
                "materiau_id": materiau_id,
                "besoin_id": None,
                "quantite_nette": ZERO,
                "taux_perte_pct": ZERO,
                "autorise": ZERO,
                "demande": ZERO,
                "en_attente": ZERO,
                "livre": ZERO,
                "pose": ZERO,
            }
        return cles[key]

    for b in besoins:
        row = ligne(b.ouvrage_id, b.materiau_id)
        row.update(
            besoin_id=b.id,
            quantite_nette=q(b.quantite_nette),
            taux_perte_pct=Decimal(b.taux_perte_pct),
            autorise=quantite_autorisee(b),
            note_calcul=b.note_calcul,
        )
    for d in demandes:
        row = ligne(d.ouvrage_id, d.materiau_id)
        if d.statut in (StatutDemande.APPROUVEE, StatutDemande.LIVREE):
            row["demande"] += Decimal(d.quantite_livree if d.statut == StatutDemande.LIVREE else d.quantite)
        elif d.statut == StatutDemande.EN_ATTENTE:
            row["en_attente"] += Decimal(d.quantite)
        if d.statut == StatutDemande.LIVREE:
            row["livre"] += Decimal(d.quantite_livree or 0)
    for ouvrage_id, materiau_id, quantite in conso:
        if ouvrage_id:
            ligne(ouvrage_id, materiau_id)["pose"] += -Decimal(quantite)

    ouvrages = {o.id: o for o in db.scalars(select(Ouvrage).where(Ouvrage.chantier_id == chantier_id))}
    materiaux = {m.id: m for m in db.scalars(select(Materiau).where(Materiau.id.in_({k[1] for k in cles})))}
    resultat = []
    for row in cles.values():
        m = materiaux[row["materiau_id"]]
        autorise = row["autorise"]
        row.update(
            ouvrage_nom=ouvrages[row["ouvrage_id"]].nom,
            materiau_nom=m.nom,
            unite=m.unite,
            reste_a_demander=max(ZERO, q(autorise - row["demande"] - row["en_attente"])),
            hors_metre=row["demande"] + row["en_attente"] > autorise or row["pose"] > autorise,
            pct_pose=float(row["pose"] / autorise * 100) if autorise > 0 else None,
        )
        for k in ("demande", "en_attente", "livre", "pose"):
            row[k] = q(row[k])
        resultat.append(row)
    resultat.sort(key=lambda r: (r["ouvrage_nom"], r["materiau_nom"]))
    return resultat
