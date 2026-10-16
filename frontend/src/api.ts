export type Group = "salaries" | "deductions";

export interface EmployeeAccount {
  account: string;
  valid_from: string;
  valid_to: string | null;
}

export interface Employee {
  id: string;
  number: string;
  first_name: string;
  last_name: string;
  email: string;
  active: boolean;
  accounts: EmployeeAccount[];
}

export interface DueDateRule {
  day: number;
  month_offset: number;
  shift: "backward" | "forward";
}

export interface Settings {
  payer_account: string;
  deduction_accounts: { account: string; label: string }[];
  due_dates: Record<Group, DueDateRule>;
  account_change_warning_days: number;
  salary_change_warning_pct: number;
  payslip_number_pattern: string;
  email_sender: string;
  email_subject: string;
  email_body: string;
}

export interface OutputFile {
  filename: string;
  due_date: string;
  count: number;
  total: number;
  generated_at: string;
  uploaded_at: string | null;
  uploaded_by?: string | null;
}

export interface Batch {
  period: string;
  status: "new" | "imported" | "generated" | "uploaded";
  abo_filename?: string;
  imported_at?: string;
  imported_by?: string;
  overrides: Record<string, Group>;
  due_dates: Record<Group, string>;
  outputs: Partial<Record<Group, OutputFile>>;
  input_count?: number;
  input_total?: number;
}

export interface ClassifiedItem {
  index: number;
  payee: string;
  payer: string;
  amount: number;
  vs: string;
  ks: string;
  ss: string;
  message: string;
  group: Group | null;
  employee_id: string | null;
  label: string;
  overridden: boolean;
}

export interface Issue {
  severity: "error" | "warning";
  code: string;
  message: string;
  item: number | null;
  params: Record<string, string>;
}

export interface Classification {
  items: ClassifiedItem[];
  issues: Issue[];
  totals: Record<Group, { count: number; amount: number }>;
  input_total: number;
  has_errors: boolean;
}

export interface BatchDetail {
  batch: Batch;
  classification: Classification;
}

export interface Payslip {
  employee_id: string;
  status: "ready" | "queued" | "sent" | "failed";
  sent_at: string | null;
  sent_to: string | null;
  error: string | null;
}

export interface PayslipList {
  summary: { processed_at: string; unmatched: string[]; missing: string[] } | null;
  payslips: Payslip[];
}

export interface AuditEntry {
  time: string;
  user: string;
  action: string;
  detail: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "payroll",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") onUnauthorized();
    throw new ApiError(response.status, data.error ?? response.statusText);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown = {}) => request<T>("PUT", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

export async function fileToBase64(file: File): Promise<string> {
  const buffer = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < buffer.length; i += 0x8000) {
    binary += String.fromCharCode(...buffer.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}
