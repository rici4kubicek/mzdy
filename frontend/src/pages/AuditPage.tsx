import { useEffect, useState } from "react";

import { api, AuditEntry } from "../api";
import { auditActionNames, formatDateTime } from "../format";

export default function AuditPage() {
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    api
      .get<AuditEntry[]>(`/audit?month=${month}`)
      .then(setEntries)
      .catch((e) => setError(e.message));
  }, [month]);

  return (
    <>
      <h1>Historie změn</h1>
      <label>
        Měsíc <input type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Čas</th>
            <th>Uživatel</th>
            <th>Akce</th>
            <th>Podrobnosti</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i} className={e.action.endsWith(".accounts") ? "row-warning" : ""}>
              <td>{formatDateTime(e.time)}</td>
              <td>{e.user}</td>
              <td>{auditActionNames[e.action] ?? e.action}</td>
              <td>
                <code className="detail">{JSON.stringify(e.detail, null, 1)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {entries.length === 0 && !error && <p className="muted">Žádné záznamy.</p>}
    </>
  );
}
