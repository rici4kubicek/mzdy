# Návrh: webová aplikace pro zpracování mezd

## 1. Účel a výchozí podmínky

| | |
|---|---|
| Účetní / mzdový systém | **Pohoda** |
| Vstup plateb | **jeden ABO soubor** (příkaz k úhradě exportovaný z Pohody: mzdy, pojistné, daně, …) |
| Banka | **ČSOB**, soubory se do bankovnictví nahrávají ručně |
| Počet zaměstnanců | do 20 |
| Hosting | **AWS, serverless** |
| Zabezpečení | jednoduché přihlášení jménem a heslem (bez Cognito) |

Aplikace mzdy **nepočítá**. Má tři úkoly:

1. **Rozdělení plateb:** jeden ABO soubor z Pohody rozdělí na **více výstupních ABO souborů**, každý s vlastním datem splatnosti. Mzdy tak mohou odejít jiný kalendářní den než pojistné, daně a další odvody.
2. **Kontrola:** platby zaměstnancům ověří proti číselníku zaměstnanců a jejich účtů. Hlídá hlavně podvržený nebo změněný účet.
3. **Výplatní pásky:** nahrané pásky přiřadí zaměstnancům a rozešle je e-mailem jako zaheslovaná PDF.

## 2. Proces jednoho mzdového období

```
                                  ┌──▶ mzdy_2026-10.abo      (splatnost např. 12.)
Pohoda ──ABO──▶ import ─▶ roztřídění ─┼──▶ pojistne_2026-10.abo  (splatnost např. 20.)
                          a kontrola  ├──▶ dane_2026-10.abo      (splatnost např. 20.)
                                  └──▶ ostatni_2026-10.abo   (…)
                                              │
                                              ▼  ruční nahrání do ČSOB CEB / BusinessBanking

Pohoda ──PDF──▶ nahrání pásek ─▶ přiřazení ─▶ odeslání e-mailů ─▶ protokol
```

### 2.1 Import ABO souboru
ABO (KPC) je textový formát s pevnou strukturou:

```
UHL1...                      hlavička (datum, název a číslo klienta)
1 1501 ...                   hlavička účetního souboru
2 <účet plátce> <součet> <datum splatnosti>      hlavička skupiny
<účet příjemce> <částka v haléřích> <VS> <kód banky+KS> <SS> AV:<zpráva>   položky
3 +                          konec skupiny
5 +                          konec souboru
```

Aplikace z něj načte účet plátce a všechny položky: účet příjemce, částku, VS, KS, SS a zprávu. Zkontroluje strukturu i součty. Přesný formát a kódování exportu z Pohody ověříme na anonymizovaném vzorku.

### 2.2 Roztřídění do skupin
V nastavení se definují **skupiny plateb**. Každá má název, výchozí den splatnosti a pravidla, podle kterých se do ní položky zařadí.

| Skupina (příklad) | Pravidlo zařazení | Výchozí splatnost |
|---|---|---|
| **Mzdy** | účet příjemce patří zaměstnanci z číselníku | 12. den v měsíci |
| **Sociální pojištění** | účet příjemce = účet ČSSZ / OSSZ | 20. den |
| **Zdravotní pojištění** | účet příjemce = účet zdravotní pojišťovny (VZP, ZPMV, OZP, …) | 20. den |
| **Daně** | účet příjemce = účet finančního úřadu | 20. den |
| **Ostatní** | vše ostatní (exekuce, penzijní spoření, …) | ručně |

- Pravidla se zapisují hlavně jako **seznam čísel účtů příjemců**. Volitelně je jde doplnit o KS, VS (prefix) nebo text zprávy.
- Platby zaměstnancům se poznají automaticky podle číselníku zaměstnanců. Účty institucí se do pravidel zadají jednou a pak platí každý měsíc.
- Položka, kterou žádné pravidlo nezachytí, spadne do skupiny *Ostatní* a před exportem ji musí uživatel ručně zařadit.
- Datum splatnosti každé skupiny se předvyplní z nastavení a jde ho pro konkrétní měsíc změnit. Když vychází na víkend nebo státní svátek, aplikace ho posune na nejbližší pracovní den (nastavitelně dopředu nebo dozadu).

### 2.3 Kontroly

| Kontrola | Výsledek |
|---|---|
| Součet položek neodpovídá hlavičce ABO | **blokující chyba** |
| Neplatné číslo účtu (modulo 11, kód banky dle ČNB) | blokující chyba |
| Účet plátce neodpovídá nastavenému firemnímu účtu ČSOB | blokující chyba |
| Položka nezařazená do žádné skupiny | blokující chyba (nutné ruční zařazení) |
| Účet zaměstnance byl v číselníku změněn v posledních N dnech | upozornění |
| Platba ve skupině „Mzdy“ jde na účet, který u zaměstnance už neplatí | blokující chyba |
| Aktivní zaměstnanec ve mzdách chybí | upozornění |
| Mzda se liší o víc než X % oproti minulému měsíci | upozornění |

Důležité: **součet všech výstupních souborů musí přesně odpovídat vstupu**, žádná platba se neztratí ani nezdvojí. Aplikace to zobrazí jako kontrolní součet.

### 2.4 Výstup pro ČSOB
- Pro každou neprázdnou skupinu se vygeneruje **samostatný ABO soubor** s datem splatnosti dané skupiny.
- Název souboru obsahuje skupinu, období a datum splatnosti, např. `mzdy_2026-10_splatnost-2026-11-12.kpc`.
- Stažení po jednom nebo vše najednou jako ZIP.
- U každého souboru se zobrazí počet plateb a součet, aby šel po nahrání snadno porovnat s tím, co ukáže ČSOB.
- Původní soubor z Pohody i vygenerované soubory se archivují.
- Každý soubor jde označit jako *nahráno do banky*, takže je vidět, co už bylo zpracováno.

### 2.5 Výplatní pásky
- **Hromadné PDF z Pohody:** aplikace ho rozdělí po stránkách a každou stránku přiřadí podle osobního čísla nebo jména, které na ní najde. Pokračující stránka bez nového osobního čísla se připojí k předchozí pásce.
- Alternativně **ZIP s PDF** s osobním číslem v názvu souboru.
- Povinný náhled přiřazení před odesláním.
- Každá páska je PDF **zaheslované** heslem nastaveným u zaměstnance. Heslo se nikdy neposílá e-mailem.
- Šablona e-mailu s proměnnými `{jmeno}` a `{obdobi}`, odeslání na pozadí, stav každého e-mailu, možnost poslat znovu.

## 3. Zabezpečení (jednoduchá varianta)

Pro 1–3 uživatele stačí vlastní přihlášení. Cognito není potřeba.

- **Uživatelé** jsou uložení v DynamoDB, heslo jako hash (bcrypt). Nového uživatele založí administrátor skriptem z příkazové řádky (`create-user`). Registrace přes web není.
- **Přihlášení:** po zadání jména a hesla Lambda vydá **podepsaný token** (JWT, platnost např. 8 h) v cookie `HttpOnly; Secure; SameSite=Strict`. Podpisový klíč je v AWS SSM Parameter Store (SecureString).
- **Ochrana API:** každý požadavek ověří Lambda authorizer (případně middleware přímo v API Lambdě).
- **Ochrana proti hádání hesla:** po 5 chybných pokusech se účet na 15 minut zablokuje, API Gateway má nastavené omezení počtu požadavků (throttling).
- **HTTPS** všude (CloudFront).
- **Data v klidu** šifrují S3 a DynamoDB výchozím šifrováním (bez placeného KMS klíče). S3 bucket je privátní, soubory se stahují a nahrávají přes presigned URL s krátkou platností.
- **Audit log:** kdo nahrál, změnil účet, vygeneroval soubory nebo odeslal pásky.
- **Retence:** S3 lifecycle soubory po N měsících smaže.
- Volitelně později: druhý faktor (TOTP) nebo omezení přístupu na IP adresu kanceláře (AWS WAF).

## 4. Architektura na AWS (serverless)

```
            ┌──────────── CloudFront (HTTPS) ────────────┐
 prohlížeč ─┤  /        → S3 (statický frontend)          │
            │  /api/*   → API Gateway (HTTP API)          │
            └──────────────────┬──────────────────────────┘
                               │  cookie s JWT
                          ┌────▼─────┐
                          │  Lambda  │  API (Python): login, zaměstnanci,
                          └┬───┬───┬─┘  import/rozdělení ABO, pásky
              ┌────────────┘   │   └──────────────┐
         ┌────▼─────┐    ┌─────▼────┐       ┌─────▼────┐
         │ DynamoDB │    │ S3 data  │       │   SQS    │
         └──────────┘    │ ABO, PDF │       └─────┬────┘
                         └──────────┘       ┌─────▼────────┐
                                            │ Lambda sender│──▶ SES ──▶ zaměstnanci
                                            └──────────────┘
```

| Služba | Účel |
|---|---|
| **S3 + CloudFront** | frontend (React SPA), HTTPS |
| **API Gateway HTTP API + Lambda (Python)** | celé API včetně přihlášení |
| **DynamoDB** (on-demand) | uživatelé, zaměstnanci, účty, skupiny a pravidla, dávky, logy |
| **S3 (privátní)** | ABO soubory, PDF pásky |
| **SSM Parameter Store** | podpisový klíč JWT, nastavení |
| **SQS + Lambda** | fronta na odesílání e-mailů (opakování, dead-letter queue) |
| **SES** | e-maily z vlastní domény (DKIM, SPF, DMARC) |
| **CloudWatch** | logy, alarm na chyby |
| **AWS CDK (Python)** | celá infrastruktura jako kód |

Region **eu-central-1 (Frankfurt)**.
**Odhad nákladů:** při tomto objemu se vše vejde do free tieru nebo se mu blíží, reálně **do 1 USD měsíčně** (plus doména).

### Technologie
- **Backend:** Python 3.12. Vlastní parser a generátor ABO, `pypdf` (dělení a šifrování PDF), `pdfplumber` (čtení textu), `bcrypt`, `PyJWT`, knihovna `holidays` (české svátky pro posun splatnosti).
- **Frontend:** React + TypeScript (Vite).
- **IaC:** AWS CDK. **CI/CD:** GitHub Actions s nasazením přes OIDC.

## 5. Datový model (DynamoDB, single-table)

| Entita | PK / SK | Atributy |
|---|---|---|
| Uživatel | `USER#<login>` / `PROFILE` | hash hesla, role, počet chybných pokusů, blokace do |
| Zaměstnanec | `EMP#<id>` / `PROFILE` | osobní číslo, jméno, e-mail, heslo k PDF, aktivní |
| Účet zaměstnance | `EMP#<id>` / `ACC#<od>` | předčíslí, číslo, kód banky, platný od / do |
| Index účtu | `ACCNUM#<účet>` / `EMP#<id>` | vyhledání zaměstnance podle účtu |
| Skupina plateb | `GROUP#<id>` / `META` | název, pořadí, den splatnosti, posun při svátku |
| Pravidlo | `GROUP#<id>` / `RULE#<n>` | typ (účet / KS / VS prefix / text), hodnota, popis (např. „VZP“) |
| Dávka | `BATCH#<YYYY-MM>` / `META` | stav, účet plátce, součet vstupu, S3 klíč vstupu |
| Položka | `BATCH#<YYYY-MM>` / `ITEM#<n>` | účet, částka, VS/KS/SS, zpráva, skupina, zaměstnanec, chyby |
| Výstupní soubor | `BATCH#<YYYY-MM>` / `OUT#<skupina>` | splatnost, počet, součet, S3 klíč, nahráno do banky (ano/ne, kdy) |
| Páska | `BATCH#<YYYY-MM>` / `SLIP#<emp>` | S3 klíč, stav odeslání, čas, chyba |
| Audit | `AUDIT#<YYYY-MM>` / `<čas>#<id>` | uživatel, akce, objekt, detail |

## 6. Obrazovky

1. **Přihlášení**
2. **Přehled:** období, stav dávky, které soubory už jsou nahrané v bance, stav pásek
3. **Nové období:** průvodce: nahrát ABO → roztřídění do skupin (tabulka po skupinách, ruční přeřazení, úprava dat splatnosti) → kontroly → stažení výstupních souborů
4. **Pásky:** nahrání PDF, přiřazení s náhledem, odeslání
5. **Zaměstnanci:** seznam, detail s účty a historií
6. **Nastavení:** firemní účet ČSOB, skupiny plateb a pravidla, šablona e-mailu, prahy upozornění
7. **Audit log**

## 7. Fáze vývoje

**Fáze 1 (MVP)**
- CDK infrastruktura, přihlášení
- číselník zaměstnanců, skupiny plateb a pravidla
- import ABO, roztřídění, kontroly, generování více ABO souborů s různou splatností
- pásky: rozdělení PDF, přiřazení, zaheslování, odeslání přes SES
- audit log

**Fáze 2**
- porovnání s minulým měsícem, upozornění na změny účtů, retence, volitelné TOTP

## 8. Co potřebuji pro implementaci

1. **Anonymizovaný ABO soubor z Pohody** se všemi typy plateb (mzdy, pojistné, daně), stačí smyšlené účty a částky.
2. **Seznam skupin**, které chcete, a jejich dny splatnosti: stačí mzdy / odvody, nebo jemněji?
3. **Ukázku výplatní pásky z Pohody** (PDF, anonymizovanou).
4. **Doménu** pro odesílání e-mailů a přístup k DNS.
5. Heslo k PDF pro zaměstnance: vlastní, nebo např. datum narození?
