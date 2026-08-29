"""First-run setup, login, and current-user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...auth import create_access_token, current_user, user_count
from ...crypto import hash_password, needs_rehash, verify_password
from ...database import get_db
from ...models import User, utcnow
from ...schemas import LoginRequest, SetupRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
def auth_status(db: Session = Depends(get_db)) -> dict:
    return {"needs_setup": user_count(db) == 0}


@router.post("/setup", response_model=TokenResponse)
def setup(payload: SetupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    if user_count(db) > 0:
        raise HTTPException(409, "setup already completed")
    user = User(
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or str(payload.email).split("@")[0],
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token, ttl = create_access_token(user.id)
    return TokenResponse(access_token=token, expires_in=ttl)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == str(payload.email).lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "invalid credentials")
    if not user.is_active:
        raise HTTPException(403, "account disabled")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.last_login_at = utcnow()
    db.commit()
    token, ttl = create_access_token(user.id)
    return TokenResponse(access_token=token, expires_in=ttl)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user
