"""Mots de passe, jetons de connexion et contrôle des droits."""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Chantier, Role, Utilisateur

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: str) -> str:
    s = get_settings()
    payload = {"sub": user_id, "exp": datetime.now(UTC) + timedelta(minutes=s.token_minutes)}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Utilisateur:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Session expirée, reconnectez-vous", {"WWW-Authenticate": "Bearer"}
    )
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise unauthorized from None
    user = db.get(Utilisateur, payload.get("sub"))
    if user is None or not user.actif:
        raise unauthorized
    return user


def require_roles(*roles: Role):
    def dep(user: Utilisateur = Depends(current_user)) -> Utilisateur:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Action non autorisée pour votre rôle")
        return user

    return dep


bureau = require_roles(Role.ADMIN, Role.CONDUCTEUR)
admin_only = require_roles(Role.ADMIN)


def can_access(user: Utilisateur, chantier: Chantier) -> bool:
    if user.role == Role.ADMIN:
        return True
    if user.role == Role.CONDUCTEUR:
        return chantier.conducteur_id == user.id
    return chantier.chef_id == user.id


def get_chantier_for(db: Session, user: Utilisateur, chantier_id: str) -> Chantier:
    chantier = db.get(Chantier, chantier_id)
    if chantier is None or not can_access(user, chantier):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chantier introuvable")
    return chantier
