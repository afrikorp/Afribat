"""Données de démonstration : `python -m app.seed`

Crée trois comptes (mot de passe « demo1234 »), un chantier avec ses ouvrages,
le métré de la salle de bain et de la cuisine, et une petite équipe d'ouvriers.
"""

from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.main import init_db
from app.models import Affectation, BesoinMateriau, Chantier, Materiau, Ouvrage, Ouvrier, Role, Utilisateur
from app.security import hash_password

MOT_DE_PASSE = "demo1234"


def seed() -> None:
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(Chantier.id).limit(1)):
            print("La base contient déjà des chantiers : rien n'a été ajouté.")
            return

        def user(nom: str, email: str, role: Role) -> Utilisateur:
            u = db.scalar(select(Utilisateur).where(Utilisateur.email == email))
            if u is None:
                u = Utilisateur(nom=nom, email=email, mot_de_passe_hash=hash_password(MOT_DE_PASSE), role=role)
                db.add(u)
            return u

        conducteur = user("Karim Conducteur", "conducteur@chantier.local", Role.CONDUCTEUR)
        chef = user("Yacine Chef", "chef@chantier.local", Role.CHEF)
        db.flush()

        m = {
            nom: Materiau(reference=ref, nom=nom, unite=unite, prix_unitaire=Decimal(prix))
            for ref, nom, unite, prix in [
                ("CAR-6060", "Carrelage sol 60×60", "m²", "18.50"),
                ("FAI-2540", "Faïence murale 25×40", "m²", "14.00"),
                ("COL-C2", "Colle carrelage C2 (sac 25 kg)", "sac", "9.80"),
                ("JNT-5", "Joint carrelage (sac 5 kg)", "sac", "6.20"),
                ("CIM-425", "Ciment CPJ 42.5 (sac 50 kg)", "sac", "7.50"),
                ("SAB-01", "Sable 0/4", "m³", "25.00"),
            ]
        }
        db.add_all(m.values())

        chantier = Chantier(
            nom="Villa Les Oliviers",
            client="M. et Mme Benali",
            adresse="12 rue des Oliviers",
            latitude=36.7538,
            longitude=3.0588,
            rayon_m=300,
            chef_id=chef.id,
            conducteur_id=conducteur.id,
        )
        db.add(chantier)
        db.flush()

        sdb = Ouvrage(chantier_id=chantier.id, code="SDB-RDC", nom="Salle de bain RDC")
        cuisine = Ouvrage(chantier_id=chantier.id, code="CUI", nom="Cuisine")
        dalle = Ouvrage(chantier_id=chantier.id, code="GO-DAL", nom="Dalle RDC")
        db.add_all([sdb, cuisine, dalle])
        db.flush()

        db.add_all(
            [
                BesoinMateriau(ouvrage_id=sdb.id, materiau_id=m["Carrelage sol 60×60"].id,
                               quantite_nette=Decimal("6.40"), taux_perte_pct=Decimal("10"),
                               note_calcul="3,20 m × 2,00 m"),
                BesoinMateriau(ouvrage_id=sdb.id, materiau_id=m["Faïence murale 25×40"].id,
                               quantite_nette=Decimal("21.00"), taux_perte_pct=Decimal("10"),
                               note_calcul="Périmètre 10,40 m × 2,20 m - porte 1,88 m²"),
                BesoinMateriau(ouvrage_id=sdb.id, materiau_id=m["Colle carrelage C2 (sac 25 kg)"].id,
                               quantite_nette=Decimal("5"), taux_perte_pct=Decimal("0")),
                BesoinMateriau(ouvrage_id=sdb.id, materiau_id=m["Joint carrelage (sac 5 kg)"].id,
                               quantite_nette=Decimal("2"), taux_perte_pct=Decimal("0")),
                BesoinMateriau(ouvrage_id=cuisine.id, materiau_id=m["Carrelage sol 60×60"].id,
                               quantite_nette=Decimal("14.00"), taux_perte_pct=Decimal("8"),
                               note_calcul="4,00 m × 3,50 m"),
                BesoinMateriau(ouvrage_id=dalle.id, materiau_id=m["Ciment CPJ 42.5 (sac 50 kg)"].id,
                               quantite_nette=Decimal("140"), taux_perte_pct=Decimal("3")),
                BesoinMateriau(ouvrage_id=dalle.id, materiau_id=m["Sable 0/4"].id,
                               quantite_nette=Decimal("9"), taux_perte_pct=Decimal("5")),
            ]
        )

        ouvriers = [
            Ouvrier(matricule="OUV-001", nom="Saidi", prenom="Ahmed", metier="Maçon", taux_journalier=Decimal("45")),
            Ouvrier(matricule="OUV-002", nom="Mansouri", prenom="Rachid", metier="Carreleur", taux_journalier=Decimal("50")),
            Ouvrier(matricule="OUV-003", nom="Haddad", prenom="Sofiane", metier="Manœuvre", taux_journalier=Decimal("30")),
            Ouvrier(matricule="OUV-004", nom="Belkacem", prenom="Omar", metier="Ferrailleur", taux_journalier=Decimal("45")),
        ]
        db.add_all(ouvriers)
        db.flush()
        db.add_all([Affectation(ouvrier_id=o.id, chantier_id=chantier.id) for o in ouvriers])
        db.commit()
        print("Données de démonstration créées.")
        print(f"  conducteur@chantier.local / {MOT_DE_PASSE}")
        print(f"  chef@chantier.local       / {MOT_DE_PASSE}")


if __name__ == "__main__":
    seed()
