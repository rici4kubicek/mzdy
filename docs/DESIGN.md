# Design: Payroll Processing Web Application

## 1. Purpose and Constraints

| | |
|---|---|
| Accounting / payroll system | **Pohoda** |
| Payment input | **a single ABO file** (payment order exported from Pohoda: salaries, insurance, taxes, …) |
| Bank | **ČSOB**, files are uploaded to internet banking manually |
| Number of employees | up to 20 |
| Hosting | **AWS, serverless** |
| Security | simple username/password login (no Cognito) |

The application does **not** calculate payroll. It has three jobs:

1. **Payment splitting:** split the single ABO file from Pohoda into **several output ABO files**, each with its own due date. Salaries can then be paid on a different calendar day than social/health insurance, taxes and other deductions.
2. **Verification:** check salary payments against the registry of employees and their bank accounts, mainly to catch a spoofed or changed account.
3. **Payslips:** match uploaded payslips to employees and send them by e-mail.

## 2. Monthly Workflow

```
                                       ┌──▶ salaries_2026-10.kpc         (due e.g. 12th)
Pohoda ──ABO──▶ import ─▶ classification ─┼──▶ social-insurance_2026-10.kpc (due e.g. 20th)
                          and checks      ├──▶ taxes_2026-10.kpc            (due e.g. 20th)
                                       └──▶ other_2026-10.kpc            (…)
                                                   │
                                                   ▼  manual upload to ČSOB CEB / BusinessBanking

Pohoda ──PDF──▶ payslip upload ─▶ matching ─▶ e-mail delivery ─▶ delivery log
```

### 2.1 ABO Import
ABO (KPC) is a fixed-structure text format:

```
UHL1...                      header (date, client name and number)
1 1501 ...                   accounting file header
2 <payer account> <total> <due date>                group header
<payee account> <amount in hellers> <VS> <bank code+KS> <SS> AV:<message>   items
3 +                          group end
5 +                          file end
```

The application reads the payer account and all items (payee account, amount, VS, KS, SS, message) and validates the structure and totals. The exact format and encoding of the Pohoda export will be verified against an anonymized sample.

### 2.2 Classification into Groups
**Payment groups** are defined in settings. Each group has a name, a default due day and rules that assign items to it.

| Group (example) | Assignment rule | Default due day |
|---|---|---|
| **Salaries** | payee account belongs to an employee in the registry | 12th of the month |
| **Social insurance** | payee account = ČSSZ / OSSZ account | 20th |
| **Health insurance** | payee account = health insurer account (VZP, ZPMV, OZP, …) | 20th |
| **Taxes** | payee account = tax office account | 20th |
| **Other** | everything else (garnishments, pension savings, …) | manual |

- Rules are primarily a **list of payee account numbers**, optionally combined with KS, VS prefix or message text.
- Employee payments are recognized automatically from the employee registry. Institution accounts are entered once and reused every month.
- An item not matched by any rule lands in *Other* and must be assigned manually before export.
- Each group's due date is prefilled from settings and can be overridden for a given month. A date falling on a weekend or Czech public holiday is moved to the nearest business day (configurable: forward or backward).

### 2.3 Checks

| Check | Result |
|---|---|
| Item total does not match the ABO header | **blocking error** |
| Invalid account number (mod 11, bank code per ČNB list) | blocking error |
| Payer account differs from the configured company ČSOB account | blocking error |
| Item not assigned to any group | blocking error (manual assignment required) |
| Employee account changed in the registry within the last N days | warning |
| Salary paid to an account no longer valid for the employee | blocking error |
| Active employee missing from salaries | warning |
| Salary differs by more than X % from previous month | warning |

Key invariant: **the sum of all output files must exactly equal the input.** No payment may be lost or duplicated. The application displays this as a control total.

### 2.4 Output for ČSOB
- One **separate ABO file** per non-empty group, with that group's due date.
- File name contains group, period and due date, e.g. `salaries_2026-10_due-2026-11-12.kpc`.
- Download individually or all at once as a ZIP.
- Each file shows its item count and total for easy comparison with what ČSOB displays after upload.
- The original Pohoda file and the generated files are archived.
- Each file can be marked as *uploaded to bank*, so it is clear what has been processed.

### 2.5 Payslips
- **Combined PDF from Pohoda:** split per page, each page matched by the employee number or name found in its text. A continuation page without a new employee number is appended to the previous payslip.
- Alternatively a **ZIP of PDFs** with the employee number in the file name.
- Mandatory matching preview before sending.
- PDFs are sent as-is (no password protection).
- E-mail template with `{name}` and `{period}` placeholders, background delivery, per-e-mail status, resend option.

## 3. Security (simple variant)

For 1–3 users a custom login is sufficient; Cognito is not needed.

- **Users** are stored in DynamoDB with a bcrypt password hash. New users are created by an administrator via a CLI script (`create-user`); there is no web registration.
- **Login:** after username/password verification, a Lambda issues a **signed token** (JWT, e.g. 8 h lifetime) in an `HttpOnly; Secure; SameSite=Strict` cookie. The signing key lives in AWS SSM Parameter Store (SecureString).
- **API protection:** every request is verified by a Lambda authorizer (or middleware inside the API Lambda).
- **Brute-force protection:** 5 failed attempts lock the account for 15 minutes; API Gateway throttling is enabled.
- **HTTPS** everywhere (CloudFront).
- **Data at rest:** default S3 and DynamoDB encryption (no paid KMS key). Private S3 bucket; uploads/downloads via short-lived presigned URLs.
- **Audit log:** who uploaded, changed an account, generated files or sent payslips.
- **Retention:** S3 lifecycle rules delete files after N months.
- Optional later: TOTP second factor or office IP allow-list (AWS WAF).

## 4. AWS Architecture (serverless)

```
            ┌──────────── CloudFront (HTTPS) ────────────┐
 browser ───┤  /        → S3 (static frontend)            │
            │  /api/*   → API Gateway (HTTP API)          │
            └──────────────────┬──────────────────────────┘
                               │  JWT cookie
                          ┌────▼─────┐
                          │  Lambda  │  API (Python): login, employees,
                          └┬───┬───┬─┘  ABO import/split, payslips
              ┌────────────┘   │   └──────────────┐
         ┌────▼─────┐    ┌─────▼────┐       ┌─────▼────┐
         │ DynamoDB │    │ S3 data  │       │   SQS    │
         └──────────┘    │ ABO, PDF │       └─────┬────┘
                         └──────────┘       ┌─────▼────────┐
                                            │ Lambda sender│──▶ SES ──▶ employees
                                            └──────────────┘
```

| Service | Purpose |
|---|---|
| **S3 + CloudFront** | frontend (React SPA), HTTPS |
| **API Gateway HTTP API + Lambda (Python)** | entire API including login |
| **DynamoDB** (on-demand) | users, employees, accounts, groups and rules, batches, logs |
| **S3 (private)** | ABO files, PDF payslips |
| **SSM Parameter Store** | JWT signing key, settings |
| **SQS + Lambda** | e-mail delivery queue (retries, dead-letter queue) |
| **SES** | e-mail from own domain (DKIM, SPF, DMARC) |
| **CloudWatch** | logs, error alarm |
| **AWS CDK (Python)** | infrastructure as code |

Region **eu-central-1 (Frankfurt)**.
**Estimated cost:** at this volume everything fits in or near the free tier, realistically **under 1 USD/month** (plus domain).

### Tech Stack
- **Backend:** Python 3.12. Custom ABO parser/generator, `pypdf` (PDF splitting), `pdfplumber` (text extraction), `bcrypt`, `PyJWT`, `holidays` (Czech public holidays for due-date shifting).
- **Frontend:** React + TypeScript (Vite).
- **IaC:** AWS CDK. **CI/CD:** GitHub Actions deploying via OIDC.

## 5. Data Model (DynamoDB, single-table)

| Entity | PK / SK | Attributes |
|---|---|---|
| User | `USER#<login>` / `PROFILE` | password hash, role, failed attempts, locked until |
| Employee | `EMP#<id>` / `PROFILE` | employee number, name, e-mail, active |
| Employee account | `EMP#<id>` / `ACC#<from>` | prefix, number, bank code, valid from / to |
| Account index | `ACCNUM#<account>` / `EMP#<id>` | look up employee by account |
| Payment group | `GROUP#<id>` / `META` | name, order, due day, holiday shift direction |
| Rule | `GROUP#<id>` / `RULE#<n>` | type (account / KS / VS prefix / text), value, label (e.g. "VZP") |
| Batch | `BATCH#<YYYY-MM>` / `META` | status, payer account, input total, input S3 key |
| Item | `BATCH#<YYYY-MM>` / `ITEM#<n>` | account, amount, VS/KS/SS, message, group, employee, errors |
| Output file | `BATCH#<YYYY-MM>` / `OUT#<group>` | due date, count, total, S3 key, uploaded to bank (yes/no, when) |
| Payslip | `BATCH#<YYYY-MM>` / `SLIP#<emp>` | S3 key, delivery status, time, error |
| Audit | `AUDIT#<YYYY-MM>` / `<time>#<id>` | user, action, object, detail |

## 6. Screens

1. **Login**
2. **Dashboard:** periods, batch status, which files have been uploaded to the bank, payslip status
3. **New period:** wizard: upload ABO → classification into groups (table per group, manual reassignment, due-date override) → checks → download output files
4. **Payslips:** PDF upload, matching with preview, sending
5. **Employees:** list, detail with accounts and history
6. **Settings:** company ČSOB account, payment groups and rules, e-mail template, warning thresholds
7. **Audit log**

## 7. Development Phases

**Phase 1 (MVP)**
- CDK infrastructure, login
- employee registry, payment groups and rules
- ABO import, classification, checks, generation of multiple ABO files with different due dates
- payslips: PDF splitting, matching, delivery via SES
- audit log

**Phase 2**
- comparison with previous month, account change alerts, retention, optional TOTP

## 8. Inputs Needed for Implementation

1. **Anonymized ABO file from Pohoda** with all payment types (salaries, insurance, taxes); fictitious accounts and amounts are fine.
2. **List of groups** and their due days: is salaries / deductions enough, or finer-grained?
3. **Sample payslip from Pohoda** (anonymized PDF).
4. **Sending domain** for e-mail and access to its DNS.
