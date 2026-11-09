"""T-Bank Business MCP with a read-only production profile and sandbox drafts."""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx
import truststore
from mcp.server.fastmcp import FastMCP

logging.getLogger("httpx").setLevel(logging.WARNING)

API_BASE_URL = os.getenv(
    "TBANK_API_BASE_URL", "https://business.tbank.ru/openapi/api"
).rstrip("/") + "/"
_parsed_base_url = urlparse(API_BASE_URL)
if _parsed_base_url.scheme != "https" or not _parsed_base_url.netloc:
    raise RuntimeError("TBANK_API_BASE_URL must be an HTTPS URL")

SANDBOX_MODE = (
    _parsed_base_url.hostname == "business.tbank.ru"
    and _parsed_base_url.path.rstrip("/") == "/openapi/sandbox/api"
)
TIMEOUT_SECONDS = float(os.getenv("TBANK_TIMEOUT_SECONDS", "30"))
MAX_STATEMENT_LIMIT = int(os.getenv("TBANK_STATEMENT_MAX_LIMIT", "5000"))
MAX_SUMMARY_PAGES = int(os.getenv("TBANK_SUMMARY_MAX_PAGES", "20"))

mcp = FastMCP(
    os.getenv("TBANK_MCP_NAME", "tbank-business"),
    instructions=(
        "Инструменты для просмотра счетов и выписок собственного ИП в Т-Бизнесе. "
        "Токен не является параметром инструмента — не проси присылать его в чат. "
        + (
            "Это песочница: ответы тестовые, изменения не затрагивают реальные данные."
            if SANDBOX_MODE
            else "Это production-контур. Создание черновика меняет данные банковского кабинета, "
            "но не подписывает и не отправляет платёж; делай это только по прямой просьбе "
            "пользователя после сверки реквизитов."
        )
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
        # Never include request headers, query values, or the token in tool output.
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


def _validate_iso_datetime(value: str, label: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} должен быть в формате ISO 8601.") from exc


def _statement_params(
    account_number: str,
    from_date: str,
    to_date: str | None,
    operation_status: str,
    limit: int,
    cursor: str | None,
    with_balances: bool,
    categories: list[str] | None,
    inns: list[str] | None,
) -> list[tuple[str, str]]:
    _validate_account_number(account_number)
    if operation_status not in {"All", "Authorization", "Transaction"}:
        raise ValueError("operation_status: допустимы All, Authorization, Transaction.")
    if not (1 <= limit <= min(MAX_STATEMENT_LIMIT, 5000)):
        raise ValueError(f"limit должен быть от 1 до {min(MAX_STATEMENT_LIMIT, 5000)}.")
    _validate_iso_datetime(from_date, "from_date")
    if to_date:
        _validate_iso_datetime(to_date, "to_date")
    if cursor and len(cursor) > 100:
        raise ValueError("cursor не должен превышать 100 символов.")
    for inn in inns or []:
        if not re.fullmatch(r"\d{10}|\d{12}", inn):
            raise ValueError("Некорректный ИНН в фильтре.")

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
        params.append(("cursor", cursor))
    params.extend(("categories", category) for category in categories or [])
    params.extend(("inns", inn) for inn in inns or [])
    return params


async def _statement_page(
    account_number: str,
    from_date: str,
    to_date: str | None = None,
    operation_status: str = "All",
    limit: int = 1000,
    cursor: str | None = None,
    with_balances: bool = True,
    categories: list[str] | None = None,
    inns: list[str] | None = None,
) -> Any:
    params = _statement_params(
        account_number,
        from_date,
        to_date,
        operation_status,
        limit,
        cursor,
        with_balances,
        categories,
        inns,
    )
    return await _request("GET", "/v1/statement", params=params)


@mcp.tool()
async def list_accounts(with_invest: bool = False) -> str:
    """Показать расчетные счета компании, статусы и остатки. Только чтение."""
    return _json(
        await _request("GET", "/v4/bank-accounts", params={"withInvest": with_invest})
    )


@mcp.tool()
async def get_company_info() -> str:
    """Получить сведения и реквизиты компании из Т-Бизнеса. Только чтение."""
    return _json(await _request("GET", "/v1/company"))


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
    """Получить страницу операций по счету за период, с фильтрами и cursor."""
    return _json(
        await _statement_page(
            account_number,
            from_date,
            to_date,
            operation_status,
            limit,
            cursor,
            with_balances,
            categories,
            inns,
        )
    )


@mcp.tool()
async def summarize_statement(
    account_number: str,
    from_date: str,
    to_date: str,
    operation_status: str = "Transaction",
) -> str:
    """Суммировать за период число, зачисления, списания и категории операций.

    Загружает страницы выписки по cursor (до TBANK_SUMMARY_MAX_PAGES страниц),
    а затем возвращает агрегаты без реквизитов и назначений платежа.
    """
    _validate_account_number(account_number)
    _validate_iso_datetime(from_date, "from_date")
    _validate_iso_datetime(to_date, "to_date")
    if operation_status not in {"All", "Authorization", "Transaction"}:
        raise ValueError("operation_status: допустимы All, Authorization, Transaction.")

    operations: list[dict[str, Any]] = []
    cursor: str | None = None
    balances: Any = None
    pages = 0
    while pages < MAX_SUMMARY_PAGES:
        response = await _statement_page(
            account_number,
            from_date,
            to_date,
            operation_status,
            limit=5000,
            cursor=cursor,
            with_balances=pages == 0,
        )
        pages += 1
        if not isinstance(response, dict):
            raise TBankAPIError("T-API вернул неожиданный формат выписки.")
        page_operations = response.get("operations", [])
        if not isinstance(page_operations, list):
            raise TBankAPIError("T-API вернул неожиданный список операций.")
        operations.extend(op for op in page_operations if isinstance(op, dict))
        balances = response.get("balances", balances)
        next_cursor = response.get("nextCursor")
        if not next_cursor:
            cursor = None
            break
        cursor = str(next_cursor)

    credits = Decimal("0")
    debits = Decimal("0")
    categories: dict[str, dict[str, Any]] = {}
    for operation in operations:
        try:
            amount = Decimal(str(operation.get("rubleAmount", operation.get("operationAmount", 0))))
        except InvalidOperation:
            continue
        kind = operation.get("typeOfOperation")
        category = str(operation.get("category") or "unknown")
        group = categories.setdefault(category, {"count": 0, "credits": Decimal("0"), "debits": Decimal("0")})
        group["count"] += 1
        if kind == "Credit":
            credits += amount
            group["credits"] += amount
        elif kind == "Debit":
            debits += amount
            group["debits"] += amount

    def money(value: Decimal) -> str:
        return f"{value:.2f}"

    report = {
        "account_suffix": account_number[-4:],
        "from": from_date,
        "to_exclusive": to_date,
        "operation_status": operation_status,
        "operation_count": len(operations),
        "credits_rub": money(credits),
        "debits_rub": money(debits),
        "net_rub": money(credits - debits),
        "categories": {
            key: {
                "count": value["count"],
                "credits_rub": money(value["credits"]),
                "debits_rub": money(value["debits"]),
            }
            for key, value in categories.items()
        },
        "pages_fetched": pages,
        "truncated": bool(cursor),
        "balances": balances,
    }
    return _json(report)


@mcp.tool()
async def get_payment_document(
    account_number: str,
    operation_id: str,
    operation_date: str,
) -> str:
    """Получить подтверждающий документ PDF по исполненной операции.

    T-API возвращает содержимое PDF в Base64; нужны значения из выписки.
    """
    _validate_account_number(account_number)
    try:
        uuid.UUID(operation_id)
    except ValueError as exc:
        raise ValueError("operation_id должен быть UUID операции.") from exc
    try:
        date.fromisoformat(operation_date)
    except ValueError as exc:
        raise ValueError("operation_date должен быть в формате YYYY-MM-DD.") from exc
    return _json(
        await _request(
            "GET",
            "/v1/payment-document",
            params={
                "accountNumber": account_number,
                "operationId": operation_id,
                "operationDate": operation_date,
            },
        )
    )


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
    execution_order: int = 5,
    tax_payer_status: str = "0",
    kbk: str = "0",
    oktmo: str = "0",
    tax_evidence: str = "0",
    tax_period: str = "0",
    uin: str = "0",
    tax_doc_number: str = "0",
    tax_doc_date: str = "0",
) -> str:
    """Создать черновик в выбранном контуре T-API.

    В песочнице это тестовая запись без влияния на реальные данные. В production
    черновик появится в настоящем кабинете Т-Бизнеса, но не будет подписан или
    отправлен. Для production токену нужен отдельный scope создания черновиков.
    """
    _validate_account_number(account_number)
    if not re.fullmatch(r"\d{20}", recipient_account):
        raise ValueError("Счёт получателя должен содержать 20 цифр.")
    if not re.fullmatch(r"\d{9}", recipient_bik):
        raise ValueError("БИК получателя должен содержать 9 цифр.")
    if not re.fullmatch(r"\d{12}|\d{10}|0", recipient_inn):
        raise ValueError("ИНН получателя должен содержать 10 или 12 цифр; для физлица допускается 0.")
    if not re.fullmatch(r"\d{9}|0", recipient_kpp):
        raise ValueError("КПП получателя должен содержать 9 цифр или быть 0.")
    if not re.fullmatch(r"\d{1,6}", document_number):
        raise ValueError("Номер документа должен содержать от 1 до 6 цифр.")
    try:
        decimal_amount = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError("Сумма должна быть числом.") from exc
    if not decimal_amount.is_finite() or decimal_amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")
    if not recipient_name.strip():
        raise ValueError("Укажите наименование получателя.")
    if not payment_purpose.strip() or len(payment_purpose) > 210:
        raise ValueError("Назначение платежа обязательно и не должно превышать 210 символов.")
    if not 1 <= execution_order <= 5:
        raise ValueError("execution_order должен быть от 1 до 5.")
    if execution_date:
        _validate_iso_datetime(execution_date, "execution_date")

    body: dict[str, Any] = {
        "documentNumber": document_number,
        "amount": float(decimal_amount),
        "recipientName": recipient_name.strip(),
        "inn": recipient_inn,
        "kpp": recipient_kpp,
        "bankAcnt": recipient_account,
        "bankBik": recipient_bik,
        "accountNumber": account_number,
        "paymentPurpose": payment_purpose.strip(),
        "executionOrder": execution_order,
        "taxPayerStatus": tax_payer_status,
        "kbk": kbk,
        "oktmo": oktmo,
        "taxEvidence": tax_evidence,
        "taxPeriod": tax_period,
        "uin": uin,
        "taxDocNumber": tax_doc_number,
        "taxDocDate": tax_doc_date,
    }
    if execution_date:
        body["date"] = execution_date

    result = await _request("POST", "/v1/payment/create", body=body)
    notice = (
        "Тестовый черновик создан в T-API песочнице; реальные данные не затронуты."
        if SANDBOX_MODE
        else "Черновик создан в production-кабинете; он не подписан и не отправлен."
    )
    return _json({"notice": notice, "result": result})


@mcp.tool()
async def get_payment_draft_status(document_ids: list[str]) -> str:
    """Проверить статусы платежных документов по их documentId (1–100)."""
    if not 1 <= len(document_ids) <= 100:
        raise ValueError("Передайте от 1 до 100 documentId.")
    return _json(
        await _request("POST", "/v1/payment/status", body={"documentIds": document_ids})
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
