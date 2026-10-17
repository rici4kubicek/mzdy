# Payroll payments and payslips

A small serverless web application for a company with up to ~20 employees that
processes payroll output from **Pohoda** and prepares payments for **ČSOB**:

1. **Splits** the single ABO payment file exported from Pohoda into three ABO
   files: **salaries** (due on the 15th), **deductions** (20th) and **pension
   savings** (25th) of the following month, and writes a comment to every line.
2. **Verifies** salary payments against the employee registry: a payment to an
   account that is not registered (or no longer valid) for an employee blocks
   the batch.
3. **E-mails payslips**: a combined payslip PDF from Pohoda (or a ZIP of PDFs) is
   split per employee and sent through Amazon SES.

See [docs/DESIGN.md](docs/DESIGN.md) for the full design. The user interface is
in Czech; code and documentation are in English.

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | Python 3.12 package `payroll`: ABO reader/writer, classification, checks, payslip splitting, API and mailer Lambdas, CLI |
| `frontend/` | React + TypeScript single-page app (Vite) |
| `infra/` | AWS CDK (Python) stack |
| `scripts/deploy.sh` | One-command build and deploy |

## How it works

```
Pohoda ──ABO──▶ import ─▶ classification + checks ─┬─▶ 15_mzdy_YYYY-MM.kpc                   ─┐
                                                   ├─▶ 20_odvody_YYYY-MM.kpc                 ─┼─▶ manual upload to ČSOB
                                                   └─▶ 25_duchodove-pripojisteni_YYYY-MM.kpc ─┘
Pohoda ──PDF──▶ split per employee ─▶ preview ─▶ SQS ─▶ SES ─▶ employees
```

Classification by payee account + bank code:

* account of an active employee (valid on the import date) → **salaries**,
  comment `AV: Mzda MMYY NP: First Last`
* account in *Settings → Deduction accounts* → **deductions**, comment `NP: <label>`
* account in *Settings → Pension accounts* → **pension savings**, comment
  `NP: <Surname> důch. připojištění`; a pension account shared by several
  employees is told apart by a variable symbol found anywhere on the line
* anything else → **unassigned**, must be assigned manually before files can be
  generated (an unknown account can never be forced into salaries)

Output files copy the input lines: the `UHL` date becomes today, line `1` is kept,
line `2` gets the group's due date (and its own total), data lines get the comment.
Each output is re-parsed after generation, and the sum of all outputs must equal
the input exactly. Once a file is marked as *uploaded to bank*, the batch can no
longer be changed.

Due dates lie in the month after the payroll period (April payroll → May) and move
to the previous business day when they fall on a weekend or Czech public holiday
(fixed holidays are hard-coded, Good Friday and Easter Monday are computed with
Gauss's Easter algorithm). They are configurable and can be overridden per period.

## Local development

Python dependencies are managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` +
`uv.lock` in `backend/` and `infra/`).

```bash
# backend tests
cd backend
uv sync
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests

# run the whole app locally against emulated AWS (moto), no AWS account needed
(cd ../frontend && npm ci && npm run build)
uv run python devserver.py
# open http://localhost:8000 and log in as admin / admin-password
```

For frontend work with hot reload, run `devserver.py` and in another terminal
`VITE_API_PROXY=http://localhost:8000 npm run dev` in `frontend/`.

## Deployment to AWS

Prerequisites: AWS CLI configured for the target account, Node.js 22, uv.

```bash
# once per account/region
npx aws-cdk@2.1145.0 bootstrap aws://ACCOUNT_ID/eu-central-1

# build and deploy (creates the JWT signing key in SSM on first run)
./scripts/deploy.sh -c senderDomain=firma.cz
```

The stack outputs the application URL, the DynamoDB table and the data bucket.

**Create a user** (there is no self-registration):

```bash
cd backend
TABLE_NAME=<TableName output> BUCKET_NAME=<DataBucketName output> AWS_REGION=eu-central-1 \
  uv run python -m payroll.cli create-user ucetni --role admin
```

Running the same command again resets the password.

**E-mail (SES)**:

1. Deploy with `-c senderDomain=firma.cz` and add the three `DkimRecord*` CNAME
   records from the stack outputs to the domain's DNS. Add SPF and DMARC records too.
2. A new AWS account starts in the SES *sandbox*, which only delivers to verified
   addresses. Request production access in the SES console (Account dashboard →
   Request production access).
3. Set the sender address (e.g. `mzdy@firma.cz`) in the application settings.

Estimated running cost at this volume: under 1 USD per month.

## Security notes

* Users are stored in DynamoDB with scrypt password hashes; sessions are 8-hour
  JWTs in an `HttpOnly; Secure; SameSite=Strict` cookie. Five failed logins lock
  the account for 15 minutes. State-changing requests require an
  `X-Requested-With` header.
* All data stays in `eu-central-1`. S3 and DynamoDB are encrypted at rest, the
  data bucket is private and files are accessed through 5-minute presigned URLs.
  Files under `batches/` expire after `retentionDays` (default 730, see `infra/cdk.json`).
* Every change (employee accounts, settings, imports, generated files, sent
  payslips) is recorded in the audit log.

## To verify with real data

The ABO reader follows the standard KPC format and accepts the common variants,
but it has not yet been tested with a real export from Pohoda. Before the first
production run:

* import an anonymized Pohoda ABO file and check the classification;
* upload a generated file to ČSOB CEB / BusinessBanking and check that it is
  accepted and that the due dates and amounts match;
* upload a payslip PDF from Pohoda and check that the employee number is found
  (adjust *Settings → payslip pattern* if necessary).
