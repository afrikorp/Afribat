"""Modèle de données.

Vue d'ensemble :
- Un **chantier** a un chef de chantier et un conducteur de travaux.
- Il est découpé en **ouvrages** (ex. « Salle de bain RDC »).
- Le **métré** fixe, pour chaque ouvrage, la quantité prévue de chaque **matériau**
  (ex. 12,5 m² de carrelage + 10 % de perte = 13,75 m² autorisés).
- Le chef de chantier fait des **demandes de matériaux**. Au-delà de la quantité
  autorisée, la demande est bloquée jusqu'à validation par le bureau.
- Les **mouvements de stock** (livraison, consommation, ajustement d'inventaire)
  donnent le stock théorique ; un **inventaire** physique révèle les écarts.
- Les **ouvriers** sont affectés aux chantiers et **pointés** chaque jour.
- Toute anomalie crée une **alerte**.
"""

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

Qty = Numeric(14, 3)
Money = Numeric(14, 2)


def _id() -> str:
    return str(uuid.uuid4())


class Role(str, enum.Enum):
    ADMIN = "admin"  # bureau / direction : tous les droits
    CONDUCTEUR = "conducteur"  # conducteur de travaux : métré, validations
    CHEF = "chef"  # chef de chantier : demandes, stock, pointage


class StatutDemande(str, enum.Enum):
    APPROUVEE = "approuvee"  # dans la quantité prévue, ou validée par le bureau
    EN_ATTENTE = "en_attente"  # dépassement : attend la validation du bureau
    REFUSEE = "refusee"
    LIVREE = "livree"


class TypeMouvement(str, enum.Enum):
    LIVRAISON = "livraison"  # entrée sur le chantier
    CONSOMMATION = "consommation"  # sortie posée sur un ouvrage
    RETOUR = "retour"  # renvoyé au dépôt / fournisseur
    AJUSTEMENT = "ajustement"  # correction après inventaire physique


class StatutPointage(str, enum.Enum):
    PRESENT = "present"
    RETARD = "retard"
    DEMI_JOURNEE = "demi_journee"
    ABSENT = "absent"
    CONGE = "conge"
    MALADIE = "maladie"


class TypeAlerte(str, enum.Enum):
    DEPASSEMENT_METRE = "depassement_metre"
    SURCONSOMMATION = "surconsommation"
    ECART_INVENTAIRE = "ecart_inventaire"
    POINTAGE_HORS_ZONE = "pointage_hors_zone"
    POINTAGE_DOUBLE = "pointage_double"


class Gravite(str, enum.Enum):
    INFO = "info"
    ATTENTION = "attention"
    CRITIQUE = "critique"


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(e, values_callable=lambda x: [m.value for m in x], native_enum=False, length=30)


class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    nom: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    mot_de_passe_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(_enum(Role))
    telephone: Mapped[str | None] = mapped_column(String(40))
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chantier(Base):
    __tablename__ = "chantiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    nom: Mapped[str] = mapped_column(String(200))
    client: Mapped[str | None] = mapped_column(String(200))
    adresse: Mapped[str | None] = mapped_column(String(300))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    # Rayon (mètres) dans lequel un pointage est considéré « sur le chantier ».
    rayon_m: Mapped[int] = mapped_column(Integer, default=300)
    date_debut: Mapped[date | None] = mapped_column(Date)
    date_fin_prevue: Mapped[date | None] = mapped_column(Date)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    chef_id: Mapped[str | None] = mapped_column(ForeignKey("utilisateurs.id"))
    conducteur_id: Mapped[str | None] = mapped_column(ForeignKey("utilisateurs.id"))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chef: Mapped[Utilisateur | None] = relationship(foreign_keys=[chef_id])
    conducteur: Mapped[Utilisateur | None] = relationship(foreign_keys=[conducteur_id])
    ouvrages: Mapped[list["Ouvrage"]] = relationship(back_populates="chantier", cascade="all, delete-orphan")


class Ouvrage(Base):
    """Partie de chantier : une pièce, un lot, un élément (ex. « Salle de bain étage 1 »)."""

    __tablename__ = "ouvrages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    chantier_id: Mapped[str] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"), index=True)
    code: Mapped[str | None] = mapped_column(String(40))
    nom: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)

    chantier: Mapped[Chantier] = relationship(back_populates="ouvrages")
    besoins: Mapped[list["BesoinMateriau"]] = relationship(back_populates="ouvrage", cascade="all, delete-orphan")


class Materiau(Base):
    __tablename__ = "materiaux"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    reference: Mapped[str | None] = mapped_column(String(60), unique=True)
    nom: Mapped[str] = mapped_column(String(200))
    unite: Mapped[str] = mapped_column(String(20))  # m², m³, kg, sac, u, ml…
    prix_unitaire: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    actif: Mapped[bool] = mapped_column(Boolean, default=True)


class BesoinMateriau(Base):
    """Ligne du métré : quantité prévue d'un matériau pour un ouvrage."""

    __tablename__ = "besoins_materiaux"
    __table_args__ = (UniqueConstraint("ouvrage_id", "materiau_id", name="uq_besoin_ouvrage_materiau"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ouvrage_id: Mapped[str] = mapped_column(ForeignKey("ouvrages.id", ondelete="CASCADE"), index=True)
    materiau_id: Mapped[str] = mapped_column(ForeignKey("materiaux.id"))
    quantite_nette: Mapped[Decimal] = mapped_column(Qty)
    taux_perte_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    note_calcul: Mapped[str | None] = mapped_column(Text)  # ex. « 3,2 m × 2,5 m + plinthes »

    ouvrage: Mapped[Ouvrage] = relationship(back_populates="besoins")
    materiau: Mapped[Materiau] = relationship()


class DemandeMateriau(Base):
    __tablename__ = "demandes_materiaux"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    numero: Mapped[int] = mapped_column(Integer, index=True)
    chantier_id: Mapped[str] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"), index=True)
    ouvrage_id: Mapped[str] = mapped_column(ForeignKey("ouvrages.id"))
    materiau_id: Mapped[str] = mapped_column(ForeignKey("materiaux.id"))
    quantite: Mapped[Decimal] = mapped_column(Qty)
    statut: Mapped[StatutDemande] = mapped_column(_enum(StatutDemande), index=True)

    # Photographie du métré au moment de la demande (traçabilité).
    quantite_autorisee: Mapped[Decimal] = mapped_column(Qty)
    deja_engage: Mapped[Decimal] = mapped_column(Qty)
    depassement: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    motif_depassement: Mapped[str | None] = mapped_column(Text)

    commentaire: Mapped[str | None] = mapped_column(Text)
    demandeur_id: Mapped[str] = mapped_column(ForeignKey("utilisateurs.id"))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    validateur_id: Mapped[str | None] = mapped_column(ForeignKey("utilisateurs.id"))
    decision_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commentaire_decision: Mapped[str | None] = mapped_column(Text)
    quantite_livree: Mapped[Decimal | None] = mapped_column(Qty)

    ouvrage: Mapped[Ouvrage] = relationship()
    materiau: Mapped[Materiau] = relationship()
    demandeur: Mapped[Utilisateur] = relationship(foreign_keys=[demandeur_id])
    validateur: Mapped[Utilisateur | None] = relationship(foreign_keys=[validateur_id])


class MouvementStock(Base):
    """Le stock d'un chantier n'est jamais saisi directement : il est la somme de ses mouvements."""

    __tablename__ = "mouvements_stock"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    chantier_id: Mapped[str] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"), index=True)
    materiau_id: Mapped[str] = mapped_column(ForeignKey("materiaux.id"), index=True)
    type: Mapped[TypeMouvement] = mapped_column(_enum(TypeMouvement))
    # Signé : positif = entrée, négatif = sortie.
    quantite: Mapped[Decimal] = mapped_column(Qty)
    ouvrage_id: Mapped[str | None] = mapped_column(ForeignKey("ouvrages.id"))
    demande_id: Mapped[str | None] = mapped_column(ForeignKey("demandes_materiaux.id"))
    inventaire_id: Mapped[str | None] = mapped_column(ForeignKey("inventaires.id"))
    note: Mapped[str | None] = mapped_column(Text)
    auteur_id: Mapped[str] = mapped_column(ForeignKey("utilisateurs.id"))
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    materiau: Mapped[Materiau] = relationship()
    ouvrage: Mapped[Ouvrage | None] = relationship()
    auteur: Mapped[Utilisateur] = relationship()


class Inventaire(Base):
    __tablename__ = "inventaires"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    chantier_id: Mapped[str] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    auteur_id: Mapped[str] = mapped_column(ForeignKey("utilisateurs.id"))
    note: Mapped[str | None] = mapped_column(Text)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lignes: Mapped[list["LigneInventaire"]] = relationship(back_populates="inventaire", cascade="all, delete-orphan")
    auteur: Mapped[Utilisateur] = relationship()


class LigneInventaire(Base):
    __tablename__ = "lignes_inventaire"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    inventaire_id: Mapped[str] = mapped_column(ForeignKey("inventaires.id", ondelete="CASCADE"), index=True)
    materiau_id: Mapped[str] = mapped_column(ForeignKey("materiaux.id"))
    quantite_theorique: Mapped[Decimal] = mapped_column(Qty)
    quantite_comptee: Mapped[Decimal] = mapped_column(Qty)
    ecart: Mapped[Decimal] = mapped_column(Qty)  # comptée - théorique (négatif = manquant)
    valeur_ecart: Mapped[Decimal] = mapped_column(Money)

    inventaire: Mapped[Inventaire] = relationship(back_populates="lignes")
    materiau: Mapped[Materiau] = relationship()


class Ouvrier(Base):
    __tablename__ = "ouvriers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    matricule: Mapped[str | None] = mapped_column(String(40), unique=True)
    nom: Mapped[str] = mapped_column(String(120))
    prenom: Mapped[str | None] = mapped_column(String(120))
    metier: Mapped[str | None] = mapped_column(String(80))  # maçon, carreleur, ferrailleur…
    telephone: Mapped[str | None] = mapped_column(String(40))
    taux_journalier: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    actif: Mapped[bool] = mapped_column(Boolean, default=True)


class Affectation(Base):
    """Un ouvrier ne peut être pointé que sur un chantier où il est affecté."""

    __tablename__ = "affectations"
    __table_args__ = (UniqueConstraint("ouvrier_id", "chantier_id", name="uq_affectation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ouvrier_id: Mapped[str] = mapped_column(ForeignKey("ouvriers.id", ondelete="CASCADE"), index=True)
    chantier_id: Mapped[str] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"), index=True)
    date_debut: Mapped[date | None] = mapped_column(Date)
    date_fin: Mapped[date | None] = mapped_column(Date)

    ouvrier: Mapped[Ouvrier] = relationship()


class Pointage(Base):
    __tablename__ = "pointages"
    # Un ouvrier ne peut être pointé qu'une fois par jour, tous chantiers confondus.
    __table_args__ = (UniqueConstraint("ouvrier_id", "date", name="uq_pointage_ouvrier_jour"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    ouvrier_id: Mapped[str] = mapped_column(ForeignKey("ouvriers.id", ondelete="CASCADE"), index=True)
    chantier_id: Mapped[str] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    statut: Mapped[StatutPointage] = mapped_column(_enum(StatutPointage))
    heure_arrivee: Mapped[time | None] = mapped_column(Time)
    heure_depart: Mapped[time | None] = mapped_column(Time)
    pause_minutes: Mapped[int] = mapped_column(Integer, default=60)
    heures: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    jours: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0"))  # 1, 0,5 ou 0
    note: Mapped[str | None] = mapped_column(Text)

    # Position du téléphone du chef au moment du pointage.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    distance_m: Mapped[int | None] = mapped_column(Integer)
    hors_zone: Mapped[bool] = mapped_column(Boolean, default=False)

    pointe_par_id: Mapped[str] = mapped_column(ForeignKey("utilisateurs.id"))
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ouvrier: Mapped[Ouvrier] = relationship()
    pointe_par: Mapped[Utilisateur] = relationship()


class Alerte(Base):
    __tablename__ = "alertes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    chantier_id: Mapped[str] = mapped_column(ForeignKey("chantiers.id", ondelete="CASCADE"), index=True)
    type: Mapped[TypeAlerte] = mapped_column(_enum(TypeAlerte))
    gravite: Mapped[Gravite] = mapped_column(_enum(Gravite))
    titre: Mapped[str] = mapped_column(String(250))
    detail: Mapped[str | None] = mapped_column(Text)
    lien_type: Mapped[str | None] = mapped_column(String(40))  # demande, inventaire, pointage…
    lien_id: Mapped[str | None] = mapped_column(String(36))
    lue: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cree_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chantier: Mapped[Chantier] = relationship()
