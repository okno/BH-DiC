# DiC privacy classification

Classification applies before Discord rendering, files, audit and model
boundaries. Membership in the private HR channel alone does not override it.

| Class | Examples | Default delivery |
| --- | --- | --- |
| Public aggregate | Total active/inactive employees; aggregate availability count with no small-group inference | Configured protected channel when policy permits |
| Internal HR | Job title, workplace label, team/group, contract type/status, schedule model | Protected HR channel only after role and field projection |
| Personal | Name, employee ID, email, phone, address, birth date, payroll number, contract dates, document title | Authorized ephemeral or verified private delivery |
| Highly confidential | Tax code, IBAN, payroll net, payroll/document attachment URL, notes, precise location/coordinates | Authorized private delivery only; never generic channel output |
| Security/IAM | Permission matrix, admin/master flags, invitation/access state, device/mode settings | Dedicated entitlement and private delivery |
| Secret/session | Credentials, cookies, tokens, session storage, MFA/TOTP, request headers | Never returned, logged, committed or sent to the model |

## Resource projection rules

- Employee list channel views must expose only explicitly authorized columns.
- Entity disambiguation may show the minimum necessary name plus opaque ID only to
  an authorized requester and only in an authorized delivery context.
- Payroll net and PDF links are not channel-safe merely because the channel is
  private; the final design requires private/ephemeral delivery and attachment
  policy.
- Document URLs are temporary bearer-like capabilities and are highly
  confidential. Logs and audit contain only resource type, outcome and opaque
  correlation.
- Coordinates, address, notes, tax code and IBAN are never provider input.

## Model boundary

The planning model receives only minimized user text and a closed schema of
policy-visible operations. It never receives employee candidates, DiC response
values, document metadata, payroll values, routes containing live IDs, browser
state or authorization material.
