import { ChangeEvent, useCallback, useEffect, useState } from "react";

import { api, Employee, PayslipList } from "../api";
import { formatDateTime, payslipStatusNames } from "../format";

export default function PayslipsSection({ period }: { period: string }) {
  const [list, setList] = useState<PayslipList | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [payslips, people] = await Promise.all([
      api.get<PayslipList>(`/batches/${period}/payslips`),
      api.get<Employee[]>("/employees"),
    ]);
    setList(payslips);
    setEmployees(people);
  }, [period]);

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  // Refresh while e-mails are being sent.
  useEffect(() => {
    if (!list?.payslips.some((p) => p.status === "queued")) return;
    const timer = setTimeout(() => load().catch(() => undefined), 3000);
    return () => clearTimeout(timer);
  }, [list, load]);

  const byId = new Map(employees.map((e) => [e.id, e]));
  const name = (id: string) => {
    const e = byId.get(id);
    return e ? `${e.last_name} ${e.first_name}` : id;
  };

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setInfo("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const isZip = file.name.toLowerCase().endsWith(".zip");
    const contentType = isZip ? "application/zip" : "application/pdf";
    run(async () => {
      const { url, key } = await api.post<{ url: string; key: string }>(`/batches/${period}/payslips/upload-url`, {
        content_type: contentType,
      });
      const response = await fetch(url, { method: "PUT", body: file, headers: { "Content-Type": contentType } });
      if (!response.ok) throw new Error("Nahrání souboru se nezdařilo.");
      setList(await api.post<PayslipList>(`/batches/${period}/payslips/process`, { key }));
      setSelected(new Set());
    });
  }

  async function preview(employeeId: string) {
    const win = window.open("", "_blank");
    try {
      const { url } = await api.get<{ url: string }>(`/batches/${period}/payslips/${employeeId}/preview`);
      if (win) win.location.href = url;
    } catch (e) {
      win?.close();
      setError((e as Error).message);
    }
  }

  function send(employeeIds: string[] | null) {
    const count = employeeIds?.length ?? list?.payslips.filter((p) => p.status === "ready" || p.status === "failed").length;
    if (!window.confirm(`Odeslat výplatní pásky e-mailem (${count})?`)) return;
    run(async () => {
      const result = await api.post<{ queued: string[]; skipped: { employee_id: string }[] }>(
        `/batches/${period}/payslips/send`,
        { employee_ids: employeeIds },
      );
      setInfo(
        `Zařazeno k odeslání: ${result.queued.length}.` +
          (result.skipped.length
            ? ` Bez e-mailu: ${result.skipped.map((s) => name(s.employee_id)).join(", ")}.`
            : ""),
      );
      setSelected(new Set());
      await load();
    });
  }

  function toggle(id: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setSelected(next);
  }

  const payslips = [...(list?.payslips ?? [])].sort((a, b) => name(a.employee_id).localeCompare(name(b.employee_id), "cs"));

  return (
    <section className="card">
      <h2>4. Výplatní pásky</h2>
      <p className="muted">
        Nahrajte hromadné PDF s páskami z Pohody (rozdělí se podle osobního čísla nebo jména), nebo ZIP s PDF soubory
        pojmenovanými osobním číslem (např. 1001_novak.pdf).
      </p>
      <label className="file">
        {list?.summary ? "Nahrát pásky znovu" : "Vybrat PDF nebo ZIP"}
        <input type="file" accept=".pdf,.zip" onChange={upload} disabled={busy} />
      </label>
      {error && <p className="error">{error}</p>}
      {info && <p className="ok">{info}</p>}

      {list?.summary && (
        <>
          <p>Zpracováno {formatDateTime(list.summary.processed_at)}.</p>
          {list.summary.unmatched.length > 0 && (
            <p className="warning">Nepřiřazené stránky / soubory: {list.summary.unmatched.join(", ")}</p>
          )}
          {list.summary.missing.length > 0 && (
            <p className="warning">Bez pásky: {list.summary.missing.map(name).join(", ")}</p>
          )}
        </>
      )}

      {payslips.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Zaměstnanec</th>
                <th>E-mail</th>
                <th>Stav</th>
                <th>Odesláno</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {payslips.map((p) => (
                <tr key={p.employee_id}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(p.employee_id)}
                      onChange={(e) => toggle(p.employee_id, e.target.checked)}
                    />
                  </td>
                  <td>{name(p.employee_id)}</td>
                  <td>{byId.get(p.employee_id)?.email || <span className="error">chybí</span>}</td>
                  <td>
                    <span className={`badge ${p.status}`}>{payslipStatusNames[p.status]}</span>
                    {p.error && <div className="error">{p.error}</div>}
                  </td>
                  <td>{p.sent_at ? `${formatDateTime(p.sent_at)} → ${p.sent_to}` : "–"}</td>
                  <td>
                    <button onClick={() => preview(p.employee_id)}>Náhled</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="actions">
            <button className="primary" onClick={() => send(null)} disabled={busy}>
              Odeslat všem neodeslaným
            </button>
            <button onClick={() => send([...selected])} disabled={busy || selected.size === 0}>
              Odeslat vybraným ({selected.size})
            </button>
          </div>
        </>
      )}
    </section>
  );
}
