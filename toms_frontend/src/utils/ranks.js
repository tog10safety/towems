/**
 * Rank seniority, highest first.
 *
 * Ranks are free text on a personnel record, so a picker cannot rely on the
 * stored spelling ("COL", "Col.", "COLONEL") nor on alphabetical order, which
 * would put a Sergeant above a Colonel. Each group below is one seniority step;
 * every accepted spelling of that step belongs in its group.
 */
const RANK_GROUPS = [
  ["LTGEN", "LT GEN", "LIEUTENANT GENERAL"],
  ["MGEN", "MG GEN", "MAJOR GENERAL"],
  ["BGEN", "BG GEN", "BRIGADIER GENERAL"],
  ["COL", "COLONEL"],
  ["LTC", "LT COL", "LIEUTENANT COLONEL"],
  ["MAJ", "MAJOR"],
  ["CPT", "CAPTAIN"],
  ["1LT", "1 LT", "FIRST LIEUTENANT"],
  ["2LT", "2 LT", "SECOND LIEUTENANT"],
  ["CMSG", "CMSGT", "CHIEF MASTER SERGEANT"],
  ["SMS", "SMSGT", "SENIOR MASTER SERGEANT"],
  ["MSG", "MSGT", "MASTER SERGEANT"],
  ["TSG", "TSGT", "TECHNICAL SERGEANT"],
  ["SSG", "SSGT", "STAFF SERGEANT"],
  ["SGT", "SERGEANT"],
  ["A1C", "AW1C", "A1C OR AW1C", "AIRMAN FIRST CLASS"],
  ["A2C", "AW2C", "A2C OR AW2C", "AIRMAN SECOND CLASS"],
  ["AM", "AW", "AM OR AW", "AIRMAN"],
];

function normalise(rank) {
  return String(rank ?? "")
    .toUpperCase()
    .replace(/\./g, "")
    .replace(/\s+/g, " ")
    .trim();
}

const RANK_WEIGHT = new Map();
RANK_GROUPS.forEach((aliases, index) => {
  for (const alias of aliases) RANK_WEIGHT.set(normalise(alias), index);
});

const UNRANKED = Number.MAX_SAFE_INTEGER;

/** Seniority index: 0 is the highest rank, UNRANKED sorts last. */
export function rankWeight(rank) {
  return RANK_WEIGHT.get(normalise(rank)) ?? UNRANKED;
}

/**
 * Sort comparator for personnel rows: highest rank first, then by name so two
 * people of the same rank keep a predictable order. A missing or unrecognised
 * rank goes to the end rather than to the top.
 */
export function byRank(left, right) {
  const difference = rankWeight(left?.rank) - rankWeight(right?.rank);
  if (difference !== 0) return difference;
  return String(left?.full_name ?? "").localeCompare(String(right?.full_name ?? ""));
}
