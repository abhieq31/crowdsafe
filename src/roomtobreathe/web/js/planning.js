// Offline planning logic: a mirror of src/roomtobreathe/planning.py.
// Runs in the browser with no server, so a temple committee or district
// officer can plan on a phone with no internet. Both implementations are
// checked against tests/vectors/planning.json.

export const FLOW_LEVEL = 82; // persons per metre width per minute (Green Guide)
export const FLOW_STAIRS = 66;

const fmt = (n) => n.toLocaleString("en-US");

export function exitFlowPps(e) {
  return Math.max(0, e.width_m) * (e.stairs ? FLOW_STAIRS : FLOW_LEVEL) / 60;
}

export function computeCapacity(usableArea, exits, planningDensity = 2.0, evacTargetS = 480) {
  const findings = [];
  const areaCap = Math.trunc(Math.max(0, usableArea) * planningDensity);
  const flows = exits.map(exitFlowPps).sort((a, b) => b - a);
  const totalFlow = flows.reduce((a, b) => a + b, 0);
  const egressCap = Math.trunc(totalFlow * evacTargetS);
  const reduced = flows.length > 1 ? flows.slice(1).reduce((a, b) => a + b, 0) : 0;
  const egressBlocked = Math.trunc(reduced * evacTargetS);

  if (exits.length === 0) findings.push("No exits defined: the venue cannot be evacuated.");
  else if (exits.length === 1)
    findings.push("Single exit: if it is blocked nobody can leave. Add at least one more " +
      "independent exit, on a different side of the venue.");

  const pick = (c) => c.reduce((m, x) => (x[0] < m[0] ? x : m));
  let [safe, limiting] = pick([[areaCap, "area"], [egressBlocked, "exits (largest blocked)"]]);
  if (exits.length === 1)
    [safe, limiting] = pick([[areaCap, "area"], [egressCap, "single exit (no redundancy)"]]);
  if (limiting.startsWith("exits") || limiting.startsWith("single"))
    findings.push(`Exits limit capacity to ${fmt(safe)} although the ground holds ${fmt(areaCap)}. ` +
      "Widening exits raises the safe capacity.");
  const evac = totalFlow > 0 ? Math.round((safe / totalFlow) * 10) / 10 : null;
  return {
    usable_area_m2: Math.round(usableArea * 100) / 100,
    planning_density: planningDensity,
    area_capacity: areaCap,
    egress_capacity: egressCap,
    egress_capacity_largest_blocked: egressBlocked,
    safe_capacity: safe,
    limiting_factor: limiting,
    evac_time_at_safe_capacity_s: evac,
    findings,
  };
}

const perThousand = (n, att) => (att > 0 ? (n * 1000) / att : Infinity);

export const PROFILE_DEFAULTS = {
  police: 0, marshals: 0, medical_posts: 0, ambulances: 0, exits: 1, night: false,
  weather: "clear", terrain: [], free_distribution: false, vip_or_celebrity: false,
  fixed_start_or_end: true, past_incidents_at_site: 0, elderly_or_children_share: 0,
  live_monitoring: false,
};

export function assessRisk(profile) {
  const p = { ...PROFILE_DEFAULTS, ...profile };
  const f = [];
  const add = (code, points, finding, recommendation) => f.push({ code, points, finding, recommendation });

  const ratio = p.safe_capacity > 0 ? p.expected_attendance / p.safe_capacity : Infinity;
  let blocker = false;
  if (ratio > 1.0) {
    blocker = true;
    add("OVER_CAPACITY", 40,
      `Expected attendance is ${ratio.toFixed(1)}x the safe capacity (${fmt(p.expected_attendance)} vs ${fmt(p.safe_capacity)}).`,
      "Reduce attendance with timed or ticketed entry, add a second venue or day, or enlarge the site and its exits. Do not approve at this size.");
  } else if (ratio > 0.85) {
    add("NEAR_CAPACITY", 15, `Expected attendance is ${Math.round(ratio * 100)}% of safe capacity.`,
      "Use entry counting with a hard stop at 100% and have overflow holding areas ready.");
  }
  const types = ["religious", "entertainment", "political", "sports", "other"];
  if (p.event_type === "religious")
    add("RELIGIOUS", 8, "Religious gatherings account for ~79% of stampede deaths in India.",
      "Plan for long dwell times, mixed ages and sacred focal points.");
  else if (!types.includes(p.event_type))
    add("UNKNOWN_TYPE", 3, `Unknown event type '${p.event_type}'.`, "Classify the event.");

  const police = perThousand(p.police, p.expected_attendance);
  if (police < 1.0) add("LOW_POLICE", 12, `Only ${police.toFixed(2)} police per 1,000 attendees.`,
    "Increase deployment, or reduce attendance to match available personnel.");
  const marshals = perThousand(p.marshals, p.expected_attendance);
  if (marshals < 2.0) add("LOW_MARSHALS", 8, `Only ${marshals.toFixed(2)} trained marshals/volunteers per 1,000 attendees.`,
    "Recruit and brief volunteers, with at least one at every choke point and gate.");
  if (p.medical_posts === 0) add("NO_MEDICAL", 8, "No medical post planned.",
    "Set up first-aid posts with triage tags and a casualty register.");
  if (p.ambulances === 0 && p.expected_attendance >= 5000)
    add("NO_AMBULANCE", 5, "No ambulance on standby for a large crowd.",
      "Stage ambulances with clear, protected access routes.");
  if (p.exits <= 1) add("SINGLE_EXIT", 15, "Only one exit.",
    "Add independent exits on different sides, and keep them unlocked and staffed.");
  if (p.free_distribution) add("FREE_DISTRIBUTION", 12, "Free items (prasad, food, gifts) cause rushes and sudden surges.",
    "Distribute at several points, in queues, away from the main flow and away from exits.");
  if (p.vip_or_celebrity) add("VIP", 8, "VIP or celebrity arrival concentrates the crowd.",
    "Announce the schedule in advance, use barriers, and never change the route at short notice.");
  if (p.fixed_start_or_end) add("SURGE_TIMES", 5, "Everyone arrives or leaves at the same time.",
    "Stagger entry and exit, open all gates before the end, and hold back flow at entrances.");
  if (p.night) add("NIGHT", 4, "Night-time event.", "Provide lighting with backup power at all paths and exits.");
  if (p.weather === "rain" || p.weather === "storm")
    add("RAIN", 8, `Weather: ${p.weather}. Slips on mud and people crowding for shelter.`,
      "Lay matting or gravel, and plan for sheltering and slower walking speeds.");
  else if (p.weather === "heat") add("HEAT", 5, "Heat stress lowers tolerance to crowding.", "Provide water points and shade.");
  for (const t of p.terrain)
    if (["mud", "slope", "riverbank", "steps", "dead_end"].includes(t))
      add(`TERRAIN_${t.toUpperCase()}`, 5, `Hazardous terrain: ${t}.`,
        "Fix the ground, or remove that area from the usable area.");
  if (p.past_incidents_at_site > 0)
    add("HISTORY", Math.min(20, 10 * p.past_incidents_at_site),
      `${p.past_incidents_at_site} previous crowd incident(s) at this site.`,
      "Repeat incidents happen at the same sites. Review the earlier after-action reports.");
  if (p.elderly_or_children_share >= 0.3) add("VULNERABLE", 5, "Large share of elderly people or children.",
    "Use lower planning densities and give vulnerable groups separate queues.");
  if (!p.live_monitoring && p.expected_attendance >= 10000)
    add("NO_MONITORING", 6, "No live density monitoring for a large crowd.",
      "Deploy Room to Breathe live monitoring (Tier 1+) or at least gate counting (Tier 0).");

  const score = Math.min(100, f.reduce((s, x) => s + x.points, 0));
  const tier = score >= 60 ? "SEVERE" : score >= 40 ? "HIGH" : score >= 20 ? "MODERATE" : "LOW";
  const approve = blocker ? "do_not_approve"
    : (tier === "HIGH" || tier === "SEVERE") ? "approve_with_conditions" : "approve";
  f.sort((a, b) => b.points - a.points);
  return {
    score, tier, approve,
    occupancy_ratio: Number.isFinite(ratio) ? Math.round(ratio * 1000) / 1000 : -1,
    factors: f,
  };
}
