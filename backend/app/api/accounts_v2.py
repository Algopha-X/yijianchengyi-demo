"""Account workflows with explicit local verification and private workspaces."""

import hashlib
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi import Path as PathParam
from pydantic import Field, StrictBool

from app.api.studio import Service, studio
from app.models._base import ContractModel
from app.services import accounts
from app.services.studio import now, require

router = APIRouter(prefix="/api/v2/auth", tags=["SRS 账号扩展"])


def session_id(request: Request):
    return accounts.raw_session(studio(request), request.cookies.get("ycy_session"))


RawSession = Annotated[str, Depends(session_id)]
Address = Annotated[str, Field(min_length=5, max_length=254)]
Password = Annotated[str, Field(min_length=8, max_length=128)]


class CodeRequest(ContractModel):
    address: Address
    purpose: Literal["register", "reset"]


class VerifyRequest(ContractModel):
    address: Address
    password: Password
    challenge_id: UUID
    code: Annotated[str, Field(pattern=r"^\d{6}$")]


class RegisterRequest(VerifyRequest):
    nickname: Annotated[str, Field(min_length=1, max_length=40)]


class LoginRequest(ContractModel):
    address: Address
    password: Password
    import_anonymous: StrictBool = False


class ProfileRequest(ContractModel):
    nickname: Annotated[str, Field(min_length=1, max_length=40)]


class ChangePasswordRequest(ContractModel):
    old_password: Password
    new_password: Password


class AdminUserRequest(ContractModel):
    role: Literal["user", "reviewer", "admin"] | None = None
    banned: StrictBool | None = None


def cookie(response, service, token):
    response.set_cookie(
        "ycy_session",
        token,
        max_age=service.settings.session_ttl_days * 86400,
        httponly=True,
        secure=service.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["X-Session-Scope"] = hashlib.sha256(
        service.session(token).encode()
    ).hexdigest()


@router.get("/me")
def me(service: Service, current: RawSession):
    with service.db.transaction() as connection:
        account = accounts.current_account(connection, current)
        if account:
            require(not account["banned"], 403, "ACCOUNT_BANNED", "账号已停用，请联系管理员")
        anonymous_count = connection.execute(
            "SELECT count(*) FROM tasks WHERE owner_session_id=?", (current,)
        ).fetchone()[0]
    return {
        "account": accounts.account_view(account) if account else None,
        "verificationMode": service.settings.verification_mode,
        "anonymousTaskCount": anonymous_count if not account else 0,
    }


@router.post("/verification")
def verification(payload: CodeRequest, service: Service, current: RawSession):
    return accounts.issue_code(service, payload.address, payload.purpose)


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, response: Response, service: Service, current: RawSession):
    require(bool(payload.nickname.strip()), 422, "VALIDATION_ERROR", "昵称不能为空")
    account = accounts.register(service, current, payload)
    cookie(response, service, accounts.rotate_token(service, current))
    return {"account": account}


@router.post("/login")
def login(payload: LoginRequest, response: Response, service: Service, current: RawSession):
    account = accounts.login(service, current, payload)
    login_session = account.pop("_sessionId")
    cookie(response, service, accounts.rotate_token(service, login_session))
    return {"account": account}


@router.post("/logout")
def logout(response: Response, service: Service, current: RawSession):
    with service.db.transaction() as connection:
        token = accounts.take_anonymous_token(service, connection, current)
        connection.execute("DELETE FROM account_sessions WHERE session_id=?", (current,))
        connection.execute("UPDATE sessions SET expires_at=? WHERE id=?", (now(), current))
    if not token:
        token, _ = service.create_session(None)
    cookie(response, service, token)
    return {"account": None}


@router.post("/reset-password")
def reset_password(
    payload: VerifyRequest, response: Response, service: Service, current: RawSession
):
    with service.db.transaction() as connection:
        account = accounts.current_account(connection, current)
        previous_account = account["id"] if account else None
    changed_account = accounts.reset_password(service, payload)
    if previous_account == changed_account:
        restored = accounts.restore_anonymous(service, current)
        if restored:
            cookie(response, service, restored)
        else:
            response.delete_cookie(
                "ycy_session",
                httponly=True,
                secure=service.settings.cookie_secure,
                samesite="lax",
                path="/",
            )
            response.headers["X-Session-Scope"] = ""
    return {"message": "密码已重置，请重新登录；已登录会话已失效"}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest, response: Response, service: Service, current: RawSession
):
    token, account = accounts.change_password(
        service, current, payload.old_password, payload.new_password
    )
    cookie(response, service, token)
    return {"account": account, "message": "密码已修改，其他登录会话已失效"}


@router.get("/admin/users")
def admin_users(
    service: Service,
    current: RawSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return accounts.list_accounts(service, current, limit, offset)


@router.patch("/admin/users/{accountId}")
def admin_user(
    account_id: Annotated[UUID, PathParam(alias="accountId")],
    payload: AdminUserRequest,
    service: Service,
    current: RawSession,
):
    changes = payload.model_dump(exclude_unset=True)
    return {"account": accounts.update_account(service, current, str(account_id), changes)}


@router.patch("/profile")
def profile(payload: ProfileRequest, service: Service, current: RawSession):
    require(bool(payload.nickname.strip()), 422, "VALIDATION_ERROR", "昵称不能为空")
    with service.db.transaction() as connection:
        account = accounts.current_account(connection, current)
        require(account is not None, 401, "LOGIN_REQUIRED", "请先登录")
        require(not account["banned"], 403, "ACCOUNT_BANNED", "账号已停用，请联系管理员")
        connection.execute(
            "UPDATE accounts SET nickname=? WHERE id=?", (payload.nickname.strip(), account["id"])
        )
        return {"account": accounts.account_view(accounts.current_account(connection, current))}
