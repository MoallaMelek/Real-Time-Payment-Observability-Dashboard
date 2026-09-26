export function formatTnd(value: number, locale = "fr-TN"): string {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 3,
  }).format(value) + " TND";
}

export function formatAmount(value: number, locale = "fr-TN"): string {
  return formatTnd(value, locale);
}

export function formatNumber(value: number, locale = "fr-TN"): string {
  return new Intl.NumberFormat(locale).format(value);
}

export function formatTime(value: string, locale = "fr-TN"): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
