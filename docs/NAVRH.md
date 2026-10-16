# Návrh: webová aplikace pro zpracování mezd

## 1. Účel

Aplikace převezme výstup ze mzdového systému a udělá z něj dvě věci:

1. **Výplaty**: z importovaných mezd sestaví platební příkaz pro banku. Každá částka jde na účet zaměstnance, který je uložený v aplikaci.
2. **Výplatní pásky**: nahrané pásky přiřadí jednotlivým zaměstnancům a rozešle je e-mailem.

Mzdy aplikace **nepočítá**. Pracuje jen s hotovými čísly ze mzdového softwaru.

## 2. Uživatelé a role

| Role | Co může |
|---|---|
| **Administrátor** | správa uživatelů aplikace, nastavení (SMTP, banka, šablony e-mailů) |
| **Mzdová účetní** | správa zaměstnanců a jejich účtů, import mezd, nahrání pásek, odeslání |
| **Schvalovatel** (volitelně) | schválí dávku před vygenerováním platebního příkazu nebo odesláním pásek |

Zaměstnanci se do aplikace nepřihlašují. Jsou to jen záznamy v číselníku.

## 3. Hlavní proces (jedno mzdové období)

```
 ┌──────────────┐   ┌───────────────┐   ┌──────────────┐   ┌──────────────┐
 │ 1. Import    │──▶│ 2. Párování   │──▶│ 3. Kontrola  │──▶│ 4. Platební  │
 │ souboru mezd │   │ na zaměstnance│   │ a schválení  │   │ příkaz (ABO/ │
 └──────────────┘   └───────────────┘   └──────────────┘   │ SEPA XML)    │
                                                           └──────────────┘
 ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
 │ 5. Nahrání   │──▶│ 6. Párování   │──▶│ 7. Odeslání  │──▶ protokol doručení
 │ pásek (PDF)  │   │ pásek         │   │ e-mailů      │
 └──────────────┘   └───────────────┘   └──────────────┘
```

### 3.1 Import mezd
- Vstup: CSV / XLSX (případně export konkrétního mzdového SW, viz otázky v kap. 10).
- Při prvním importu se nastaví **mapování sloupců** (osobní číslo, jméno, částka, případně číslo účtu, VS/SS). Mapování se uloží jako šablona pro další měsíce.
- Každý import vytvoří **dávku** pro období (např. 2026-10) ve stavu *koncept*.

### 3.2 Párování na zaměstnance
- Klíč: **osobní číslo** (náhradní klíč: jméno, případně rodné číslo).
- Účet pro výplatu se bere z číselníku zaměstnanců. Když soubor obsahuje i číslo účtu, aplikace ho porovná a **upozorní na rozdíl**, protože to může být chyba nebo podvod.
- Řádky bez nalezeného zaměstnance nebo bez účtu se označí jako chyba. Dávku s chybou nejde dokončit.

### 3.3 Kontrola
- Validace čísla účtu: český formát `předčíslí-číslo/kód banky` s kontrolou modulo 11, IBAN s kontrolním součtem, kód banky z číselníku ČNB.
- Souhrn: počet plateb, celková částka, porovnání s předchozím měsícem (upozornění na velké odchylky u jednotlivců).
- Volitelně se dávka **zamyká schválením** (princip čtyř očí).

### 3.4 Platební příkaz
- Výstup podle banky:
  - **ABO / KPC** (`.kpc`): standard pro české banky (ČSOB, KB, ČS, Fio, …)
  - **SEPA XML pain.001**: univerzální, umí i platby do zahraničí
- Nastavení: účet plátce, datum splatnosti, VS/KS/SS, zpráva pro příjemce (např. „Mzda 10/2026“).
- Soubor se stáhne a ručně nahraje do internetového bankovnictví (napojení přes bankovní API lze přidat později).

### 3.5 Výplatní pásky
Podporované vstupy:
- **ZIP s jednotlivými PDF**: přiřazení podle názvu souboru (např. `1234_novak.pdf`, kde 1234 je osobní číslo).
- **Jedno hromadné PDF**: aplikace ho rozdělí po stránkách a každou stránku přiřadí podle textu, který na ní najde (osobní číslo / jméno).

Náhled přiřazení před odesláním: kdo pásku má, kdo ne, které pásky zůstaly nepřiřazené.

### 3.6 Odeslání e-mailů
- Odesílá se přes vlastní SMTP nebo službu (Microsoft 365, Gmail Workspace, SendGrid, …).
- Pásky obsahují osobní údaje, proto je **PDF zaheslované**. Heslo je zvolené per zaměstnanec, např. datum narození nebo vlastní heslo, a nikdy se neposílá v tom samém e-mailu.
- Šablona e-mailu s proměnnými (`{jmeno}`, `{obdobi}`).
- E-maily se odesílají ve frontě na pozadí s omezením rychlosti. Stav každého e-mailu je vidět (čeká / odesláno / chyba) a jde ho odeslat znovu.
- Možnost poslat jednomu zaměstnanci znovu nebo na jinou adresu.

## 4. Datový model

```
Zamestnanec
  id, osobni_cislo (unikátní), jmeno, prijmeni, email,
  heslo_pdf (šifrovaně) | datum_narozeni, aktivni, poznamka

BankovniUcet
  id, zamestnanec_id, predcisli, cislo, kod_banky | iban, bic,
  platny_od, platny_do, vychozi (bool)
  -- historie účtů: změna účtu neovlivní minulé dávky

MzdoveObdobi / Davka
  id, obdobi (YYYY-MM), stav (koncept → zkontrolovano → schvaleno → vyplaceno),
  zdrojovy_soubor, vytvoril, schvalil, datum_splatnosti

PolozkaVyplaty
  id, davka_id, zamestnanec_id, castka, ucet_snapshot,
  ucet_ze_souboru, vs, ks, ss, stav_validace, chyby[]

PlatebniPrikaz
  id, davka_id, format (ABO|SEPA), soubor, vygenerovano_kdy, kym

VyplatniPaska
  id, davka_id, zamestnanec_id (nullable = nepřiřazená), soubor_pdf,
  zdroj (ZIP|split), stav_odeslani, odeslano_kdy, chyba

EmailLog
  id, paska_id, prijemce, predmet, stav, smtp_odpoved, cas

AuditLog
  id, uzivatel, akce, objekt, pred, po, cas

Uzivatel (aplikace)
  id, email, role, 2FA
```

## 5. Obrazovky

1. **Přehled**: období a stav jejich dávek
2. **Zaměstnanci**: seznam, detail s účty a historií, hromadný import zaměstnanců z CSV
3. **Nové období**: průvodce (wizard) podle kroků z kapitoly 3
4. **Detail dávky**: tabulka plateb s chybami a upozorněními, souhrn, tlačítka *Schválit* a *Stáhnout příkaz*
5. **Pásky**: nahrání, tabulka přiřazení, náhled PDF, *Odeslat vše* / *Odeslat vybrané*
6. **Nastavení**: účet plátce, formát banky, SMTP, šablony e-mailů, mapování importu
7. **Audit log**

## 6. Bezpečnost a GDPR

Aplikace pracuje s mzdami, čísly účtů a rodnými čísly, tedy s citlivými osobními údaji.
- Přihlášení s **dvoufázovým ověřením** a role podle kapitoly 2.
- HTTPS. Šifrování citlivých polí v databázi (čísla účtů, hesla k PDF) a šifrované úložiště souborů.
- **Audit log** všech změn účtů a všech odeslání. Změna účtu zaměstnance je nejčastější vektor podvodu, proto jde o ni upozornit.
- Retence: pásky a importy se po N měsících automaticky mažou (nastavitelné).
- Provoz ideálně on-premise nebo v EU cloudu.

## 7. Technologie (doporučení)

| Vrstva | Volba | Proč |
|---|---|---|
| Backend | **Python + Django** | rychlé CRUD a admin, silné knihovny na PDF a Excel |
| DB | PostgreSQL | |
| Fronta e-mailů | Celery + Redis (nebo Django-Q) | odesílání na pozadí, opakování |
| Frontend | Django šablony + HTMX (případně React, pokud bude potřeba bohatší UI) | jednoduché, bez zbytečné složitosti |
| PDF | `pypdf` (rozdělení, šifrování), `pdfplumber` (čtení textu) | |
| Import | `openpyxl`, `pandas` | |
| Nasazení | Docker Compose | |

Alternativou je TypeScript (Next.js + Prisma), pokud tým preferuje JavaScript.

## 8. Fáze vývoje

**MVP (fáze 1)**
- přihlášení, role
- číselník zaměstnanců a účtů + import z CSV
- import mezd (CSV/XLSX s mapováním), párování, validace účtů
- export ABO/KPC
- nahrání ZIP s PDF, přiřazení podle názvu, odeslání e-mailů s heslem v PDF
- audit log

**Fáze 2**
- dělení hromadného PDF, SEPA XML, schvalovací workflow, porovnání s minulým měsícem, 2FA, retence

**Fáze 3**
- napojení na API banky, přímý import ze mzdového SW, výplata na více účtů (rozdělení částky)

## 9. Rizika

- Rozdíl mezi účtem ve mzdovém SW a v aplikaci: aplikace na něj upozorní, ale musí být jasné, který zdroj platí.
- Chybné přiřazení pásky znamená únik osobních údajů. Proto je povinný náhled přiřazení a heslo na PDF.
- Doručitelnost e-mailů: je potřeba nastavit SPF, DKIM a DMARC pro odesílací doménu.

## 10. Otevřené otázky

1. Z jakého mzdového systému je vstupní soubor (Pohoda, Money S3, ABRA, Helios, Vema, …)? A v jakém formátu (CSV, XLSX, TXT)? Ideálně pošlete anonymizovaný vzorek.
2. Jak vypadají pásky: jeden PDF na zaměstnance, nebo jedno hromadné PDF?
3. Jaká banka a jaký formát příkazu (ABO/KPC, SEPA XML)?
4. Může mít zaměstnanec více účtů (část mzdy na jiný účet, exekuce)?
5. Kolik zaměstnanců (desítky, stovky, tisíce)? A kolik firem/IČO? Má jít o multi-tenant řešení?
6. Kde má aplikace běžet (vlastní server, cloud)?
7. Jaké heslo k PDF pro zaměstnance (datum narození, část RČ, vlastní)?
8. Kdo bude aplikaci používat a je potřeba schvalování?
