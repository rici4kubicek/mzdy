import type { Group, Issue } from "./api";

const czk = new Intl.NumberFormat("cs-CZ", { style: "currency", currency: "CZK" });

/** Amounts are stored in hellers (1/100 CZK). */
export function money(hellers: number | undefined): string {
  return hellers === undefined ? "–" : czk.format(hellers / 100);
}

export function formatDate(iso: string | null | undefined, weekday = true): string {
  if (!iso) return "–";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString("cs-CZ", {
    weekday: weekday ? "short" : undefined,
    day: "numeric",
    month: "numeric",
    year: "numeric",
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("cs-CZ");
}

export function formatPeriod(period: string): string {
  const [year, month] = period.split("-");
  return `${Number(month)}/${year}`;
}

export const groupNames: Record<Group, string> = {
  salaries: "Mzdy",
  deductions: "Odvody",
  pension: "Důchodové připojištění",
};

export const batchStatusNames: Record<string, string> = {
  new: "Nové",
  imported: "Načteno",
  generated: "Soubory vygenerovány",
  uploaded: "Nahráno do banky",
};

export const payslipStatusNames: Record<string, string> = {
  ready: "Připraveno",
  queued: "Odesílá se",
  sent: "Odesláno",
  failed: "Chyba",
};

const issueTexts: Record<string, (p: Record<string, string>) => string> = {
  group_total_mismatch: (p) =>
    `Součet položek (${money(Number(p.actual))}) neodpovídá součtu v hlavičce ABO (${money(Number(p.expected))}).`,
  invalid_account: (p) => `Neplatné číslo účtu příjemce ${p.account}.`,
  unknown_bank: (p) => `Neznámý kód banky ${p.code}.`,
  payer_mismatch: (p) => `Účet plátce ${p.account} neodpovídá firemnímu účtu ${p.expected}.`,
  recent_account_change: (p) => `${p.name} má účet změněný od ${formatDate(p.date, false)}.`,
  expired_account: (p) => `Účet ${p.account} už u zaměstnance ${p.name} neplatí.`,
  salary_to_unknown_account: (p) =>
    `Účet ${p.account} nepatří žádnému zaměstnanci. Nejprve ho přidejte k zaměstnanci.`,
  pension_vs_not_found: (p) =>
    `Platba na účet penzijního připojištění ${p.account} neobsahuje žádný z nastavených variabilních symbolů.`,
  unassigned: (p) => `Platba na neznámý účet ${p.account}. Zařaďte ji do skupiny.`,
  missing_salary: (p) => `${p.name} nemá v souboru žádnou mzdu.`,
  salary_change: (p) =>
    `Mzda ${p.name} se změnila z ${money(Number(p.previous))} na ${money(Number(p.current))}.`,
};

export function issueText(issue: Issue): string {
  return issueTexts[issue.code]?.(issue.params) ?? issue.message;
}

export const auditActionNames: Record<string, string> = {
  "user.login": "Přihlášení",
  "user.locked": "Zablokování účtu po chybných pokusech",
  "user.create": "Vytvoření uživatele",
  "employee.create": "Nový zaměstnanec",
  "employee.create.accounts": "Nový zaměstnanec",
  "employee.update": "Úprava zaměstnance",
  "employee.update.accounts": "Změna bankovního účtu zaměstnance",
  "employee.delete": "Smazání zaměstnance",
  "settings.update": "Změna nastavení",
  "batch.import": "Import ABO souboru",
  "batch.overrides": "Ruční zařazení plateb",
  "batch.due_dates": "Změna data splatnosti",
  "batch.generate": "Vygenerování souborů pro banku",
  "batch.download": "Stažení souboru",
  "batch.mark_uploaded": "Označeno jako nahráno do banky",
  "batch.unmark_uploaded": "Zrušeno označení nahráno do banky",
  "payslips.process": "Zpracování výplatních pásek",
  "payslips.send": "Odeslání výplatních pásek",
  "payslip.sent": "Páska doručena do e-mailu",
};
