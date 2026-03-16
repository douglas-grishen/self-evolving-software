"""Authentication API — login and current user info."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import authenticate_user, create_access_token, get_current_admin
from app.database import get_db
from app.models.admin import AdminUser
from app.schemas.auth import AdminUserResponse, LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate an admin user and return a JWT access token."""
    user = await authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Update last_login
    user.last_login = datetime.now(timezone.utc)
    await db.flush()

    access_token = create_access_token(data={"sub": user.username})
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=AdminUserResponse)
async def get_me(
    current_user: AdminUser = Depends(get_current_admin),
) -> AdminUser:
    """Return the currently authenticated admin user."""
    return current_user
