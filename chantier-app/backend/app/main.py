"""Point d'entrée : `uvicorn app.main:app --reload`"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Role, Utilisateur
from app.routers import alertes, auth, chantiers, materiel, pointage, referentiels
from app.security import hash_password
from app.services.erreurs import ErreurMetier

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def init_db() -> None:
    Base.metadata.create_all(engine)
    s = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(Utilisateur.id).limit(1)) is None:
            db.add(
                Utilisateur(
                    nom="Administrateur",
                    email=s.admin_email.lower(),
                    mot_de_passe_hash=hash_password(s.admin_password),
                    role=Role.ADMIN,
                )
            )
            db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Gestion de chantiers BTP", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ErreurMetier)
async def erreur_metier(_: Request, exc: ErreurMetier):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, **exc.details})


for r in (auth, chantiers, referentiels, materiel, pointage, alertes):
    app.include_router(r.router)


@app.get("/api/sante")
def sante():
    return {"ok": True}


# En production, le backend sert aussi l'application web construite (frontend/dist).
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{chemin:path}", include_in_schema=False)
    def spa(chemin: str):
        if chemin.startswith("api/"):
            return JSONResponse({"detail": "Introuvable"}, status_code=404)
        fichier = (FRONTEND_DIST / chemin).resolve()
        if chemin and fichier.is_file() and FRONTEND_DIST in fichier.parents:
            return FileResponse(fichier)
        return FileResponse(FRONTEND_DIST / "index.html")
