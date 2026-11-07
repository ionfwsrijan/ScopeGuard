import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function money(value: number | string | null | undefined, currency = "USD") {
  const amount = Number(value ?? 0);
  if (!Number.isFinite(amount)) return String(value ?? "—");
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 2 }).format(amount);
}

export function shortHash(value?: string | null, length = 12) {
  return value ? `${value.slice(0, length)}…` : "—";
}

export function titleCase(value?: string | null) {
  return value ? value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) : "—";
}

export function dateLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}
