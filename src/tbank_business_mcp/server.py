"""Read-only account tools and payment-draft preparation for T-Bank Business."""

from __future__ import annotations

import json
import os
import re
import ssl
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import truststore
from mcp.server.fastmcp import FastMCP


API_BASE_URL = os.getenv(
    "TBANK_API_BASE_URL", "https://business.tbank.ru/openapi/api"
).rstrip("/") + "/"
_parsed_base_url = urlparse(API_BASE_URL)
if _parsed_base_url.scheme != "https" or not _parsed_base_url.netloc:
    raise RuntimeError("TBANK_API_BASE_URL must be an HTTPS URL")

TIMEOUT_SECONDS = float(os.getenv("TBANK_TIMEOUT_SECONDS", "30"))
MAX_STATEMENT_LIMIT = int(os.getenv("TBANK_STATEMENT_MAX_LIMIT", "5000"))

mcp = FastMCP(
    "tbank-business",
    instructions=(
        "Инструменты для собственного расчётного счёта ИП в Т-Банк Бизнесе. "
        "Сведения о счёте и выписка доступны только в рамках прав API-токена. "
        "Создание платёжного черновика меняет данные в банковском кабинете, "
        "но не подписывает и не отправляет платёж. Перед созданием черновика "
        "сверяй реквизиты, сумму и назначение с пользователем. Не проси присылать токен в чат."
    ),
)


class TBankAPIError(RuntimeError):
    """Sanitized error returned by T-Bank API."""


def _client() -> httpx.AsyncClient:
    token = os.getenv("TBANK_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TBANK_API_TOKEN не задан. Настройте его в окружении MCP-сервера."
        )

    ca_bundle = os.getenv("TBANK_CA_BUNDLE")
    verify: bool | str | ssl.SSLContext = (
        ca_bundle
        if ca_bundle
        else truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )
    return httpx.AsyncClient(
        base_url=API_BASE_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
        verify=verify,
    )


async def _request(
    method: str,
    path: str,
    *,
    params: Any = None,
    body: dict[str, Any] | None = None,
) -> Any:
    request_id = str(uuid.uuid4())
    try:
        async with _client() as client:
            response = await client.request(
                method,
                path.lstrip("/"),
                params=params,
                json=body,
                headers={"X-Request-Id": request_id},
            )
    except httpx.TimeoutException as exc:
        raise TBankAPIError("Т-Банк не ответил вовремя. Повторите запрос позже.") from exc
    except httpx.HTTPError as exc:
        # Never include request headers or the token in tool output.
        raise TBankAPIError(f"Не удалось связаться с T-API: {type(exc).__name__}.") from exc

    if response.is_error:
        safe_messages = {
            400: "Т-Банк отклонил запрос как некорректный (400). Проверьте параметры.",
            401: "Токен T-API не принят (401). Проверьте токен и срок его действия.",
            403: "У токена нет разрешения на эту операцию (403).",
            422: "Т-Банк не смог обработать данные запроса (422).",
            429: "Превышен лимит запросов T-API (429). Повторите позже.",
        }
        raise TBankAPIError(
            safe_messages.get(
                response.status_code,
                f"T-API вернул HTTP {response.status_code}; request_id={request_id}.",
            )
        )

    if response.status_code == 204 or not response.content:
        return {"ok": True, "request_id": request_id}
    try:
        return response.json()
    except ValueError:
        return {"content_type": response.headers.get("content-type"), "text": response.text}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _validate_account_number(value: str) -> str:
    if not re.fullmatch(r"\d{20}", value):
        raise ValueError("Номер расчётного счёта должен содержать 20 цифр.")
    return value


@mcp.tool()
async def list_accounts(with_invest: bool = False) -> str:
    """Показать расчётные счета компании, статусы и доступные остатки.

    Вызов не меняет данные в банке. Инвестиционные счета не запрашиваются,
    если with_invest=false.
    """
    return _json(await _request("GET", "/v4/bank-accounts", params={"withInvest": with_invest}))


@mcp.tool()
async def get_statement(
    account_number: str,
    from_date: str,
    to_date: str | None = None,
    operation_status: str = "All",
    limit: int = 1000,
    cursor: str | None = None,
    with_balances: bool = True,
    categories: list[str] | None = None,
    inns: list[str] | None = None,
) -> str:
    """Получить страницу операций по расчётному счёту за период.

    from_date и to_date — ISO 8601, например 2026-11-01T00:00:00Z.
    to_date не включается в период. Если ответ содержит nextCursor,
    передайте его в cursor для следующей страницы.
    operation_status: All, Authorization или Transaction.
    """
    _validate_account_number(account_number)
    if operation_status not in {"All", "Authorization", "Transaction"}:
        raise ValueError("operation_status: допустимы All, Authorization, Transaction.")
    if not (1 <= limit <= min(MAX_STATEMENT_LIMIT, 5000)):
        raise ValueError(f"limit должен быть от 1 до {min(MAX_STATEMENT_LIMIT, 5000)}.")
    try:
        datetime.fromisoformat(from_date.replace("Z", "+00:00"))
        if to_date:
            datetime.fromisoformat(to_date.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Даты должны быть в формате ISO 8601.") from exc

    params: list[tuple[str, str]] = [
        ("accountNumber", account_number),
        ("from", from_date),
        ("operationStatus", operation_status),
        ("limit", str(limit)),
        ("withBalances", str(with_balances).lower()),
    ]
    if to_date:
        params.append(("to", to_date))
    if cursor:
        if len(cursor) > 100:
            raise ValueError("cursor не должен превышать 100 символов.")
        params.append(("cursor", cursor))
    for category in categories or []:
        params.append(("categories", category))
    for inn in inns or []:
        if not re.fullmatch(r"\d{10}|\d{12}", inn):
            raise ValueError(f"Некорректный ИНН в фильтре: {inn}")
        params.append(("inns", inn))

    return _json(await _request("GET", "/v1/statement", params=params))


@mcp.tool()
async def create_payment_draft(
    document_number: str,
    amount: float,
    recipient_name: str,
    recipient_inn: str,
    recipient_kpp: str,
    recipient_account: str,
    recipient_bik: str,
    account_number: str,
    payment_purpose: str,
    execution_date: str | None = None,
) -> str:
    """Создать черновик обычного рублёвого платежа по реквизитам.

    Это создаёт платёжный документ в кабинете Т-Бизнеса, но не подписывает
    и не отправляет его. Перед вызовом проверьте каждый реквизит с пользователем.
    Предназначено для обычного перевода; бюджетные платежи здесь не поддержаны.
    """
    _validate_account_number(account_number)
    if not re.fullmatch(r"\d{20}", recipient_account):
        raise ValueError("Расчётный счёт получателя должен содержать 20 цифр.")
    if not re.fullmatch(r"\d{9}", recipient_bik):
        raise ValueError("БИК получателя должен содержать 9 цифр.")
    if not re.fullmatch(r"\d{12}|\d{10}|0", recipient_inn):
        raise ValueError("ИНН получателя должен содержать 10 или 12 цифр; для физлица допускается 0.")
    if not re.fullmatch(r"\d{9}|0", recipient_kpp):
        raise ValueError("КПП получателя должен содержать 9 цифр или быть 0.")
    if not re.fullmatch(r"\d{1,6}", document_number):
        raise ValueError("Номер документа должен содержать от 1 до 6 цифр.")
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")
    if not recipient_name.strip():
        raise ValueError("Укажите имя или наименование получателя.")
    if not payment_purpose.strip() or len(payment_purpose) > 210:
        raise ValueError("Назначение платежа обязательно и не должно превышать 210 символов.")

    body: dict[str, Any] = {
        "documentNumber": document_number,
        "amount": amount,
        "recipientName": recipient_name.strip(),
        "inn": recipient_inn,
        "kpp": recipient_kpp,
        "bankAcnt": recipient_account,
        "bankBik": recipient_bik,
        "accountNumber": account_number,
        "paymentPurpose": payment_purpose.strip(),
    }
    if execution_date:
        try:
            datetime.fromisoformat(execution_date.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("execution_date должен быть ISO 8601.") from exc
        body["date"] = execution_date

    result = await _request("POST", "/v1/payment/create", body=body)
    return _json({
        "warning": "Создан черновик в Т-Бизнесе. Он не подписан и не отправлен.",
        "result": result,
    })


@mcp.tool()
async def get_payment_draft_status(document_ids: list[str]) -> str:
    """Проверить статусы черновиков платежных поручений (1–100 documentId)."""
    if not 1 <= len(document_ids) <= 100:
        raise ValueError("Передайте от 1 до 100 documentId.")
    return _json(
        await _request(
            "POST", "/v1/payment/status", body={"documentIds": document_ids}
        )
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
