import os

os.environ["APP_DATABASE_URL"] = "sqlite://"  # remplacé ci-dessous par une base en mémoire partagée

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import database
from app.database import Base, get_db
from app.main import app
from app.models import Role, Utilisateur
from app.security import create_token, hash_password
from app.services.pointage import aujourdhui


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr("app.main.SessionLocal", TestSession)
    monkeypatch.setattr("app.main.engine", engine)

    def _get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    yield TestSession
    app.dependency_overrides.clear()


class Api:
    """Client de test connecté en tant qu'un utilisateur donné."""

    def __init__(self, client: TestClient, user: Utilisateur):
        self.client = client
        self.user = user
        self.headers = {"Authorization": f"Bearer {create_token(user.id)}"}

    def get(self, url, **kw):
        return self.client.get(url, headers=self.headers, **kw)

    def post(self, url, json=None, **kw):
        return self.client.post(url, json=json, headers=self.headers, **kw)

    def patch(self, url, json=None, **kw):
        return self.client.patch(url, json=json, headers=self.headers, **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, headers=self.headers, **kw)


@pytest.fixture()
def env(db_session):
    """Un chantier prêt à l'emploi : comptes, ouvrage salle de bain, carrelage au métré, 2 ouvriers."""
    with db_session() as db:
        users = {}
        for role, email in ((Role.ADMIN, "a@x.fr"), (Role.CONDUCTEUR, "c@x.fr"), (Role.CHEF, "chef@x.fr"),
                            (Role.CHEF, "chef2@x.fr")):
            u = Utilisateur(nom=email, email=email, mot_de_passe_hash=hash_password("motdepasse"), role=role)
            db.add(u)
            users[email] = u
        db.commit()

    client = TestClient(app)
    admin = Api(client, users["a@x.fr"])
    cond = Api(client, users["c@x.fr"])
    chef = Api(client, users["chef@x.fr"])
    chef2 = Api(client, users["chef2@x.fr"])

    ch = admin.post("/api/chantiers", {
        "nom": "Villa A", "latitude": 36.75, "longitude": 3.05, "rayon_m": 300,
        "chef_id": chef.user.id, "conducteur_id": cond.user.id,
    }).json()
    ch2 = admin.post("/api/chantiers", {"nom": "Immeuble B", "chef_id": chef2.user.id}).json()
    sdb = cond.post(f"/api/chantiers/{ch['id']}/ouvrages", {"nom": "Salle de bain RDC"}).json()
    carrelage = cond.post("/api/materiaux", {"nom": "Carrelage 60x60", "unite": "m²", "prix_unitaire": 20}).json()
    colle = cond.post("/api/materiaux", {"nom": "Colle C2", "unite": "sac", "prix_unitaire": 10}).json()
    # 10 m² nets + 10 % de perte = 11 m² autorisés
    cond.post(f"/api/chantiers/{ch['id']}/metre", {
        "ouvrage_id": sdb["id"], "materiau_id": carrelage["id"], "quantite_nette": 10, "taux_perte_pct": 10,
    })
    o1 = cond.post("/api/ouvriers", {"nom": "Saidi", "prenom": "Ahmed", "taux_journalier": 40}).json()
    o2 = cond.post("/api/ouvriers", {"nom": "Haddad", "taux_journalier": 30}).json()
    for o in (o1, o2):
        cond.post(f"/api/chantiers/{ch['id']}/equipe", {"ouvrier_id": o["id"]})
    admin.post(f"/api/chantiers/{ch2['id']}/equipe", {"ouvrier_id": o1["id"]})

    class Env:
        pass

    e = Env()
    e.admin, e.cond, e.chef, e.chef2 = admin, cond, chef, chef2
    e.ch, e.ch2, e.sdb, e.carrelage, e.colle, e.o1, e.o2 = ch, ch2, sdb, carrelage, colle, o1, o2
    e.today = aujourdhui().isoformat()
    return e
