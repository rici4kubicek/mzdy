import { FormEvent, useState } from "react";

import { api, ApiError } from "../api";

interface Props {
  onLogin: (user: { username: string; role: string }) => void;
}

export default function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await api.post("/auth/login", { username, password }));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(
        status === 429
          ? "Účet je po opakovaných chybných pokusech dočasně zablokovaný. Zkuste to za 15 minut."
          : status === 401
            ? "Neplatné jméno nebo heslo."
            : "Přihlášení se nezdařilo.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <h1>Mzdy</h1>
        <label>
          Uživatelské jméno
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="username" />
        </label>
        <label>
          Heslo
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" className="primary" disabled={busy || !username || !password}>
          Přihlásit
        </button>
      </form>
    </div>
  );
}
