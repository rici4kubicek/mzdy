import { FormEvent, useEffect, useState } from "react";

import { api, Group, Settings } from "../api";
import { groupNames } from "../format";

export default function SettingsPage() {
  const [form, setForm] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<Settings>("/settings")
      .then(setForm)
      .catch((e) => setError(e.message));
  }, []);

  if (!form) return error ? <p className="error">{error}</p> : null;

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setSaved(false);
    setForm({ ...form, [key]: value });
  };

  const setRule = (group: Group, patch: Partial<Settings["due_dates"][Group]>) =>
    set("due_dates", { ...form.due_dates, [group]: { ...form.due_dates[group], ...patch } });

  const setKnown = (index: number, patch: Partial<Settings["deduction_accounts"][number]>) =>
    set(
      "deduction_accounts",
      form.deduction_accounts.map((k, i) => (i === index ? { ...k, ...patch } : k)),
    );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setBusy(true);
    setError("");
    try {
      const body = { ...form, deduction_accounts: form.deduction_accounts.filter((k) => k.account.trim()) };
      setForm(await api.put<Settings>("/settings", body));
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <h1>Nastavení</h1>

      <section className="card">
        <h2>Firemní účet</h2>
        <label>
          Účet plátce u ČSOB (bez kódu banky)
          <input
            className="mono"
            value={form.payer_account}
            placeholder="123456789"
            onChange={(e) => set("payer_account", e.target.value)}
          />
        </label>
        <p className="muted">Platby z jiného účtu v souboru z Pohody budou označeny jako chyba.</p>
      </section>

      <section className="card">
        <h2>Splatnost</h2>
        <table>
          <thead>
            <tr>
              <th>Skupina</th>
              <th>Den v měsíci</th>
              <th>Měsíc</th>
              <th>Připadne-li na víkend nebo svátek</th>
            </tr>
          </thead>
          <tbody>
            {(Object.keys(groupNames) as Group[]).map((g) => (
              <tr key={g}>
                <td>{groupNames[g]}</td>
                <td>
                  <input
                    type="number"
                    min={1}
                    max={31}
                    value={form.due_dates[g].day}
                    onChange={(e) => setRule(g, { day: Number(e.target.value) })}
                  />
                </td>
                <td>
                  <select
                    value={form.due_dates[g].month_offset}
                    onChange={(e) => setRule(g, { month_offset: Number(e.target.value) })}
                  >
                    <option value={0}>stejný jako období</option>
                    <option value={1}>následující</option>
                    <option value={2}>o dva později</option>
                  </select>
                </td>
                <td>
                  <select
                    value={form.due_dates[g].shift}
                    onChange={(e) => setRule(g, { shift: e.target.value as "backward" | "forward" })}
                  >
                    <option value="backward">posunout na předchozí pracovní den</option>
                    <option value="forward">posunout na následující pracovní den</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Účty pro odvody</h2>
        <p className="muted">
          Platby na tyto účty (ČSSZ, zdravotní pojišťovny, finanční úřad, exekutoři…) se automaticky zařadí do odvodů.
          Platby zaměstnancům se poznají podle jejich účtů. Cokoli jiného je nutné zařadit ručně.
        </p>
        <table>
          <thead>
            <tr>
              <th>Číslo účtu</th>
              <th>Popis</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {form.deduction_accounts.map((k, i) => (
              <tr key={i}>
                <td>
                  <input
                    className="mono"
                    value={k.account}
                    placeholder="předčíslí-číslo/banka"
                    onChange={(e) => setKnown(i, { account: e.target.value })}
                  />
                </td>
                <td>
                  <input value={k.label} placeholder="např. VZP" onChange={(e) => setKnown(i, { label: e.target.value })} />
                </td>
                <td>
                  <button
                    type="button"
                    onClick={() =>
                      set(
                        "deduction_accounts",
                        form.deduction_accounts.filter((_, j) => j !== i),
                      )
                    }
                  >
                    Odebrat
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button
          type="button"
          onClick={() => set("deduction_accounts", [...form.deduction_accounts, { account: "", label: "" }])}
        >
          Přidat účet
        </button>
      </section>

      <section className="card">
        <h2>Upozornění</h2>
        <div className="grid">
          <label>
            Upozornit na změnu účtu zaměstnance mladší než (dní)
            <input
              type="number"
              min={0}
              value={form.account_change_warning_days}
              onChange={(e) => set("account_change_warning_days", Number(e.target.value))}
            />
          </label>
          <label>
            Upozornit na změnu mzdy oproti minulému měsíci větší než (%)
            <input
              type="number"
              min={0}
              value={form.salary_change_warning_pct}
              onChange={(e) => set("salary_change_warning_pct", Number(e.target.value))}
            />
          </label>
        </div>
      </section>

      <section className="card">
        <h2>Výplatní pásky</h2>
        <div className="grid">
          <label>
            Odesílatel (ověřená adresa v Amazon SES)
            <input
              type="email"
              value={form.email_sender}
              placeholder="mzdy@firma.cz"
              onChange={(e) => set("email_sender", e.target.value)}
            />
          </label>
          <label>
            Předmět
            <input value={form.email_subject} onChange={(e) => set("email_subject", e.target.value)} />
          </label>
        </div>
        <label>
          Text e-mailu
          <textarea rows={7} value={form.email_body} onChange={(e) => set("email_body", e.target.value)} />
        </label>
        <p className="muted">Proměnné: {"{name}"} jméno a příjmení, {"{first_name}"} jméno, {"{period}"} období (např. 10/2026).</p>
        <label>
          Vzor pro nalezení osobního čísla v PDF (regulární výraz nad textem bez diakritiky, malými písmeny)
          <input
            className="mono"
            value={form.payslip_number_pattern}
            onChange={(e) => set("payslip_number_pattern", e.target.value)}
          />
        </label>
      </section>

      {error && <p className="error">{error}</p>}
      {saved && <p className="ok">Uloženo.</p>}
      <button type="submit" className="primary" disabled={busy}>
        Uložit nastavení
      </button>
    </form>
  );
}
