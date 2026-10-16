# Návrh: webová aplikace pro zpracování mezd

## 1. Účel a výchozí podmínky

| | |
|---|---|
| Účetní / mzdový systém | **Pohoda** |
| Vstup plateb | **ABO soubor** (příkaz k úhradě exportovaný z Pohody) |
| Banka | **ČSOB** |
| Počet zaměstnanců | do 20 |
| Hosting | **AWS, serverless** |

Aplikace mzdy **nepočítá**. Má dva úkoly:

1. **Kontrola výplat:** načte ABO soubor z Pohody a každou platbu ověří proti číselníku zaměstnanců a jejich účtů. Hlídá hlavně podvržený nebo změněný účet. Po kontrole vydá ABO soubor k nahrání do ČSOB.
2. **Výplatní pásky:** nahrané pásky přiřadí zaměstnancům a rozešle je e-mailem jako zaheslovaná PDF.

## 2. Uživatelé

- **Administrátor:** nastavení (účet plátce, e-maily, šablony), správa uživatelů.
- **Mzdová účetní:** správa zaměstnanců a účtů, import ABO, pásky, odeslání.
- Volitelně **schvalovatel**, pokud má výplaty schvalovat druhá osoba.

Zaměstnanci se do aplikace nepřihlašují. Jsou to jen záznamy v číselníku.

## 3. Proces jednoho mzdového období

```
Pohoda ──ABO──▶ 1. Import ABO ─▶ 2. Párování a kontrola ─▶ 3. Schválení ─▶ 4. Stažení ABO ──▶ ČSOB
Pohoda ──PDF──▶ 5. Nahrání pásek ─▶ 6. Přiřazení ─▶ 7. Odeslání e-mailů ─▶ protokol doručení
```

### 3.1 Import ABO souboru
ABO (KPC) je textový formát s pevnou strukturou:

```
UHL1...                      hlavička (datum, název a číslo klienta)
1 1501 ...                   hlavička účetního souboru
2 <účet plátce> <součet> <datum splatnosti>      hlavička skupiny
<účet příjemce> <částka v haléřích> <VS> <kód banky+KS> <SS> AV:<zpráva>   položky
3 +                          konec skupiny
5 +                          konec souboru
```

Aplikace z něj načte účet plátce, datum splatnosti a položky: účet příjemce, částku, VS, KS, SS a zprávu. Zkontroluje strukturu i to, že součet položek odpovídá hlavičce skupiny. Kódování a drobné odchylky exportu z Pohody ověříme na anonymizovaném vzorku.

### 3.2 Párování a kontrola
Každá položka se spáruje se zaměstnancem **podle čísla účtu příjemce**. Pokud Pohoda plní do VS osobní číslo, použije se jako druhý klíč.

| Kontrola | Výsledek |
|---|---|
| Účet není v číselníku u žádného zaměstnance | **blokující chyba** (možný podvod nebo překlep) |
| Účet patří jinému zaměstnanci než VS / osobní číslo | blokující chyba |
| Účet byl v číselníku změněn v posledních N dnech | upozornění |
| Neplatné číslo účtu (modulo 11, kód banky dle ČNB) | blokující chyba |
| Částka se liší o víc než X % oproti minulému měsíci | upozornění |
| Aktivní zaměstnanec v souboru chybí | upozornění |
| Účet plátce v souboru neodpovídá nastavenému firemnímu účtu ČSOB | blokující chyba |

### 3.3 Schválení a výstup pro ČSOB
- Když dávka nemá žádnou blokující chybu, jde ji schválit. Volitelně ji schvaluje druhá osoba.
- Výstupem je **ABO soubor pro ČSOB**. Aplikace ho vygeneruje znovu ze zkontrolovaných dat: pevná struktura, kódování a datum splatnosti z nastavení. Do bankovnictví tak jde přesně to, co prošlo kontrolou. Původní soubor z Pohody se archivuje.
- Soubor se nahraje do **ČSOB CEB / ČSOB BusinessBanking** (import ABO). Možnost otisku souboru (hash) pro kontrolu, že se po stažení nezměnil.
- Později lze přidat přímé odeslání přes **ČSOB CEB API**, bez ručního nahrávání.

### 3.4 Výplatní pásky
Pohoda umí pásky vytisknout do PDF. Aplikace podporuje dva vstupy:
- **Jedno hromadné PDF:** aplikace ho rozdělí po stránkách a každou stránku přiřadí podle textu, který na ní najde (osobní číslo nebo jméno). Páska na dvě stránky se pozná podle toho, že pokračující stránka nemá nové osobní číslo.
- **ZIP s PDF:** přiřazení podle názvu souboru (např. `1234_novak.pdf`).

Před odesláním je povinný náhled přiřazení: kdo pásku má, kdo ne a co zůstalo nepřiřazené.

### 3.5 Odeslání
- Každá páska je **PDF zaheslované** (AES-256) heslem nastaveným u zaměstnance. Heslo se nikdy neposílá e-mailem.
- Šablona e-mailu s proměnnými `{jmeno}` a `{obdobi}`.
- Odeslání na pozadí, stav u každého e-mailu (čeká / odesláno / doručeno / chyba), možnost poslat znovu.

## 4. Architektura na AWS (serverless)

```
            ┌──────────── CloudFront ────────────┐
 prohlížeč ─┤  /        → S3 (statický frontend) │
            │  /api/*   → API Gateway (HTTP API) │
            └──────────────────┬─────────────────┘
                               │  JWT z Cognito (s MFA)
                          ┌────▼─────┐
                          │  Lambda  │  API (Python)
                          └┬───┬───┬─┘
              ┌────────────┘   │   └──────────────┐
         ┌────▼─────┐    ┌─────▼────┐       ┌─────▼────┐
         │ DynamoDB │    │ S3 data  │       │   SQS    │
         │ (data)   │    │ ABO, PDF │       │ fronta   │
         └──────────┘    │ SSE-KMS  │       └─────┬────┘
                         └──────────┘       ┌─────▼────────┐
                                            │ Lambda sender│──▶ SES ──▶ zaměstnanec
                                            └──────────────┘
```

| Služba | Účel |
|---|---|
| **S3 + CloudFront** | frontend (React SPA), HTTPS |
| **Cognito** | přihlášení uživatelů aplikace, povinné MFA (TOTP) |
| **API Gateway HTTP API + Lambda (Python)** | REST API: zaměstnanci, import ABO, kontrola, generování ABO, pásky |
| **DynamoDB** (on-demand) | zaměstnanci, účty, dávky, položky, logy. Pro 20 lidí stačí jedna tabulka. |
| **S3 (privátní bucket, SSE-KMS)** | ABO soubory a PDF pásky; nahrávání přes presigned URL; lifecycle pro automatické mazání |
| **KMS** | šifrovací klíč pro S3 a citlivá pole (čísla účtů, hesla k PDF) |
| **SQS + Lambda** | fronta na odesílání e-mailů s opakováním a dead-letter queue |
| **SES** | odesílání e-mailů z vlastní domény (DKIM, SPF, DMARC) |
| **CloudWatch** | logy, alarm na chyby odeslání |
| **AWS CDK** | celá infrastruktura jako kód, nasazení jedním příkazem |

Region **eu-central-1 (Frankfurt)**, data zůstávají v EU (GDPR).

**Odhad nákladů:** při 20 zaměstnancích a jednom běhu měsíčně se většina služeb vejde do free tieru. Reálně jde o **cca 1–3 USD měsíčně**, hlavně KMS klíč (1 USD) a doména.

### Technologie
- **Backend:** Python 3.12 v Lambdě. `pypdf` pro dělení a šifrování PDF, `pdfplumber` pro čtení textu, vlastní parser a generátor ABO.
- **Frontend:** React + TypeScript (Vite), přihlášení přes Cognito (AWS Amplify Auth).
- **IaC:** AWS CDK (Python).
- **CI/CD:** GitHub Actions s nasazením do AWS přes OIDC, bez uložených klíčů.

## 5. Datový model (DynamoDB, single-table)

| Entita | Klíč (PK / SK) | Atributy |
|---|---|---|
| Zaměstnanec | `EMP#<id>` / `PROFILE` | osobní číslo, jméno, e-mail, heslo k PDF (šifrovaně), aktivní |
| Účet | `EMP#<id>` / `ACC#<od>` | předčíslí, číslo, kód banky, platný od / do |
| Index účtu | `ACCNUM#<účet>` / `EMP#<id>` | rychlé vyhledání zaměstnance podle účtu |
| Dávka | `BATCH#<YYYY-MM>` / `META` | stav, účet plátce, splatnost, součet, S3 klíče souborů, kdo / kdy schválil |
| Položka | `BATCH#<YYYY-MM>` / `ITEM#<n>` | účet, částka, VS/KS/SS, zaměstnanec, chyby, upozornění |
| Páska | `BATCH#<YYYY-MM>` / `SLIP#<emp>` | S3 klíč, stav odeslání, čas, chyba |
| Audit | `AUDIT#<YYYY-MM>` / `<čas>#<id>` | uživatel, akce, objekt, před / po |

Změna účtu nepřepisuje historii. Nový účet dostane platnost od data, takže minulé dávky zůstanou dohledatelné.

## 6. Obrazovky

1. **Přehled:** období a jejich stav
2. **Zaměstnanci:** seznam, detail s účty a historií změn
3. **Nové období:** průvodce: nahrát ABO → výsledek kontroly → schválit → stáhnout ABO pro ČSOB
4. **Pásky:** nahrání PDF, tabulka přiřazení s náhledem, *Odeslat*
5. **Nastavení:** firemní účet ČSOB, šablona e-mailu, odesílací adresa, prahy upozornění
6. **Audit log**

## 7. Bezpečnost

- Cognito s povinným MFA. Registrace jen přes pozvánku od administrátora.
- Privátní S3 s blokovaným veřejným přístupem, šifrování KMS, presigned URL s krátkou platností.
- Citlivá pole v DynamoDB šifrovaná přes KMS.
- Audit log každé změny účtu a každého odeslání; volitelně e-mailové upozornění administrátorovi při změně účtu.
- Retence: soubory se po N měsících automaticky smažou (S3 lifecycle).
- Lambdy mají přístup jen k tomu, co nutně potřebují (least-privilege IAM).

## 8. Fáze vývoje

**Fáze 1 (MVP)**
- CDK infrastruktura, Cognito přihlášení
- číselník zaměstnanců a účtů
- import ABO z Pohody, párování, kontroly, vygenerování ABO pro ČSOB
- nahrání hromadného PDF s pásky, rozdělení, přiřazení, zaheslování, odeslání přes SES
- audit log

**Fáze 2**
- schvalování druhou osobou, porovnání s minulým měsícem, upozornění na změny účtů, retence

**Fáze 3**
- přímé odeslání plateb přes ČSOB CEB API

## 9. Co potřebuji pro implementaci

1. **Anonymizovaný ABO soubor z Pohody** (stačí smyšlené účty a částky), abych ověřil přesný formát a kódování.
2. **Ukázku výplatní pásky z Pohody** (PDF, anonymizovanou), abych ověřil, jak z ní číst osobní číslo nebo jméno.
3. **Doménu** pro odesílání e-mailů a přístup k jejímu DNS (kvůli SES).
4. Čím se zaměstnanci budou otevírat PDF: vlastní heslo, nebo např. datum narození?
5. Plní Pohoda do VS u mezd osobní číslo?
