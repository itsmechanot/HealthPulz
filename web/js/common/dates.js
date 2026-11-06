// Dates as people read them. The ranges for whole weeks ("Nov 2 – 8, 2026") come from the server.

/** The start of a week on a chart axis: "May 11", with the year if it isn't this year. */
export function weekStart(week) {
  const start = new Date(`${week.start}T00:00`);
  const options = { month: "short", day: "numeric" };
  if (start.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return start.toLocaleDateString(undefined, options);
}
