# T-Bank Business MCP

Локальный MCP-сервер для своего расчётного счёта ИП в Т-Бизнесе. Сервер работает по stdio и обращается к официальному T-API напрямую. Токен не передаётся модели как параметр инструмента и не записывается в файлы проекта. HTTPS-сертификаты проверяются через системное хранилище сертификатов.

## Возможности первой версии

- `list_accounts` — сведения о расчётных и инвестиционных счетах компании.
- `get_statement` — операции за период, фильтры по статусу/категории/ИНН и постраничная выдача через `nextCursor`.
- `create_payment_draft` — создать черновик обычного платежного поручения. Черновик появится в банковском кабинете, но инструмент не подписывает и не отправляет платеж.
- `get_payment_draft_status` — получить статусы черновиков по `documentId`.

MCP не содержит инструмента отправки денег, оплаты реестров, операций с картами, налоговых платежей или безакцептного списания.

## Требования

- Python 3.11+
- `uv`
- Т-Бизнес аккаунт ИП и API-токен с минимальными нужными правами

## Настройка на macOS

Выпустите отдельный T-API токен в личном кабинете Т-Бизнеса: доступ к информации о счетах компании, операциям по счетам и (если нужны черновики) созданию черновиков платежей. Не добавляйте права на совершение платежей.

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

Для Codex добавьте в `~/.codex/config.toml`:

```toml
[mcp_servers.tbank-business]
command = "/path/to/tbank_business_mcp/scripts/run-with-keychain.sh"
args = []
```

Замените `/path/to/tbank_business_mcp` в примерах на фактический путь, куда вы клонировали репозиторий.

Либо выполните команду Codex CLI:

```bash
codex mcp add tbank-business -- /path/to/tbank_business_mcp/scripts/run-with-keychain.sh
```

Для Claude Desktop соответствующая запись выглядит так:

```json
{
  "mcpServers": {
    "tbank-business": {
      "command": "/path/to/tbank_business_mcp/scripts/run-with-keychain.sh"
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

## Т-Банк API

- API base URL по умолчанию: `https://business.tbank.ru/openapi/api`
- Токен: Bearer API Token
- Для платёжного черновика требуется отдельный scope создания черновиков. Т-Банк ограничивает этот метод 10 запросами в секунду.
- Создание черновика — действие в банковском кабинете, несмотря на то что оно не переводит деньги. Проверяйте данные перед вызовом.

Документация: [T-API](https://developer.tbank.ru/docs/api/t-api), [счета и выписки](https://developer.tbank.ru/docs/api/scheta-i-vipiski), [черновик платежа](https://developer.tbank.ru/docs/api/post-api-v-1-payment-create), [статусы платежных документов](https://developer.tbank.ru/docs/api/payment-status).
