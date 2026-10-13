# WhatsApp Gateway: Operator Runbook

This runbook describes how the WhatsApp gateway behaves in the current code: how to set it up, how it routes messages, and how to troubleshoot it. It covers only what the code does.

Source of truth:

- `huf/ai/gateway_adapters/whatsapp.py`: adapter (ingress verification, parsing, outbound sends)
- `huf/ai/gateway_adapters/whatsapp_bridge.py`: the only module that touches `frappe_whatsapp` DocTypes
- `huf/ai/whatsapp_management.py`: whitelisted management API
- `huf/ai/gateway_webhook.py`: `handle_gateway_webhook` (the single Meta callback)
- `huf/ai/tools/whatsapp.py`: agent tool

---

## 1. Overview

Huf's WhatsApp gateway works without `frappe_whatsapp`. It has two modes.

**Direct mode** (Huf core only)

- Huf receives Meta webhooks, verifies them, and runs the agent.
- Replies are posted to the Meta Graph API (`v21.0`) using the credentials in the connected Integration Settings.
- No `frappe_whatsapp` DocTypes are required.

**Bridge mode** (`frappe_whatsapp` installed and the Gateway's `whatsapp_account` field set)

- Replies are created as `WhatsApp Message` (Outgoing) rows, which send the message through `frappe_whatsapp`.
- The inbound message is mirrored into `WhatsApp Message` for history.
- Template management and message listing from Huf use `frappe_whatsapp` DocTypes.

Huf never imports `frappe_whatsapp` at module load. The bridge checks for the DocTypes `WhatsApp Message`, `WhatsApp Templates` and `WhatsApp Account`. If any is missing, the bridge reports itself unavailable.

### What each mode supports

| Capability | Direct mode | Bridge mode |
|---|---|---|
| Receive signed text, interactive and button messages and run the agent | Yes | Yes |
| Duplicate Meta deliveries (same message id) produce one run | Yes | Yes |
| Free-form reply inside the 24h window | Yes, via Graph API | Yes, via `WhatsApp Message` Outgoing |
| Template fallback when the window is closed (Gateway `fallback_template` set) | Yes, via Graph API | Yes, via `WhatsApp Message` |
| Clear failure when the window is closed and no fallback is set | Yes | Yes |
| Inbound messages mirrored into `WhatsApp Message` | Only if `frappe_whatsapp` is installed and a WhatsApp Account resolves | Yes |
| Outbound messages recorded in `WhatsApp Message` | No | Yes |
| Delivery status callbacks forwarded to `frappe_whatsapp` | No | Yes (only for known message ids) |
| Template list, create, delete and status sync | No (`bridge_unavailable`) | Yes |
| Message and conversation listing in the management API | No (`bridge_unavailable`) | Yes |
| Outbound media | No | No |
| Inbound media download | Not wired (see section 7) | Not wired (see section 7) |

Note: the management API and the agent tool's `list_templates` and `create_template` actions need `frappe_whatsapp`. The agent tool's `send_message` and `send_template` do not. Without `frappe_whatsapp`, they call the Graph API directly.

---

## 2. Prerequisites

### Meta side

1. A Meta app with the WhatsApp product added.
2. A WhatsApp Business phone number, and its **Phone Number ID**.
3. A **permanent system-user access token** with permission to send messages and manage templates for the WhatsApp Business Account. Do not use the temporary 24-hour token from the API setup page.
4. The Meta app's **App Secret** (App settings > Basic). The gateway uses it to verify webhook signatures.
5. A **webhook verify token**: any string you choose. It must match the value you enter in Huf.
6. The **WhatsApp Business Account ID** if you plan to use template management. The sync reads templates by this ID.

### Optional: `frappe_whatsapp` (bridge mode)

Install the app on the bench and site:

```bash
bench get-app https://github.com/shridarpatil/frappe_whatsapp
bench --site <site> install-app frappe_whatsapp
```

Then create a **WhatsApp Account** in Frappe Desk. The code reads these fields:

| Field | Used for |
|---|---|
| `phone_id` | Phone Number ID. Checked against inbound `metadata.phone_number_id` |
| `token` | Access token (stored as a Password field) |
| `business_id` | Template list and sync |
| `url` | Graph base URL (defaults to `https://graph.facebook.com`) |
| `version` | Graph API version (defaults to `v21.0`) |
| `is_default_outgoing` | Selected when no account is named |
| `status` | Account must not be inactive when resolved as the first active account |

Mark one account as **default outgoing**. If no account is marked default, the first account with status `Active` is used. If neither exists, the bridge returns `no_account`.

---

## 3. Setup steps

### 3.1 Integration Settings (credentials)

Create an **Integration Settings** record for service `whatsapp` and add these credential rows (key, value):

| Key | Required | Notes |
|---|---|---|
| `phone_number_id` | Yes | Not secret. Meta Phone Number ID |
| `access_token` | Yes | Permanent system-user token. Stored as Password |
| `webhook_verify_token` | Yes | Must match the verify token entered in Meta |
| `app_secret` | Yes | Meta App Secret. Without it, every POST delivery is rejected |

The adapter refuses to start without `phone_number_id`, `access_token`, `webhook_verify_token` and `app_secret`.

Optional privilege flags (also credential rows, value `1` to enable). They are read from the active `whatsapp` Integration Settings (`is_active = 1`). See section 6.

| Key | Effect |
|---|---|
| `allow_proactive` | Allows free-form messages to numbers with no open 24h window |
| `allow_whatsapp_management` | Allows template creation, and also allows proactive sends |

### 3.2 Gateway

Create a **Gateway**:

| Field | Value |
|---|---|
| Channel (`provider`) | `WhatsApp` |
| Connected integration (`integration_settings`) | The Integration Settings record from 3.1 |
| WhatsApp Account (`whatsapp_account`) | Optional. Name of a frappe_whatsapp WhatsApp Account. Setting it enables bridge mode when the app is installed |
| Fallback template (`fallback_template`) | Optional. Name of an approved template with no variables. Used when a reply is due and the 24h window is closed |
| Run as user (`execution_user`) | Least-privileged Huf user. Never `Administrator` |
| Direct-message policy (`direct_policy`) | `Allow list` (default), `Pairing`, `Open` or `Disabled` |
| Enabled (`is_enabled`) | Checked |

Admission:

- With `Allow list`, a direct message is denied unless an approved **Gateway Access Entry** matches the sender. The sender's `external_id` is stored digits-only (see section 8).
- With `Pairing`, an unknown sender creates a pending pairing request. The triggering message is not executed.

### 3.3 Register the callback in Meta

In the Meta app, under WhatsApp > Configuration > Webhooks:

1. **Callback URL**: Huf's endpoint for this gateway:

   ```
   https://<site>/api/method/huf.ai.gateway_webhook.handle_gateway_webhook?gateway_name=<Gateway name>
   ```

   Replace `<Gateway name>` with the Gateway document name, URL-encoded if needed.

2. **Verify token**: the same value as `webhook_verify_token`.
3. Click **Verify and save**. Meta sends a GET request. Huf returns `hub.challenge` when the token matches.
4. Subscribe to these fields:
   - `messages`
   - `message_template_status_update`

**Security: the callback must be Huf's endpoint, never `frappe_whatsapp`'s webhook.** Meta allows one callback URL per app. `frappe_whatsapp` ships an unsigned webhook route. If that route is configured as the callback, Huf's signature verification is bypassed. Huf cannot detect this from its side. Check the callback URL in Meta. The management API's setup status returns the correct URL and a warning string to compare against.

### 3.4 Verify the setup

Call `huf.ai.whatsapp_management.get_setup_status` (System Manager or Huf Admin), or open the WhatsApp page in the Huf console (`/whatsapp`). It reports:

- `bridge_available`
- the WhatsApp Accounts found
- for each WhatsApp gateway: its webhook URL, whether `app_secret` and `webhook_verify_token` are present, and a `failing_checks` list

A healthy gateway has an empty `failing_checks` list. Send a test message from Meta or from a WhatsApp phone to the business number. Confirm a Gateway Event and an agent run appear.

---

## 4. The 24-hour window and templates

### 4.1 Window rule

The customer-service window is computed only from signed inbound Gateway Events for the same gateway and sender (`verified_sender = 1`). It is **open** for 24 hours after the sender's latest signed inbound message. `WhatsApp Message` rows are never used to decide the window.

Inside the window, replies are sent as free-form text.

### 4.2 Outside the window

When a reply is due and the window is closed:

1. If the Gateway has a `fallback_template`, the reply is sent as that template with no variables (language `en_US`).
2. If there is no fallback template, the send raises `WhatsAppWindowClosed`. The Gateway Event is set to status `Failed`, and its `error_message` (up to 500 characters) reads like `WhatsApp delivery failed: ...`. A log entry is also written. Nothing is retried in a loop.

Approval and pairing notifications follow the same rule. If the window is closed and no fallback is set, they are skipped and logged. The approval itself is not rolled back.

### 4.3 Template management

**UI**: the WhatsApp page in the Huf console (`/whatsapp`).

**Management API** (`huf.ai.whatsapp_management`, all whitelisted):

| Function | Method | What it does |
|---|---|---|
| `get_setup_status` | GET | Setup checks (requires System Manager or Huf Admin) |
| `list_templates` | GET | Lists WhatsApp Templates, optionally by account or status |
| `create_template` | POST | Creates a WhatsApp Templates row. Its hook pushes it to Meta |
| `delete_template` | POST | Deletes the row. `frappe_whatsapp` removes the template on Meta |
| `sync_templates` | POST | Pulls template status and id from Meta for one WhatsApp Account |
| `send_template_message` | POST | Sends an approved template. Allowed outside the 24h window |
| `send_text_message` | POST | Sends free-form text. Refused with `window_closed` outside the window |
| `list_messages` | GET | Recent WhatsApp Messages, newest first |
| `list_conversations` | GET | Recent messages grouped by counterparty number |

Template name rules: lowercase letters, digits and underscores only (`^[a-z0-9_]+$`). Category: `MARKETING`, `UTILITY` or `AUTHENTICATION`. `language_code` is required.

**Agent tool actions** (`huf/ai/tools/whatsapp.py`): `list_templates` (read-only) and `create_template` (gated, see section 6).

**Approval lifecycle**: the code does not enforce a state machine. A template is created in Meta by `frappe_whatsapp`. Its `status` field holds the value Meta reports, which Huf copies on `sync_templates`. The status values Huf expects are `PENDING`, `APPROVED` and `REJECTED`, and the agent tool filters on them. Only `APPROVED` templates can be sent successfully. Run a sync after Meta reviews a template, then check its status before using it as a fallback.

---

## 5. Agent tool

Actions: `send_message`, `send_template`, `list_messages`, `get_account_info`, `list_templates`, `create_template`.

- `send_message` is a free-form text send. It is **proactive** unless `gateway_name` is passed and that gateway's window is open. Without `gateway_name`, every free-form send is treated as proactive. A proactive send needs `allow_proactive` or `allow_whatsapp_management`.
- `send_template` is always allowed.
- `create_template` needs `allow_whatsapp_management` or `allow_proactive`.
- The tool never uses `ignore_permissions` and never switches the session user.
- `send_template`, `get_account_info` and the fallback path of `send_message` call the Graph API directly, using `phone_number_id` and `access_token` from the arguments or the Integration Settings. `send_message` logs to `WhatsApp Message` only when the bridge is not used and the app is installed.

---

## 6. Agent tool gating

Two server-side flags control privileged behaviour. They are read from the active `whatsapp` Integration Settings credential rows. Both are off by default.

| Credential row | Enables |
|---|---|
| `allow_proactive` | Free-form `send_message` outside an open window |
| `allow_whatsapp_management` | `create_template`, and proactive sends |

Rules:

- A flag counts only when its value is `1`, `true` or `yes` (case-insensitive). `0`, `false`, empty or a missing row do not count.
- Any error while reading the settings fails closed.
- The LLM cannot enable a flag. Any `allow_proactive` or `allow_whatsapp_management` argument in a tool call is discarded.

---

## 7. Media

**Inbound.** The adapter records each image, document, audio, video or sticker message as an attachment entry (media id, MIME type, filename, kind) in the event's raw payload. The text of the message is used as the agent input. The caption is used when present.

The bridge provides `download_media`, which enforces:

- size limit: 16 MB (declared size and actual size)
- MIME allowlist: `image/`, `audio/`, `video/`, `application/pdf`, `text/plain`
- filename sanitised to `A-Z a-z 0-9 . _ -`, at most 100 characters

**However, nothing in the inbound path calls `download_media` today.** Inbound media is not downloaded and no File is created. Operators should not expect attachments to reach the agent.

**Outbound.** Media replies are not supported. The adapter declares `supports_media_reply = False` and sends text only.

---

## 8. Phone identity

`normalize_phone` keeps digits only. It strips `+`, spaces, dashes and parentheses, and removes a leading `00`.

It is applied to:

- the inbound sender, which is also the conversation id (direct messages only)
- Gateway Access Entry `external_id` for WhatsApp, on save
- recipients in the management API and the agent tool
- the sender lookup in the 24h window check

Management recipients must have 7 to 15 digits. Meta's `wa_id` value is treated as canonical for inbound senders.

Quoted replies do not create a new conversation. The adapter sets `thread_id` to none and keeps the quoted message id in the raw payload. The reply to a message quotes its inbound message id, and the conversation key stays the sender's number.

---

## 9. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Meta shows "verification failed" when saving the callback | The GET token does not match `webhook_verify_token`, or the gateway name in the URL is wrong or the gateway is disabled | Make the token identical in both places. Check `gateway_name` and `is_enabled`. A mismatch returns HTTP 403 `Webhook verification failed` |
| Every POST delivery returns 401 `Provider verification failed` | `app_secret` is missing, the `X-Hub-Signature-256` header is absent, or the signature does not match | Run `get_setup_status`. If `app_secret_missing` is listed, add the App Secret. Confirm the callback is Huf's URL, not `frappe_whatsapp`'s |
| Reply shows `WhatsApp 24-hour customer-service window is closed` (Gateway Event `Failed`) | The sender has not messaged in the last 24 hours and there is no `fallback_template` | Set a `fallback_template` on the Gateway, or ask the customer to message again. The management `send_text_message` also returns `window_closed` |
| `bridge_unavailable` from management or tool calls | `frappe_whatsapp` is not installed, or one of its three DocTypes is missing | Install the app (section 2). Direct replies still work without it |
| `no_account` | No WhatsApp Account is marked default, and none is `Active` | Create an account, mark one as default outgoing, or pass an account name |
| `app_secret_missing` in `failing_checks` | The `app_secret` credential row is empty | Add the Meta App Secret to the Integration Settings |
| Duplicate events | Meta redelivers a message. Each delivery has the same message id | Expected. The second delivery is recorded as a duplicate and does not start another run. Ingest errors are re-raised so Meta retries; redelivery is then deduplicated |
| Messages for another phone number are ignored | The payload's `metadata.phone_number_id` does not match the gateway's `phone_number_id` | Check that the Integration Settings and the webhook subscription belong to the same WhatsApp number |
| Status updates do not appear in `frappe_whatsapp` | Bridge mode is off, or the message id is not in `WhatsApp Message` | Status is forwarded only for known messages. Status-only deliveries are acknowledged with 200 and do not start runs |

---

## 10. Testing

Test files for this integration:

- `huf/ai/tests/test_whatsapp_bridge.py`
- `huf/ai/tests/test_whatsapp_gateway_adapter.py`
- `huf/ai/tests/test_whatsapp_contract.py`
- `huf/ai/tests/test_whatsapp_ingress.py`
- `huf/ai/tests/test_whatsapp_management.py`
- `huf/ai/tests/test_whatsapp_tool_actions.py`
- `huf/ai/tests/test_whatsapp_tool_send_template.py`

Run them on a bench site:

```bash
bench --site <site> run-tests --module huf.ai.tests.test_whatsapp_bridge
```

Repeat for each module above, or run `bench --site <site> run-tests --app huf` for the full suite.
