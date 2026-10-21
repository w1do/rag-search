from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.deps import Services, get_services, require_owner

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class LoginOut(BaseModel):
    token: str
    expires_at: datetime


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, services: Services = Depends(get_services)) -> LoginOut:
    token, expires_at = services.auth.login(body.email, body.password)
    return LoginOut(token=token, expires_at=expires_at)


@router.post("/logout", status_code=204)
def logout(token: str = Depends(require_owner), services: Services = Depends(get_services)) -> Response:
    services.auth.logout(token)
    return Response(status_code=204)


@router.get("/me")
def me(_: str = Depends(require_owner)) -> dict[str, str]:
    return {"role": "owner"}
