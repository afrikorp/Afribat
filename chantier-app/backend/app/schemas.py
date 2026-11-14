from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer

from app.models import Gravite, Role, StatutDemande, StatutPointage, TypeAlerte, TypeMouvement

# Les quantités sont des Decimal en base (pas d'erreur d'arrondi) et des nombres en JSON.
Num = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Utilisateurs ------------------------------------------------------------


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UtilisateurOut(ORM):
    id: str
    nom: str
    email: str
    role: Role
    telephone: str | None
    actif: bool


class UtilisateurCreate(BaseModel):
    nom: str = Field(min_length=2, max_length=120)
    email: EmailStr
    mot_de_passe: str = Field(min_length=8, max_length=128)
    role: Role
    telephone: str | None = None


class UtilisateurUpdate(BaseModel):
    nom: str | None = Field(None, min_length=2, max_length=120)
    role: Role | None = None
    telephone: str | None = None
    actif: bool | None = None
    mot_de_passe: str | None = Field(None, min_length=8, max_length=128)


class UserMini(ORM):
    id: str
    nom: str


# --- Chantiers et ouvrages ---------------------------------------------------


class ChantierBase(BaseModel):
    nom: str = Field(min_length=2, max_length=200)
    client: str | None = None
    adresse: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    rayon_m: int = Field(300, ge=20, le=10_000)
    date_debut: date | None = None
    date_fin_prevue: date | None = None
    chef_id: str | None = None
    conducteur_id: str | None = None


class ChantierCreate(ChantierBase):
    pass


class ChantierUpdate(BaseModel):
    nom: str | None = Field(None, min_length=2, max_length=200)
    client: str | None = None
    adresse: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    rayon_m: int | None = Field(None, ge=20, le=10_000)
    date_debut: date | None = None
    date_fin_prevue: date | None = None
    chef_id: str | None = None
    conducteur_id: str | None = None
    actif: bool | None = None


class ChantierOut(ORM, ChantierBase):
    id: str
    actif: bool
    chef: UserMini | None
    conducteur: UserMini | None


class OuvrageIn(BaseModel):
    code: str | None = None
    nom: str = Field(min_length=1, max_length=200)
    description: str | None = None


class OuvrageOut(ORM, OuvrageIn):
    id: str
    chantier_id: str


# --- Matériaux et métré -----------------------------------------------------


class MateriauIn(BaseModel):
    reference: str | None = None
    nom: str = Field(min_length=1, max_length=200)
    unite: str = Field(min_length=1, max_length=20)
    prix_unitaire: Decimal = Field(Decimal("0"), ge=0)
    actif: bool = True


class MateriauOut(ORM):
    id: str
    reference: str | None
    nom: str
    unite: str
    prix_unitaire: Num
    actif: bool


class BesoinIn(BaseModel):
    ouvrage_id: str
    materiau_id: str
    quantite_nette: Decimal = Field(gt=0)
    taux_perte_pct: Decimal = Field(Decimal("0"), ge=0, le=100)
    note_calcul: str | None = None


class BesoinUpdate(BaseModel):
    quantite_nette: Decimal | None = Field(None, gt=0)
    taux_perte_pct: Decimal | None = Field(None, ge=0, le=100)
    note_calcul: str | None = None


class BesoinOut(ORM):
    id: str
    ouvrage_id: str
    materiau_id: str
    quantite_nette: Num
    taux_perte_pct: Num
    note_calcul: str | None
    quantite_autorisee: Num
    materiau: MateriauOut


class SuiviLigne(BaseModel):
    ouvrage_id: str
    ouvrage_nom: str
    materiau_id: str
    materiau_nom: str
    unite: str
    besoin_id: str | None
    quantite_nette: Num
    taux_perte_pct: Num
    note_calcul: str | None = None
    autorise: Num
    demande: Num
    en_attente: Num
    livre: Num
    pose: Num
    reste_a_demander: Num
    hors_metre: bool
    pct_pose: float | None


# --- Demandes ---------------------------------------------------------------


class VerificationIn(BaseModel):
    ouvrage_id: str
    materiau_id: str
    quantite: Decimal = Field(gt=0)


class VerificationOut(BaseModel):
    quantite_autorisee: Num
    deja_engage: Num
    reste: Num
    quantite: Num
    depassement: Num
    dans_le_metre: bool
    prevu_au_metre: bool
    unite: str


class DemandeIn(VerificationIn):
    motif_depassement: str | None = Field(None, max_length=1000)
    commentaire: str | None = Field(None, max_length=1000)


class DecisionIn(BaseModel):
    approuver: bool
    commentaire: str | None = Field(None, max_length=1000)


class ReceptionIn(BaseModel):
    quantite_livree: Decimal = Field(gt=0)
    note: str | None = None


class DemandeOut(ORM):
    id: str
    numero: int
    chantier_id: str
    ouvrage: OuvrageOut
    materiau: MateriauOut
    quantite: Num
    statut: StatutDemande
    quantite_autorisee: Num
    deja_engage: Num
    depassement: Num
    motif_depassement: str | None
    commentaire: str | None
    demandeur: UserMini
    cree_le: datetime
    validateur: UserMini | None
    decision_le: datetime | None
    commentaire_decision: str | None
    quantite_livree: Num | None


# --- Stock ------------------------------------------------------------------


class SortieIn(BaseModel):
    type: TypeMouvement = TypeMouvement.CONSOMMATION
    materiau_id: str
    quantite: Decimal = Field(gt=0)
    ouvrage_id: str | None = None
    note: str | None = None


class MouvementOut(ORM):
    id: str
    type: TypeMouvement
    quantite: Num
    materiau: MateriauOut
    ouvrage: OuvrageOut | None
    note: str | None
    auteur: UserMini
    cree_le: datetime


class StockLigne(BaseModel):
    materiau: MateriauOut
    quantite: Num
    valeur: Num


class InventaireLigneIn(BaseModel):
    materiau_id: str
    quantite_comptee: Decimal = Field(ge=0)


class InventaireIn(BaseModel):
    date: date | None = None
    note: str | None = None
    lignes: list[InventaireLigneIn] = Field(min_length=1)


class InventaireLigneOut(ORM):
    materiau: MateriauOut
    quantite_theorique: Num
    quantite_comptee: Num
    ecart: Num
    valeur_ecart: Num


class InventaireOut(ORM):
    id: str
    date: date
    note: str | None
    auteur: UserMini
    cree_le: datetime
    lignes: list[InventaireLigneOut]


# --- Ouvriers et pointage -------------------------------------------------------


class OuvrierIn(BaseModel):
    matricule: str | None = None
    nom: str = Field(min_length=1, max_length=120)
    prenom: str | None = None
    metier: str | None = None
    telephone: str | None = None
    taux_journalier: Decimal = Field(Decimal("0"), ge=0)
    actif: bool = True


class OuvrierOut(ORM):
    id: str
    matricule: str | None
    nom: str
    prenom: str | None
    metier: str | None
    telephone: str | None
    taux_journalier: Num
    actif: bool


class AffectationIn(BaseModel):
    ouvrier_id: str
    date_debut: date | None = None
    date_fin: date | None = None


class LignePointageIn(BaseModel):
    ouvrier_id: str
    statut: StatutPointage
    heure_arrivee: time | None = None
    heure_depart: time | None = None
    pause_minutes: int = Field(60, ge=0, le=600)
    note: str | None = None


class PointageIn(BaseModel):
    date: date
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    lignes: list[LignePointageIn] = Field(min_length=1)


class PointageOut(ORM):
    id: str
    ouvrier_id: str
    chantier_id: str
    date: date
    statut: StatutPointage
    heure_arrivee: time | None
    heure_depart: time | None
    pause_minutes: int
    heures: Num
    jours: Num
    note: str | None
    distance_m: int | None
    hors_zone: bool
    pointe_par: UserMini


class PointageResultat(BaseModel):
    enregistres: list[PointageOut]
    refus: list[dict]
    hors_zone: bool
    distance_m: int | None


class FeuilleJourLigne(BaseModel):
    ouvrier: OuvrierOut
    pointage: PointageOut | None


class FeuilleJour(BaseModel):
    date: date
    modifiable: bool
    lignes: list[FeuilleJourLigne]


class RecapLigne(BaseModel):
    ouvrier_id: str
    matricule: str | None
    nom: str
    metier: str | None
    taux_journalier: Num
    jours: Num
    heures: Num
    absences: int
    retards: int
    hors_zone: int
    cout: Num


class Recap(BaseModel):
    du: date
    au: date
    lignes: list[RecapLigne]
    total_jours: Num
    total_heures: Num
    total_cout: Num


# --- Alertes et tableau de bord ---------------------------------------------


class AlerteOut(ORM):
    id: str
    chantier_id: str
    type: TypeAlerte
    gravite: Gravite
    titre: str
    detail: str | None
    lien_type: str | None
    lien_id: str | None
    lue: bool
    cree_le: datetime


class TableauDeBord(BaseModel):
    chantiers_actifs: int
    alertes_non_lues: int
    alertes_critiques: int
    demandes_en_attente: int
    presents_aujourdhui: int
    ouvriers_affectes: int
    devise: str
