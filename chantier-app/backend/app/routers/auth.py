from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Utilisateur
from app.schemas import Token, UtilisateurCreate, UtilisateurOut, UtilisateurUpdate
from app.security import admin_only, create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["Utilisateurs"])


class LoginIn(BaseModel):
    email: str
    mot_de_passe: str


@router.post("/auth/login", response_model=Token)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(Utilisateur).where(Utilisateur.email == data.email.strip().lower()))
    if user is None or not user.actif or not verify_password(data.mot_de_passe, user.mot_de_passe_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail ou mot de passe incorrect")
    return Token(access_token=create_token(user.id))


@router.get("/auth/moi", response_model=UtilisateurOut)
def moi(user: Utilisateur = Depends(current_user)):
    return user


@router.get("/utilisateurs", response_model=list[UtilisateurOut])
def lister(db: Session = Depends(get_db), _: Utilisateur = Depends(current_user)):
    # Tout utilisateur connecté voit la liste (pour choisir un chef ou un conducteur).
    return db.scalars(select(Utilisateur).order_by(Utilisateur.nom)).all()


@router.post("/utilisateurs", response_model=UtilisateurOut, status_code=201)
def creer(data: UtilisateurCreate, db: Session = Depends(get_db), _: Utilisateur = Depends(admin_only)):
    email = data.email.lower()
    if db.scalar(select(Utilisateur).where(Utilisateur.email == email)):
        raise HTTPException(409, "Un utilisateur avec cet e-mail existe déjà")
    user = Utilisateur(
        nom=data.nom,
        email=email,
        mot_de_passe_hash=hash_password(data.mot_de_passe),
        role=data.role,
        telephone=data.telephone,
    )
    db.add(user)
    db.commit()
    return user


@router.patch("/utilisateurs/{user_id}", response_model=UtilisateurOut)
def modifier(
    user_id: str, data: UtilisateurUpdate, db: Session = Depends(get_db), admin: Utilisateur = Depends(admin_only)
):
    user = db.get(Utilisateur, user_id)
    if user is None:
        raise HTTPException(404, "Utilisateur introuvable")
    changes = data.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("actif") is False or changes.get("role", user.role) != user.role):
        raise HTTPException(409, "Vous ne pouvez pas désactiver ni changer le rôle de votre propre compte")
    if "mot_de_passe" in changes:
        user.mot_de_passe_hash = hash_password(changes.pop("mot_de_passe"))
    for k, v in changes.items():
        setattr(user, k, v)
    db.commit()
    return user
