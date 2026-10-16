import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, Batch, Group } from "../api";
import { batchStatusNames, formatDate, formatPeriod, groupNames, money } from "../format";

function previousMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function DashboardPage() {
  const [batches, setBatches] = useState<Batch[] | null>(null);
  const [error, setError] = useState("");
  const [period, setPeriod] = useState(previousMonth());
  const navigate = useNavigate();

  useEffect(() => {
    api
      .get<Batch[]>("/batches")
      .then(setBatches)
      .catch((e) => setError(e.message));
  }, []);

  function open(event: FormEvent) {
    event.preventDefault();
    navigate(`/batches/${period}`);
  }

  return (
    <>
      <h1>Mzdová období</h1>
      <form className="card inline" onSubmit={open}>
        <label>
          Období
          <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} required />
        </label>
        <button type="submit" className="primary">
          Otevřít / zpracovat
        </button>
      </form>

      {error && <p className="error">{error}</p>}
      {batches && batches.length === 0 && <p className="muted">Zatím nebylo zpracováno žádné období.</p>}
      {batches && batches.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Období</th>
              <th>Stav</th>
              <th className="num">Platby</th>
              <th className="num">Celkem</th>
              <th>Soubory pro banku</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.period}>
                <td>
                  <Link to={`/batches/${b.period}`}>{formatPeriod(b.period)}</Link>
                </td>
                <td>
                  <span className={`badge ${b.status}`}>{batchStatusNames[b.status]}</span>
                </td>
                <td className="num">{b.input_count ?? "–"}</td>
                <td className="num">{money(b.input_total)}</td>
                <td>
                  {(Object.entries(b.outputs ?? {}) as [Group, NonNullable<Batch["outputs"][Group]>][]).map(
                    ([group, o]) => (
                      <div key={group}>
                        {groupNames[group]}: {formatDate(o.due_date)} {o.uploaded_at ? "✓ v bance" : ""}
                      </div>
                    ),
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
