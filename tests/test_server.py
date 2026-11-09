from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from tbank_business_mcp import server


ACCOUNT = "40702810110011000000"


class ValidationTests(unittest.TestCase):
    def test_account_number_requires_twenty_digits(self) -> None:
        self.assertEqual(server._validate_account_number(ACCOUNT), ACCOUNT)
        with self.assertRaisesRegex(ValueError, "20 цифр"):
            server._validate_account_number("123")

    def test_statement_params_encode_filters_and_cursor(self) -> None:
        params = server._statement_params(
            ACCOUNT,
            "2026-11-01T00:00:00Z",
            "2026-11-02T00:00:00Z",
            "Transaction",
            25,
            "next-page",
            True,
            ["fee", "income"],
            ["1234567890"],
        )
        self.assertIn(("cursor", "next-page"), params)
        self.assertIn(("categories", "fee"), params)
        self.assertIn(("categories", "income"), params)
        self.assertIn(("inns", "1234567890"), params)
        self.assertIn(("withBalances", "true"), params)

    def test_statement_rejects_bad_status_and_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "operation_status"):
            server._statement_params(
                ACCOUNT, "2026-11-01T00:00:00Z", None, "Pending", 10, None, True, None, None
            )
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            server._statement_params(
                ACCOUNT, "yesterday", None, "All", 10, None, True, None, None
            )


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_accounts_calls_v4_endpoint(self) -> None:
        with patch.object(server, "_request", new=AsyncMock(return_value=[{"status": "NORM"}])) as request:
            result = json.loads(await server.list_accounts())
        self.assertEqual(result, [{"status": "NORM"}])
        request.assert_awaited_once_with("GET", "/v4/bank-accounts", params={"withInvest": False})

    async def test_company_info_uses_read_only_endpoint(self) -> None:
        with patch.object(server, "_request", new=AsyncMock(return_value={"name": "Sandbox"})) as request:
            result = json.loads(await server.get_company_info())
        self.assertEqual(result["name"], "Sandbox")
        request.assert_awaited_once_with("GET", "/v1/company")

    async def test_get_statement_forwards_cursor_and_filters(self) -> None:
        expected = {"operations": [], "nextCursor": "next"}
        with patch.object(server, "_statement_page", new=AsyncMock(return_value=expected)) as request:
            result = json.loads(
                await server.get_statement(
                    ACCOUNT,
                    "2026-11-01T00:00:00Z",
                    "2026-11-02T00:00:00Z",
                    "Transaction",
                    50,
                    "cursor-1",
                    False,
                    ["fee"],
                    ["1234567890"],
                )
            )
        self.assertEqual(result, expected)
        request.assert_awaited_once_with(
            ACCOUNT,
            "2026-11-01T00:00:00Z",
            "2026-11-02T00:00:00Z",
            "Transaction",
            50,
            "cursor-1",
            False,
            ["fee"],
            ["1234567890"],
        )

    async def test_summarize_statement_aggregates_all_pages(self) -> None:
        pages = [
            {
                "operations": [
                    {"typeOfOperation": "Credit", "category": "customer", "rubleAmount": 100.10},
                    {"typeOfOperation": "Debit", "category": "fee", "rubleAmount": 10},
                ],
                "nextCursor": "cursor-2",
                "balances": {"closing": 110.10},
            },
            {
                "operations": [
                    {"typeOfOperation": "Credit", "category": "customer", "rubleAmount": 20}
                ],
            },
        ]
        with patch.object(server, "_statement_page", new=AsyncMock(side_effect=pages)) as request:
            summary = json.loads(
                await server.summarize_statement(
                    ACCOUNT, "2026-11-01T00:00:00Z", "2026-12-01T00:00:00Z"
                )
            )
        self.assertEqual(summary["operation_count"], 3)
        self.assertEqual(summary["credits_rub"], "120.10")
        self.assertEqual(summary["debits_rub"], "10.00")
        self.assertEqual(summary["net_rub"], "110.10")
        self.assertEqual(summary["categories"]["customer"]["count"], 2)
        self.assertEqual(summary["balances"], {"closing": 110.10})
        self.assertFalse(summary["truncated"])
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.await_args_list[1].kwargs["cursor"], "cursor-2")
        self.assertFalse(request.await_args_list[1].kwargs["with_balances"])

    async def test_payment_document_validates_and_maps_parameters(self) -> None:
        operation_id = "00000000-0000-4000-8000-000000000001"
        with patch.object(server, "_request", new=AsyncMock(return_value={"pdf": "base64"})) as request:
            result = json.loads(await server.get_payment_document(ACCOUNT, operation_id, "2026-11-01"))
        self.assertEqual(result["pdf"], "base64")
        request.assert_awaited_once_with(
            "GET",
            "/v1/payment-document",
            params={"accountNumber": ACCOUNT, "operationId": operation_id, "operationDate": "2026-11-01"},
        )
        with self.assertRaisesRegex(ValueError, "UUID"):
            await server.get_payment_document(ACCOUNT, "bad-id", "2026-11-01")

    async def test_create_draft_contains_required_contract_fields(self) -> None:
        result = {"documentId": "sandbox-doc-id"}
        with (
            patch.object(server, "SANDBOX_MODE", True),
            patch.object(server, "_request", new=AsyncMock(return_value=result)) as request,
        ):
            response = json.loads(
                await server.create_payment_draft(
                    "12345",
                    1.0,
                    "Sandbox Recipient",
                    "0",
                    "0",
                    ACCOUNT,
                    "044525974",
                    ACCOUNT,
                    "Sandbox contract check",
                )
            )
        self.assertIn("песочнице", response["notice"])
        request.assert_awaited_once()
        method, path = request.await_args.args[:2]
        body = request.await_args.kwargs["body"]
        self.assertEqual((method, path), ("POST", "/v1/payment/create"))
        for field in (
            "documentNumber", "amount", "recipientName", "inn", "kpp", "bankAcnt",
            "bankBik", "accountNumber", "paymentPurpose", "executionOrder",
            "taxPayerStatus", "kbk", "oktmo", "taxEvidence", "taxPeriod", "uin",
            "taxDocNumber", "taxDocDate",
        ):
            self.assertIn(field, body)
        self.assertEqual(body["executionOrder"], 5)

    async def test_payment_status_requires_document_ids_and_posts_read_only_query(self) -> None:
        with self.assertRaisesRegex(ValueError, "1 до 100"):
            await server.get_payment_draft_status([])
        with patch.object(server, "_request", new=AsyncMock(return_value={"result": []})) as request:
            response = json.loads(await server.get_payment_draft_status(["sandbox-doc-id"]))
        self.assertEqual(response, {"result": []})
        request.assert_awaited_once_with(
            "POST", "/v1/payment/status", body={"documentIds": ["sandbox-doc-id"]}
        )

    async def test_all_seven_mcp_tools_are_registered(self) -> None:
        names = {tool.name for tool in await server.mcp.list_tools()}
        self.assertEqual(
            names,
            {
                "list_accounts",
                "get_company_info",
                "get_statement",
                "summarize_statement",
                "get_payment_document",
                "create_payment_draft",
                "get_payment_draft_status",
            },
        )


if __name__ == "__main__":
    unittest.main()
