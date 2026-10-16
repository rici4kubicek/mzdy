import { FormEvent, useEffect, useState } from "react";

import { api, Employee, EmployeeAccount } from "../api";
import { formatDate } from "../format";

const today = () => new Date().toISOString().slice(0, 10);

const empty = (): Employee => ({
  id: "",
  number: "",
  first_name: "",
  last_name: "",
  email: "",
  active: true,
  accounts: [{ account: "", valid_from: today(), valid_to: null }],
});

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    api
      .get<Employee[]>("/employees")
      .then(setEmployees)
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const current = (e: Employee) =>
    e.accounts.filter((a) => a.valid_from <= today() && (!a.valid_to || a.valid_to >= today()));

  return (
    <>
      <h1>Zaměstnanci</h1>
      {error && <p className="error">{error}</p>}
      {editing ? (
        <EmployeeForm
          employee={editing}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      ) : (
        <>
          <button className="primary" onClick={() => setEditing(empty())}>
            Přidat zaměstnance
          </button>
          <table>
            <thead>
              <tr>
                <th>Os. číslo</th>
                <th>Jméno</th>
                <th>E-mail</th>
                <th>Platný účet</th>
                <th>Stav</th>
              </tr>
            </thead>
            <tbody>
              {employees.map((e) => (
                <tr key={e.id} className="clickable" onClick={() => setEditing(e)}>
                  <td>{e.number}</td>
                  <td>
                    {e.last_name} {e.first_name}
                  </td>
                  <td>{e.email || <span className="muted">–</span>}</td>
                  <td className="mono">
                    {current(e)
                      .map((a) => a.account)
                      .join(", ") || <span className="error">žádný</span>}
                  </td>
                  <td>{e.active ? "aktivní" : <span className="muted">neaktivní</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

interface FormProps {
  employee: Employee;
  onCancel: () => void;
  onSaved: () => void;
}

function EmployeeForm({ employee, onCancel, onSaved }: FormProps) {
  const [form, setForm] = useState<Employee>(employee);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const isNew = !employee.id;

  const set = <K extends keyof Employee>(key: K, value: Employee[K]) => setForm({ ...form, [key]: value });

  const setAccount = (index: number, patch: Partial<EmployeeAccount>) =>
    set(
      "accounts",
      form.accounts.map((a, i) => (i === index ? { ...a, ...patch } : a)),
    );

  /** Changing an account keeps history: the old one ends yesterday, the new one starts today. */
  function replaceAccount() {
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    set("accounts", [
      ...form.accounts.map((a) => (a.valid_to ? a : { ...a, valid_to: yesterday })),
      { account: "", valid_from: today(), valid_to: null },
    ]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const body = { ...form, accounts: form.accounts.filter((a) => a.account.trim()) };
    try {
      if (isNew) await api.post("/employees", body);
      else await api.put(`/employees/${employee.id}`, body);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Opravdu smazat zaměstnance ${employee.first_name} ${employee.last_name}?`)) return;
    try {
      await api.delete(`/employees/${employee.id}`);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>{isNew ? "Nový zaměstnanec" : `${employee.first_name} ${employee.last_name}`}</h2>
      <div className="grid">
        <label>
          Osobní číslo
          <input value={form.number} onChange={(e) => set("number", e.target.value)} required />
        </label>
        <label>
          Jméno
          <input value={form.first_name} onChange={(e) => set("first_name", e.target.value)} />
        </label>
        <label>
          Příjmení
          <input value={form.last_name} onChange={(e) => set("last_name", e.target.value)} required />
        </label>
        <label>
          E-mail pro výplatní pásky
          <input type="email" value={form.email} onChange={(e) => set("email", e.target.value)} />
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={form.active} onChange={(e) => set("active", e.target.checked)} />
          Aktivní
        </label>
      </div>

      <h3>Bankovní účty</h3>
      <p className="muted">
        Mzda se smí poslat jen na účet, který je tu zapsaný a platný. Při změně účtu použijte „Změnit účet“, aby
        zůstala zachovaná historie.
      </p>
      <table>
        <thead>
          <tr>
            <th>Číslo účtu (předčíslí-číslo/banka)</th>
            <th>Platí od</th>
            <th>Platí do</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {form.accounts.map((a, i) => (
            <tr key={i}>
              <td>
                <input
                  className="mono"
                  value={a.account}
                  placeholder="19-2000145399/0800"
                  onChange={(e) => setAccount(i, { account: e.target.value })}
                />
              </td>
              <td>
                <input type="date" value={a.valid_from} onChange={(e) => setAccount(i, { valid_from: e.target.value })} />
              </td>
              <td>
                <input
                  type="date"
                  value={a.valid_to ?? ""}
                  onChange={(e) => setAccount(i, { valid_to: e.target.value || null })}
                />
                {a.valid_to && <span className="muted"> {formatDate(a.valid_to, false)}</span>}
              </td>
              <td>
                <button type="button" onClick={() => set("accounts", form.accounts.filter((_, j) => j !== i))}>
                  Odebrat
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="actions">
        <button type="button" onClick={replaceAccount}>
          Změnit účet
        </button>
        <button
          type="button"
          onClick={() => set("accounts", [...form.accounts, { account: "", valid_from: today(), valid_to: null }])}
        >
          Přidat další účet
        </button>
      </div>

      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy}>
          Uložit
        </button>
        <button type="button" onClick={onCancel}>
          Zpět
        </button>
        {!isNew && (
          <button type="button" className="danger" onClick={remove}>
            Smazat
          </button>
        )}
      </div>
    </form>
  );
}
