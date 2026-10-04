/** Semantic CSS colors let charts respond to the active theme. */
export const chartColors = Array.from(
  { length: 6 },
  (_, i) => `var(--chart-${i + 1})`,
);

/** A category keeps its color across sorting, filters, and dashboard sections. */
export function categoryColor(name: string): string {
  const hash = Array.from(name).reduce(
    (value, character) => (value * 31 + character.codePointAt(0)!) >>> 0,
    0,
  );
  return chartColors[hash % chartColors.length];
}
