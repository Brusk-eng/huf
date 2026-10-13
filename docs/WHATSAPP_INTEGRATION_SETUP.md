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
- Replies are posted to the Meta Graph API (`v25.0`) using the credentials in the connected Integration Settings.
- No `frappe_whatsapp` DocTypes are required.

**Bridge mode** (`frappe_whatsapp` installed and the Gateway's `whatsapp_account` field set)

- Replies are created as `WhatsApp Message` (Outgoing) rows, which send the message through `frappe_whatsapp`.
- The inbound message is mirrored into `WhatsApp Message` for history.
- Template management and message listing from Huf use `frappe_whatsapp` DocTypes.

Huf never imports `frappe_whatsapp` at module load. The bridge checks for the DocTypes `WhatsApp Message`, `WhatsApp Templates` and `WhatsApp Account`. If any is missing, the bridge reports itself unavailable.

> **Template management and message history require the optional connector app (`frappe_whatsapp`, "bridge mode").** Without it, Huf still receives messages, runs the agent and replies (including the template fallback), but the template list/create/delete/sync, message and conversation listing, and the WhatsApp Message history are unavailable (`bridge_unavailable`).

### Known limits

- **One Gateway per Meta app and phone number.** A signed payload for a `phone_number_id` that does not match the gateway is ignored (HTTP 200) and the gateway's `last_error` says so.
- **Non-text inbound** (location, contacts, reactions) is acknowledged but does not start a run and does not open the 24h window in Huf. Known limitation.
- **72-hour click-to-WhatsApp windows are not modelled.** Huf always applies the conservative 24h rule.
- The window is anchored on the customer's own message timestamp, so Meta redelivering an old webhook (up to about 7 days) does not reopen it.

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
| Outbound media (image, document, audio, video) inside the 24h window | Yes, by link or Meta media id | Yes, by link only |
| Rich templates: header, footer, buttons | No (`bridge_unavailable`) | Yes (see section 4.4) |
| Template send with header and button parameters | Yes, via Graph API | Yes (see section 4.4) |
| Inbound media download | Yes, best-effort in the background job (see section 7) | Yes, best-effort in the background job (see section 7) |

Note: the management API and the agent tool's `list_templates` and `create_template` actions need `frappe_whatsapp`. The agent tool's `send_message`, `send_media` and `send_template` do not. Without `frappe_whatsapp`, they call the Graph API directly.

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
| `version` | Graph API version (defaults to `v25.0`) |
| `is_default_outgoing` | Selected when no account is named |
| `status` | Used only by the fallback that picks the first account with status `Active` |

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
| Fallback template language (`fallback_template_language`) | Language code of that template. Defaults to `en_US` |
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
   - `message_template_quality_update` (optional)
   - `template_category_update` (optional)

   Huf forwards these best-effort. A rejected/paused/disabled template, a quality drop to LOW or RED, or a category change is written to the `last_error` of every gateway whose fallback template matches. `played` delivery statuses are accepted and ignored. The verification GET requires `hub.mode=subscribe` when `hub.mode` is sent.

**Security: the callback must be Huf's endpoint, never `frappe_whatsapp`'s webhook.** Meta allows one callback URL per app. `frappe_whatsapp` ships an unsigned webhook route. If that route is configured as the callback, Huf's signature verification is bypassed. Huf cannot detect this from its side. Check the callback URL in Meta. The management API's setup status returns the correct URL and a warning string to compare against.

**The connector's own webhook stays live.** Installing `frappe_whatsapp` leaves `/api/method/frappe_whatsapp.utils.webhook.webhook` reachable by anyone, with no signature check. Anyone who can reach it can write fake rows into `WhatsApp Message` and pollute the message history. Huf does not read those rows to decide the 24h window, but the history views would show them. Disable it by blocking the route at the reverse proxy, for example in nginx:

```nginx
location = /api/method/frappe_whatsapp.utils.webhook.webhook {
    return 403;
}
```

Place it in the site's `server` block before the generic Frappe proxy location. Meta then only reaches Huf's signed endpoint.

### 3.4 Verify the setup

Call `huf.ai.whatsapp_management.get_setup_status` (System Manager or Huf Admin), or open the WhatsApp page in the Huf console (`/whatsapp`). It reports:

- `bridge_available`
- the WhatsApp Accounts found
- for each WhatsApp gateway: its webhook URL, whether `app_secret` and `webhook_verify_token` are present, and a `failing_checks` list. `account_phone_mismatch` is listed when the linked WhatsApp Account's `phone_id` differs from the credential `phone_number_id`

A healthy gateway has an empty `failing_checks` list. Send a test message from Meta or from a WhatsApp phone to the business number. Confirm a Gateway Event and an agent run appear.

---

## 4. The 24-hour window and templates

### 4.1 Window rule

The customer-service window is computed only from signed inbound Gateway Events for the same gateway and sender (`verified_sender = 1`). It is **open** for 24 hours after the sender's latest signed inbound message. `WhatsApp Message` rows are never used to decide the window.

Inside the window, replies are sent as free-form text.

### 4.2 Outside the window

When a reply is due and the window is closed:

1. If the Gateway has a `fallback_template`, the reply is sent as that template with no variables (language from the Gateway's `fallback_template_language`, default `en_US`; in bridge mode the language of the matching WhatsApp Templates row is used when found). The Gateway Event stays `Succeeded` and its `error_message` reads "Fallback template sent; agent reply suppressed (24h window closed)".
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
| `send_media_message` | POST | Sends an image, document, audio or video by public link or public uploaded file. Refused with `window_closed` outside the window |
| `list_messages` | GET | Recent WhatsApp Messages, newest first |
| `list_conversations` | GET | Recent messages grouped by counterparty number |

Template name rules: lowercase letters, digits and underscores only (`^[a-z0-9_]+$`). Category: `MARKETING` or `UTILITY` (`AUTHENTICATION` is not supported because Meta requires OTP button components). `language_code` is required.

**Agent tool actions** (`huf/ai/tools/whatsapp.py`): `list_templates` (read-only) and `create_template` (gated, see section 6). `delete_template` returns `template_in_use` for a template that has been sent, without touching Meta.

**Approval lifecycle**: the code does not enforce a state machine. A template is created in Meta by `frappe_whatsapp`. Its `status` field holds the value Meta reports, which Huf copies on `sync_templates`. The agent tool's `status` filter uses the values `PENDING`, `APPROVED` and `REJECTED`. Huf does not block sends of a template based on its status. Run a sync after Meta reviews a template, and check its status before relying on it as a fallback.


### 4.4 Rich templates (header, footer, buttons)

`create_template` (management API, console Create dialog, agent tool) accepts these optional parts besides the body. They map onto the `WhatsApp Templates` fields and `WhatsApp Button` child rows exactly as `frappe_whatsapp` defines them.

| Payload key | Doctype field | Notes |
|---|---|---|
| `template` (or `body`) | `template` | Required, up to 1024 characters. `{{1}}`, `{{2}}`, ... in order, no gaps |
| `sample_values` | `sample_values` | One example per body variable, comma separated (so examples cannot contain commas). Required when the body has variables |
| `header_type` | `header_type` | `TEXT`, `IMAGE` or `DOCUMENT`. `VIDEO` headers are not available in `frappe_whatsapp` and are rejected |
| `header` | `header` | TEXT header, up to 60 characters, at most one variable `{{1}}` |
| `sample` | `sample` | TEXT header with a variable: one example value. IMAGE/DOCUMENT: an `http(s)` URL (public, https outside tests) or the path of an uploaded File (`/files/...`). `frappe_whatsapp` uploads it to Meta for review |
| `footer` | `footer` | Up to 60 characters, no variables |
| `buttons` | `buttons` rows | List of `{type, text, ...}`: `QUICK_REPLY`; `URL` with `url` (a trailing `{{1}}` makes it dynamic and needs an `example` full URL); `PHONE_NUMBER` with `phone_number`. Mapped to `Quick Reply`, `Visit Website` (`url_type` Static or Dynamic, `example_url`) and `Call Phone` |

Limits enforced before anything is sent to Meta: button text 25 characters, at most 3 quick replies or 2 call-to-action buttons (URL plus phone) with at most one phone button, and quick replies and call-to-action buttons each grouped together. A bad request returns one structured error listing every problem:

```json
{"success": false, "error": "validation_failed", "message": "...",
 "errors": [{"field": "footer", "code": "too_long", "message": "..."}]}
```

**Sending with parameters.** `send_template_message` (and the tool's `send_template`, and the adapter in direct mode) take:

- `body_params`: strings for `{{1}}`... The count must match the template. Parameters cannot be empty or contain newlines, tabs or four or more spaces (Meta rejects them).
- `header_params`: one text string for a TEXT header variable, or one `{"type": "image|document", "link": "https://..."}` for a media header (`filename` is optional for documents). An IMAGE header with no link falls back to the template's own sample link.
- `button_params`: `[{"index": 0, "sub_type": "url", "text": "<suffix>"}]` for each dynamic URL button (`index` is the button's position, starting at 0). A bare string is the suffix of the button at that list position.

Bridge mode: plain body-only sends go through an Outgoing `WhatsApp Message`, which `frappe_whatsapp` sends. `frappe_whatsapp` cannot build header variables or dynamic URL parameters without a reference document, so sends that use `header_params` or `button_params` (or whose template has a dynamic URL button) are posted to Graph with the account credentials and mirrored into `WhatsApp Message` with the returned message id. There is still exactly one send.

Not available: `VIDEO` headers, `AUTHENTICATION` templates, carousel, flow, catalog and multi-product buttons, copy-code and other button types, and editing a template after it is created.

---

## 5. Agent tool

Actions: `send_message`, `send_media`, `send_template`, `list_messages`, `get_account_info`, `list_templates`, `create_template`.

- `send_message` is a free-form text send. It is **proactive** unless `gateway_name` is passed and that gateway's window is open. Without `gateway_name`, every free-form send is treated as proactive. A proactive send needs `allow_proactive` or `allow_whatsapp_management`.
- `send_media` (`to`, `kind` image/document/audio/video, `link`, `caption`, `filename`) is a free-form send. It follows exactly the same proactive logic and flags as `send_message`.
- `send_template` is always allowed. It accepts `parameters`, `header_params` and `button_params` (see section 4.4).
- `create_template` needs `allow_whatsapp_management` or `allow_proactive`, and accepts the header, footer and button fields of section 4.4.
- The tool never uses `ignore_permissions` and never switches the session user.
- `send_template`, `get_account_info` and the fallback path of `send_message` call the Graph API directly, using `phone_number_id` and `access_token` from the arguments or the Integration Settings. `send_message` logs to `WhatsApp Message` only when the bridge is not used and the app is installed.

---

## 6. Agent tool gating

Two server-side flags control privileged behaviour. They are read from the active `whatsapp` Integration Settings credential rows. Both are off by default.

| Credential row | Enables |
|---|---|
| `allow_proactive` | Free-form `send_message` and `send_media` outside an open window |
| `allow_whatsapp_management` | `create_template`, and proactive sends |

Rules:

- A flag counts only when its value is `1`, `true` or `yes` (case-insensitive). `0`, `false`, empty or a missing row do not count.
- Any error while reading the settings fails closed.
- The LLM cannot enable a flag. Any `allow_proactive` or `allow_whatsapp_management` argument in a tool call is discarded.

---

## 7. Media

**Inbound.** The adapter records each image, document, audio, video or sticker message as an attachment entry (media id, MIME type, filename, kind) in the event's raw payload. The text of the message is used as the agent input. The caption is used when present.

The bridge provides `download_media`, which enforces:

- per-kind size limits matching Meta's inbound limits, checked against both the declared and the streamed size: document 100 MB, image 5 MB, audio 16 MB, video 16 MB, sticker 500 KB
- MIME allowlist: `image/`, `audio/`, `video/`, `application/pdf`, `text/plain`, plus Office documents (`application/msword`, `application/vnd.openxmlformats-officedocument.*`, `application/vnd.ms-excel`, `application/vnd.ms-powerpoint`). SVG, HTML and executables are always rejected
- filename sanitised to `A-Z a-z 0-9 . _ -`, at most 100 characters

When the background job processes an event with an image, document, audio, video or sticker, it calls `download_media` (best-effort), saves a private File attached to the Gateway Event, and adds an `[Attachment saved: <url>]` note to the agent prompt. A failed or rejected download is logged and the text flow continues.

**Outbound.** The adapter declares `supports_media_reply = True`.

- `adapter.send_media(to, kind, link=..., media_id=..., caption=..., filename=...)`: `kind` is `image`, `document`, `audio` or `video`. Give exactly one of a public `link` or a Meta `media_id`. Captions are up to 1024 characters and are not allowed on audio. `filename` applies to documents. Direct mode posts the Graph `/messages` payload. Bridge mode inserts an Outgoing `WhatsApp Message` with `content_type` and `attach` (so only `link` is supported; `media_id` and `filename` are not).
- Free-form media is subject to the same 24h window as text. A closed window raises `WhatsAppWindowClosed` (management API: `window_closed`). There is no template fallback for media.
- `GatewayReply.attachments` is honoured by the WhatsApp adapter: the first attachment (URL or media id) is sent with the reply text as its caption; for audio the text follows as a separate message. Other adapters ignore the field, and the agent reply pipeline does not produce attachments yet, so today media is sent through the explicit paths: the adapter, the management endpoint `send_media_message` (console Messages tab: paperclip button, public link or file upload), and the agent tool action `send_media`.
- Uploaded files must be public (`/files/...`) for Meta to fetch them. Private files are rejected with `invalid_file`.

### Fake Graph API

`huf/ai/tests/fake_graph_server.py` is a real local HTTP server that implements the Graph endpoints Huf and `frappe_whatsapp` call (messages, message_templates, resumable sample upload, media lookup and download) and strictly validates request bodies against Meta's documented schema, including template review state and parameter counts. `test_whatsapp_fake_graph.py` points the WhatsApp Account `url` and an Integration Settings `graph_api_url` credential at it, so real `requests` calls are made. The `graph_api_url` credential and the `frappe.flags.whatsapp_test_media_hosts` media-host allowance are only honoured while `frappe.flags.in_test` is set; production always uses `graph.facebook.com` and the fixed media host allowlist. A schema violation recorded by the fake fails the test.

Remaining limits of the fake and of the feature: live Meta has not been exercised; the fake does not model Meta's own review decisions, rate limits or the 24h window; template edits after creation, `VIDEO` headers and non-text inbound opening the window are not supported.
