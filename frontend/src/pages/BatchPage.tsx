import { ChangeEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, Batch, BatchDetail, ClassifiedItem, fileToBase64, Group } from "../api";
import { formatDate, formatDateTime, formatPeriod, groupNames, issueText, money } from "../format";
import PayslipsSection from "./PayslipsSection";

const GROUPS: Group[] = ["salaries", "deductions"];

export default function BatchPage() {
  const { period = "" } = useParams();
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setDetail(await api.get<BatchDetail>(`/batches/${period}`));
      setMissing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setError((e as Error).message);
    }
  }, [period]);

  useEffect(() => {
    load();
  }, [load]);

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      return await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function uploadAbo(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const result = await run(async () =>
      api.put<BatchDetail>(`/batches/${period}/abo`, {
        filename: file.name,
        content_base64: await fileToBase64(file),
      }),
    );
    if (result) {
      setDetail(result);
      setMissing(false);
    }
  }

  async function setOverride(item: ClassifiedItem, group: string) {
    if (!detail) return;
    const overrides: Record<string, string> = { ...detail.batch.overrides };
    if (group) overrides[item.index] = group;
    else delete overrides[item.index];
    const result = await run(() => api.put<BatchDetail>(`/batches/${period}/overrides`, { overrides }));
    if (result) setDetail(result);
  }

  async function setDueDate(group: Group, value: string) {
    const result = await run(() =>
      api.put<BatchDetail>(`/batches/${period}/due-dates`, { due_dates: { [group]: value } }),
    );
    if (result) setDetail(result);
  }

  async function generate() {
    const batch = await run(() => api.post<Batch>(`/batches/${period}/outputs`));
    if (batch && detail) setDetail({ ...detail, batch });
  }

  async function download(group: Group) {
    const result = await run(() => api.get<{ url: string }>(`/batches/${period}/outputs/${group}/download`));
    if (result) window.location.href = result.url;
  }

  async function markUploaded(group: Group, uploaded: boolean) {
    const batch = await run(() => api.post<Batch>(`/batches/${period}/outputs/${group}/uploaded`, { uploaded }));
    if (batch && detail) setDetail({ ...detail, batch });
  }

  const locked = Object.values(detail?.batch.outputs ?? {}).some((o) => o?.uploaded_at);

  const importCard = (
    <section className="card">
      <h2>1. Soubor ABO z Pohody</h2>
      {detail?.batch.abo_filename && (
        <p>
          Načten soubor <strong>{detail.batch.abo_filename}</strong> ({formatDateTime(detail.batch.imported_at)},{" "}
          {detail.batch.imported_by}): {detail.batch.input_count} plateb, celkem {money(detail.batch.input_total)}.
        </p>
      )}
      {locked ? (
        <p className="muted">Soubory už jsou označené jako nahrané do banky, období nelze měnit.</p>
      ) : (
        <label className="file">
          {detail ? "Nahrát jiný soubor" : "Vybrat soubor ABO"}
          <input type="file" accept=".abo,.kpc,.txt,text/plain" onChange={uploadAbo} disabled={busy} />
        </label>
      )}
    </section>
  );

  return (
    <>
      <p>
        <Link to="/">← Období</Link>
      </p>
      <h1>Období {formatPeriod(period)}</h1>
      {error && <p className="error">{error}</p>}
      {missing && importCard}
      {detail && (
        <>
          {importCard}
          <Classification
            detail={detail}
            locked={locked || busy}
            onOverride={setOverride}
            onDueDate={setDueDate}
          />
          <section className="card">
            <h2>3. Soubory pro ČSOB</h2>
            {Object.keys(detail.batch.outputs).length === 0 ? (
              <>
                <p className="muted">
                  Pro každou skupinu se vytvoří samostatný soubor ABO s vlastním datem splatnosti. Soubory pak
                  ručně nahrajte do ČSOB CEB / BusinessBanking.
                </p>
                <button
                  className="primary"
                  onClick={generate}
                  disabled={busy || detail.classification.has_errors}
                  title={detail.classification.has_errors ? "Nejprve vyřešte chyby" : undefined}
                >
                  Vygenerovat soubory
                </button>
              </>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Skupina</th>
                    <th>Soubor</th>
                    <th>Splatnost</th>
                    <th className="num">Plateb</th>
                    <th className="num">Celkem</th>
                    <th></th>
                    <th>Nahráno do banky</th>
                  </tr>
                </thead>
                <tbody>
                  {GROUPS.filter((g) => detail.batch.outputs[g]).map((g) => {
                    const o = detail.batch.outputs[g]!;
                    return (
                      <tr key={g}>
                        <td>{groupNames[g]}</td>
                        <td className="mono">{o.filename}</td>
                        <td>{formatDate(o.due_date)}</td>
                        <td className="num">{o.count}</td>
                        <td className="num">{money(o.total)}</td>
                        <td>
                          <button onClick={() => download(g)} disabled={busy}>
                            Stáhnout
                          </button>
                        </td>
                        <td>
                          <label className="checkbox">
                            <input
                              type="checkbox"
                              checked={!!o.uploaded_at}
                              disabled={busy}
                              onChange={(e) => markUploaded(g, e.target.checked)}
                            />
                            {o.uploaded_at ? `${formatDateTime(o.uploaded_at)} (${o.uploaded_by})` : "ne"}
                          </label>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>
          <PayslipsSection period={period} />
        </>
      )}
    </>
  );
}

interface ClassificationProps {
  detail: BatchDetail;
  locked: boolean;
  onOverride: (item: ClassifiedItem, group: string) => void;
  onDueDate: (group: Group, value: string) => void;
}

function Classification({ detail, locked, onOverride, onDueDate }: ClassificationProps) {
  const { classification: c, batch } = detail;
  const generalIssues = c.issues.filter((i) => i.item === null);
  const itemIssues = (index: number) => c.issues.filter((i) => i.item === index);
  const unassigned = c.items.filter((i) => i.group === null);
  const outputSum = GROUPS.reduce((sum, g) => sum + c.totals[g].amount, 0);

  const itemRow = (item: ClassifiedItem) => (
    <tr key={item.index} className={itemIssues(item.index).some((i) => i.severity === "error") ? "row-error" : ""}>
      <td>{item.label || <span className="muted">neznámý příjemce</span>}</td>
      <td className="mono">{item.payee}</td>
      <td className="mono">{item.vs}</td>
      <td className="mono">{item.ks}</td>
      <td>{item.message}</td>
      <td className="num">{money(item.amount)}</td>
      <td>
        <select
          value={batch.overrides[item.index] ?? ""}
          disabled={locked}
          onChange={(e) => onOverride(item, e.target.value)}
        >
          <option value="">automaticky</option>
          {GROUPS.map((g) => (
            <option key={g} value={g}>
              {groupNames[g]}
            </option>
          ))}
        </select>
        {itemIssues(item.index).map((issue, n) => (
          <div key={n} className={issue.severity}>
            {issueText(issue)}
          </div>
        ))}
      </td>
    </tr>
  );

  const head = (
    <thead>
      <tr>
        <th>Příjemce</th>
        <th>Účet</th>
        <th>VS</th>
        <th>KS</th>
        <th>Zpráva</th>
        <th className="num">Částka</th>
        <th>Zařazení</th>
      </tr>
    </thead>
  );

  return (
    <section className="card">
      <h2>2. Rozdělení a kontrola</h2>
      {generalIssues.length > 0 && (
        <ul className="issues">
          {generalIssues.map((issue, n) => (
            <li key={n} className={issue.severity}>
              {issueText(issue)}
            </li>
          ))}
        </ul>
      )}
      {c.has_errors ? (
        <p className="error">Období obsahuje chyby, které je nutné vyřešit před vygenerováním souborů.</p>
      ) : (
        <p className="ok">Kontroly bez chyb.</p>
      )}

      {unassigned.length > 0 && (
        <>
          <h3>Nezařazené platby</h3>
          <table>
            {head}
            <tbody>{unassigned.map(itemRow)}</tbody>
          </table>
        </>
      )}

      {GROUPS.map((g) => (
        <div key={g} className="group">
          <div className="group-head">
            <h3>{groupNames[g]}</h3>
            <label>
              Splatnost{" "}
              <input
                type="date"
                value={batch.due_dates[g]}
                disabled={locked}
                onChange={(e) => e.target.value && onDueDate(g, e.target.value)}
              />{" "}
              <span className="muted">{formatDate(batch.due_dates[g])}</span>
            </label>
            <span>
              {c.totals[g].count} plateb, <strong>{money(c.totals[g].amount)}</strong>
            </span>
          </div>
          {c.totals[g].count > 0 && (
            <table>
              {head}
              <tbody>{c.items.filter((i) => i.group === g).map(itemRow)}</tbody>
            </table>
          )}
        </div>
      ))}

      <p className={outputSum === c.input_total ? "ok" : "error"}>
        Kontrolní součet: vstup {money(c.input_total)}, rozděleno {money(outputSum)}
        {outputSum !== c.input_total && ` (rozdíl ${money(c.input_total - outputSum)} nezařazeno)`}
      </p>
    </section>
  );
}
