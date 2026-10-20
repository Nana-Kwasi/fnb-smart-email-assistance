import type { EmailStatus } from "@/api/types";

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const shortDay = new Intl.DateTimeFormat(undefined, { weekday: "short" });

export function formatDateTime(value: string | null | undefined): string {
  return value ? dateTime.format(new Date(value)) : "—";
}

export function formatDay(isoDate: string): string {
  return shortDay.format(new Date(`${isoDate}T12:00:00`));
}

export function timeAgo(value: string | null | undefined): string {
  if (!value) return "never";
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return `${Math.round(seconds / 60)} min`;
}

export function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export const STATUS_LABEL: Record<EmailStatus, string> = {
  received: "Processing",
  queued: "Needs a person",
  auto_answered: "Answered automatically",
  replied: "Answered by a person",
  closed: "Closed",
  skipped: "Not a customer email",
};

export const STATUS_TONE: Record<EmailStatus, string> = {
  received: "badge-info",
  queued: "badge-warn",
  auto_answered: "badge-ok",
  replied: "badge-brand",
  closed: "",
  skipped: "",
};

export function waitingFor(receivedAt: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(receivedAt).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ${minutes % 60} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

export function isOverdue(receivedAt: string, hours: number): boolean {
  return Date.now() - new Date(receivedAt).getTime() > hours * 3600_000;
}

export function initials(name: string): string {
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase();
}

export function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

export const CATEGORY_LABEL: Record<string, string> = {
  general: "General enquiry",
  account_info: "Accounts and services",
  digital_banking: "Mobile and internet banking",
  cards: "Card services",
  payments: "Transfers and payments",
  fees_rates: "Fees, charges and rates",
  branches: "Branches and hours",
  complaint: "Complaint",
  missing_transaction: "Missing or delayed transaction",
  fraud: "Suspected fraud",
  account_security: "Account security",
  identity_verification: "Needs identity verification",
  staff_action: "Needs staff action",
  unclear: "Incomplete or unclear",
  spam: "Spam or out of scope",
  unclassified: "Not classified",
};

export function categoryLabel(code: string | null | undefined): string {
  return code ? (CATEGORY_LABEL[code] ?? code) : "Not classified";
}

export const LANGUAGE_LABEL: Record<string, string> = { en: "English", tw: "Twi", mixed: "English and Twi", other: "Other" };

export const ROLE_LABEL: Record<string, string> = {
  officer: "Officer",
  supervisor: "Supervisor",
  auditor: "Auditor",
  admin: "Admin",
};

/** A key for one send: repeating the same request never sends twice. */
export function requestId(): string {
  return (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "");
}
