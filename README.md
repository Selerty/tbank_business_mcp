# T-Bank Business MCP

Локальный MCP-сервер для своего расчётного счёта ИП в Т-Бизнесе. Сервер работает по stdio и обращается к официальному T-API напрямую. Токен не передаётся модели как параметр инструмента и не записывается в файлы проекта. HTTPS-сертификаты проверяются через системное хранилище сертификатов.

## Инструменты

- `list_accounts` — расчётные и инвестиционные счета, их статусы и остатки.
- `get_company_info` — сведения и реквизиты компании.
- `get_statement` — операции за период, фильтры по статусу/категории/ИНН и постраничная выдача через `nextCursor`.
- `summarize_statement` — сводка за период: зачисления, списания, чистое изменение и суммы по категориям.
- `get_payment_document` — PDF-подтверждение исполненной операции из выписки.
- `get_payment_draft_status` — статус документа по `documentId`.
- `create_payment_draft` — создать черновик в выбранном контуре. В sandbox он тестовый; в production появляется в банковском кабинете. Инструмент не подписывает и не отправляет платёж.

MCP не подписывает и не отправляет платежи и не переводит деньги. В production создание черновика всё же добавляет документ в банковский кабинет и требует отдельного scope токена.

## Параметры методов

### `list_accounts`

- `with_invest: bool = false` — включить инвестиционные счета в ответ.
- Возвращает счета, типы, статусы и доступные API остатки.

### `get_company_info`

Аргументов нет. Возвращает сведения о компании и её реквизитах в рамках прав токена.

### `get_statement`

- `account_number` — 20-значный номер счёта.
- `from_date` — начало периода ISO 8601, включительно.
- `to_date: str | null = null` — конец периода, не включается.
- `operation_status: str = "All"` — `All`, `Authorization` или `Transaction`.
- `limit: int = 1000` — 1–5000 операций на страницу.
- `cursor: str | null = null` — `nextCursor` из предыдущего ответа.
- `with_balances: bool = true` — вернуть балансы на первой странице.
- `categories: list[str] | null = null` — фильтр по категориям.
- `inns: list[str] | null = null` — фильтр по ИНН контрагентов.

### `summarize_statement`

- `account_number`, `from_date`, `to_date` — счёт и интервал ISO 8601; конечная дата не включается.
- `operation_status: str = "Transaction"` — `All`, `Authorization` или `Transaction`.
- Автоматически проходит страницы до 20 × 5000 операций и возвращает количество, суммы зачислений/списаний, чистое изменение, разрез по категориям и балансы. Ответ не содержит назначений платежей и реквизитов контрагентов. Поле `truncated` показывает, достигнут ли лимит страниц.

### `get_payment_document`

- `account_number` — номер счёта.
- `operation_id` — UUID операции из выписки.
- `operation_date` — дата операции `YYYY-MM-DD`.
- Возвращает подтверждающий документ исполненной операции в Base64 (PDF).

### `get_payment_draft_status`

- `document_ids: list[str]` — от 1 до 100 идентификаторов документов.
- Запрашивает статусы документов; метод не меняет их состояние.

### `create_payment_draft`

- Обязательные поля: `document_number`, `amount`, `recipient_name`, `recipient_inn`, `recipient_kpp`, `recipient_account`, `recipient_bik`, `account_number`, `payment_purpose`.
- Дополнительные поля: `execution_date: str | null = null`, `execution_order: int = 5` (1–5), налоговые поля по умолчанию равны строке `"0"` для обычного неналогового перевода.
- В sandbox создаётся только тестовый черновик. В production документ появится в банковском кабинете, но не будет подписан или отправлен; токену нужен scope создания черновиков.

## Требования

- Python 3.11+
- `uv`
- Т-Бизнес аккаунт ИП и API-токен с минимальными нужными правами

## Настройка на macOS

Для просмотра выпустите T-API токен с правами на чтение счетов и операций. Для `create_payment_draft` в production дополнительно нужен scope создания черновиков; не добавляйте его, если функция не нужна.

Сохраните токен в macOS Keychain. Команда запросит значение интерактивно и не поместит его в историю оболочки:

```bash
security add-generic-password -a "$USER" -s tbank-business-api-token -w
```

Установите зависимости:

```bash
cd /path/to/tbank_business_mcp
uv sync
chmod +x scripts/run-with-keychain.sh
```

Для безопасной разработки подключите тестовую песочницу. Ей не нужен личный токен: скрипт использует публичный `TBankSandboxToken` и адрес `https://business.tbank.ru/openapi/sandbox/api`.

```bash
codex mcp add tbank-business-sandbox -- /bin/sh /path/to/tbank_business_mcp/scripts/run-sandbox.sh
```

В песочнице можно проверить контракты и поведение методов; её ответы тестовые и могут меняться. Запросы к ней не влияют на реальные данные. Не используйте значения из песочницы как реальные банковские данные.

Для Codex добавьте в `~/.codex/config.toml`:

```toml
[mcp_servers.tbank-business]
command = "/bin/sh"
args = ["/path/to/tbank_business_mcp/scripts/run-with-keychain.sh"]
```

Замените `/path/to/tbank_business_mcp` в примерах на фактический путь, куда вы клонировали репозиторий.

Либо выполните команду Codex CLI:

```bash
codex mcp add tbank-business -- /bin/sh /path/to/tbank_business_mcp/scripts/run-with-keychain.sh
```

Для Claude Desktop соответствующая запись выглядит так:

```json
{
  "mcpServers": {
    "tbank-business": {
      "command": "/bin/sh",
      "args": ["/path/to/tbank_business_mcp/scripts/run-with-keychain.sh"]
    }
  }
}
```

Перезапустите Codex/клиент и проверьте доступность инструментов MCP. Для ротации токена используйте Keychain с `-U`:

```bash
security add-generic-password -U -a "$USER" -s tbank-business-api-token -w
```

Для запуска вручную можно задать `TBANK_API_TOKEN` в окружении и выполнить `uv run tbank-business-mcp`. Не помещайте настоящий токен в `.env`, MCP-конфиг, shell history или репозиторий.

## Даты выписки

Передаются в ISO 8601, например `2026-11-01T00:00:00Z`. `to_date` не включается в интервал. Если ответ содержит `nextCursor`, передайте его в следующий вызов `get_statement` как `cursor`. Максимум T-API — 5000 операций на страницу.

## Контуры и ограничения

- Production base URL: `https://business.tbank.ru/openapi/api`; Bearer-токен из Keychain.
- Sandbox base URL: `https://business.tbank.ru/openapi/sandbox/api`; публичный тестовый Bearer-токен.
- Все методы доступны в обоих профилях. В production `create_payment_draft` требует дополнительный scope и создаёт документ в кабинете, но не подписывает и не отправляет его.

Документация: [T-API](https://developer.tbank.ru/docs/api/t-api), [песочница](https://developer.tbank.ru/docs/intro/other/sandbox), [счета и выписки](https://developer.tbank.ru/docs/api/scheta-i-vipiski), [подтверждающий документ](https://developer.tbank.ru/docs/api/get-api-v-1-payment-document), [информация о компании](https://developer.tbank.ru/docs/api/get-api-v-1-company), [черновик платежа](https://developer.tbank.ru/docs/api/post-api-v-1-payment-create), [статусы платежных документов](https://developer.tbank.ru/docs/api/payment-status).

## Проверки

Локальные проверки без банковских запросов:

```bash
python -m unittest discover -s tests -v
```

Проверка API-контрактов выполняется через профиль `tbank-business-sandbox`; песочница возвращает тестовые данные и не затрагивает production.
