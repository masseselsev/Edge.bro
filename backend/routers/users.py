import os
from datetime import datetime, timedelta
from typing import Union, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
import jwt
import bcrypt
from sqlalchemy.orm import Session
from database import get_db
import models
import schemas

# Re-exported so this module keeps working as the login/user-management
# router; the definitions live in backend/auth.py so that nothing has to
# import a guard from a sibling router.
from auth import (  # noqa: F401
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    JWT_SECRET_KEY,
    create_access_token,
    get_current_auth,
    get_password_hash,
    require_admin,
    require_admin_plus_or_superadmin,
    require_kiosk_or_admin,
    require_superadmin,
    require_user,
    verify_password,
)

router = APIRouter()


# --- Endpoints ---

@router.post("/api/auth/login")
def login(payload: schemas.LoginPayload, response: Response, request: Request = None, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == payload.username).first()
    from database import log_user_action
    if not user or not verify_password(payload.password, user.hashed_password):
        log_user_action(db, payload.username, "Login Failed", "Failed login attempt (invalid username or password)", request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    # Generate JWT
    token = create_access_token(data={"sub": user.username})

    # Set secure HTTP-only cookie
    # secure=False is used for local HTTP testing, but is_secure can be derived from request scheme
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False, # Set to True in production with TLS
    )

    log_user_action(db, user.username, "Login", "User logged in successfully", request)

    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "is_superadmin": user.is_superadmin,
        "is_admin_plus": user.is_admin_plus
    }


@router.post("/api/auth/logout")
def logout(response: Response, db: Session = Depends(get_db), current_user = Depends(require_admin), request: Request = None):
    response.delete_cookie(key="admin_session", httponly=True, samesite="lax")
    from database import log_user_action
    log_user_action(db, current_user.username, "Logout", "User logged out", request)
    return {"status": "SUCCESS"}


@router.get("/api/auth/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(require_admin)):
    return current_user


@router.put("/api/users/profile", response_model=schemas.UserResponse)
def update_profile(
    payload: schemas.UserSelfUpdate,
    current_user: models.User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if payload.name is not None:
        current_user.name = payload.name
    if payload.phone is not None:
        current_user.phone = payload.phone
    if payload.telegram_id is not None:
        current_user.telegram_id = payload.telegram_id
    if payload.password is not None:
        if len(payload.password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 6 characters long"
            )
        current_user.hashed_password = get_password_hash(payload.password)

    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/api/users", response_model=List[schemas.UserResponse])
def list_users(
    current_user: models.User = Depends(require_admin_plus_or_superadmin),
    db: Session = Depends(get_db)
):
    """
    Lists all administrator users. Restricted to Admin+ or Superadmin.
    """
    return db.query(models.User).order_by(models.User.username).all()


@router.post("/api/users", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: schemas.UserCreate,
    request: Request = None,
    current_user: models.User = Depends(require_admin_plus_or_superadmin),
    db: Session = Depends(get_db)
):
    """
    Creates a new administrator user. Restricted to Admin+ or Superadmin.
    """
    if not current_user.is_superadmin:
        # Standard admin+ cannot create superadmins or other admin+ users
        if payload.is_admin_plus:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Standard admin+ users cannot create admin+ users."
            )

    existing = db.query(models.User).filter(models.User.username == payload.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this username already exists."
        )

    db_user = models.User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        name=payload.name,
        phone=payload.phone,
        telegram_id=payload.telegram_id,
        comment=payload.comment,
        is_superadmin=False,
        is_admin_plus=payload.is_admin_plus if payload.is_admin_plus is not None else False
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    from database import log_user_action
    log_user_action(db, current_user.username, "Create User", f"Created administrator user '{db_user.username}' (admin_plus={db_user.is_admin_plus})", request)
    return db_user


@router.put("/api/users/{user_id}", response_model=schemas.UserResponse)
def update_user(
    user_id: int,
    payload: schemas.UserUpdate,
    request: Request = None,
    current_user: models.User = Depends(require_admin_plus_or_superadmin),
    db: Session = Depends(get_db)
):
    """
    Updates an administrator user. Restricted to Admin+ or Superadmin.
    """
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found."
        )

    if not current_user.is_superadmin:
        # Standard admin+ cannot modify superadmins or admin+ users
        if user.is_superadmin or user.is_admin_plus:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Standard admin+ users cannot modify superadmin or admin+ accounts."
            )
        # Standard admin+ cannot promote a user to admin+
        if payload.is_admin_plus is True:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Standard admin+ users cannot promote standard administrators to admin+."
            )

    changes = []
    fields = [
        ("name", "Name"),
        ("phone", "Contact"),
        ("telegram_id", "Telegram ID"),
        ("comment", "Comment"),
        ("is_admin_plus", "Admin+ Privilege"),
    ]
    for attr, label in fields:
        old_val = getattr(user, attr, None)
        new_val = getattr(payload, attr, None)
        if new_val is not None and old_val != new_val:
            changes.append(f"{label}: '{old_val}' ➔ '{new_val}'")

    if payload.password is not None:
        if len(payload.password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 6 characters long."
            )
        user.hashed_password = get_password_hash(payload.password)
        changes.append("Password updated")

    if payload.name is not None:
        user.name = payload.name
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.telegram_id is not None:
        user.telegram_id = payload.telegram_id
    if payload.comment is not None:
        user.comment = payload.comment
    
    if payload.is_admin_plus is not None:
        # Only superadmin can modify admin+ status
        if current_user.is_superadmin:
            user.is_admin_plus = payload.is_admin_plus

    db.commit()
    db.refresh(user)
    from database import log_user_action
    details_str = f"Update User '{user.username}': {', '.join(changes)}" if changes else f"Updated administrator user '{user.username}' (no values changed)"
    log_user_action(db, current_user.username, "Update User", details_str, request)
    return user


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    request: Request = None,
    current_user: models.User = Depends(require_admin_plus_or_superadmin),
    db: Session = Depends(get_db)
):
    """
    Deletes an administrator user. Restricted to Admin+ or Superadmin. Cannot delete oneself, or delete a superadmin. Standard admin+ cannot delete admin+.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account."
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found."
        )

    if not current_user.is_superadmin:
        # Standard admin+ cannot delete other admin+ or superadmin accounts
        if user.is_admin_plus or user.is_superadmin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Standard admin+ users cannot delete superadmin or admin+ accounts."
            )

    if user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Super-administrator accounts cannot be deleted."
        )

    db.delete(user)
    db.commit()
    from database import log_user_action
    log_user_action(db, current_user.username, "Delete User", f"Deleted administrator user '{user.username}'", request)


@router.get("/api/users/audit-logs", response_model=List[schemas.AuditLogResponse])
def get_audit_logs(
    type: Optional[str] = None,
    current_user: models.User = Depends(require_admin_plus_or_superadmin),
    db: Session = Depends(get_db)
):
    """
    Lists audit logs of user actions. Restricted to Admin+ or Superadmin.
    """
    query = db.query(models.AuditLog)
    if type == "kiosk":
        query = query.filter(models.AuditLog.username.like("Kiosk%"))
    elif type == "admin":
        query = query.filter(~models.AuditLog.username.like("Kiosk%"))
    return query.order_by(models.AuditLog.created_at.desc()).limit(1000).all()


