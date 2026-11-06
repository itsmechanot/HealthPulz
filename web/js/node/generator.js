// Synthetic case generator for one barangay (rules: docs/synthetic-data.md).
// Uses the same diseases.json the RHU serves at /api/config.

import { uuid4 } from "../common/ids.js";

const OUTBREAK_MULTIPLIER = 5;
const PUROKS = 7;

/**
 * A realistic week of cases for this barangay, optionally with an outbreak of one disease.
 *
 * outbreak: { code, minimumCases }. The outbreak disease gets ×5 its normal rate but
 * at least `minimumCases`, so it goes over the RHU's threshold in any season (×5 of a
 * low off-season rate can still be below it).
 */
export function realisticWeek(diseases, barangay, outbreak = null) {
  const month = new Date().getMonth();
  const records = [];
  for (const disease of diseases) {
    const lambda = disease.base_rate * (barangay.population / 10_000) * disease.season[month] * uniform(0.8, 1.2);
    if (disease.disease_code === outbreak?.code) {
      const count = Math.max(poisson(lambda * OUTBREAK_MULTIPLIER), outbreak.minimumCases);
      for (let i = 0; i < count; i++) records.push(makeRecord(disease));
    } else {
      const count = poisson(lambda);
      for (let i = 0; i < count; i++) records.push(makeRecord(disease));
    }
  }
  return records;
}

/** Exactly `count` cases, diseases picked in proportion to their base rate. For the burst test. */
export function stressRecords(diseases, count) {
  return Array.from({ length: count }, () => makeRecord(pickWeighted(diseases, (d) => d.base_rate)));
}

/** The illness may have started up to 6 days ago, but the checkup is always this week, because
 *  a case counts in the week of its checkup and sent data should show in this week's numbers. */
function makeRecord(disease) {
  const today = new Date();
  const monday = addDays(today, -((today.getDay() + 6) % 7));
  const onset = addDays(today, -randInt(0, 6));
  const consult = latest(addDays(onset, randInt(0, 3)), monday);
  const ageBand = pickWeighted(disease.age_profile, (band) => band.weight);
  return {
    record_id: uuid4(),
    disease_code: disease.disease_code,
    onset_date: localDate(onset),
    consult_date: localDate(consult > today ? today : consult),
    age_years: randInt(ageBand.min, ageBand.max),
    sex: Math.random() < disease.male_ratio ? "M" : "F",
    purok: randInt(1, PUROKS),
    case_status: Math.random() < 0.7 ? "SUSPECTED" : "CONFIRMED",
  };
}

const latest = (a, b) => (a > b ? a : b);

// ---- random helpers ------------------------------------------------------

function uniform(min, max) {
  return min + Math.random() * (max - min);
}

function randInt(min, max) {
  return min + Math.floor(Math.random() * (max - min + 1));
}

function pickWeighted(items, weightOf) {
  const total = items.reduce((sum, item) => sum + weightOf(item), 0);
  let roll = Math.random() * total;
  for (const item of items) {
    roll -= weightOf(item);
    if (roll < 0) return item;
  }
  return items[items.length - 1];
}

/** Poisson sample (Knuth's method). Fine for the small weekly rates used here. */
function poisson(lambda) {
  const limit = Math.exp(-lambda);
  let k = 0;
  let p = 1;
  do {
    k++;
    p *= Math.random();
  } while (p > limit);
  return k - 1;
}

// ---- dates ---------------------------------------------------------------

function addDays(date, days) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

/** YYYY-MM-DD in the device's local time (toISOString would use UTC and can be off by a day). */
function localDate(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
