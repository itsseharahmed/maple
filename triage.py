"""Alert triage: ~hundreds of alerts/day -> <=10 ranked, explained actions.
WHY rules not ML: 14 days with no ground-truth labels, and the ops team must be able to audit every rank."""
import os, pandas as pd, numpy as np
TZ = "America/Toronto"                       # Maple Freight works in Eastern; 23:00 window is LOCAL
AS_OF = pd.Timestamp("2026-09-21 18:00", tz=TZ)
URG = {"Critical": 100, "High": 70, "Medium": 30, "Low": 10}

def load(d="data"):
    """WHY: single entry point so real CSVs drop in over the sample ones."""
    r = lambda f: pd.read_csv(f"{d}/{f}.csv")
    a = r("alerts")
    bf = f"{d}/hamilton_backfill.csv"                                    # WHAT: alerts Hamilton held until 18:47 ET
    if os.path.exists(bf): a = pd.concat([a, pd.read_csv(bf)], ignore_index=True)   # WHY: export predates them; content-key dedupe catches overlap
    return a, r("vehicles"), r("dtc_reference"), r("work_orders")

def clean(a):
    """WHY: FLAG, never delete. Every raw alert must end up visible somewhere so nothing useful is lost."""
    a = a.copy()
    for c in ("event_time_utc", "received_time_utc"): a[c] = pd.to_datetime(a[c], utc=True, format="ISO8601")  # WHAT: parse as UTC; ISO8601 tolerates mixed "T...Z" / space formats across files  # WHAT: parse as UTC
    a["local"] = a.event_time_utc.dt.tz_convert(TZ)                      # WHAT: handles EDT offset
    # Vendor claims exactly-once; verify. Strict key (same vehicle, type, exact timestamp, value, DTC) so
    # repeated alert types with different values/times are NOT duplicates. Flagged only, shown in the UI.
    a["is_dup"] = a.duplicated(["vehicle_id", "alert_type", "event_time_utc", "value", "dtc_code"], keep="first")
    a["status"] = ""
    a.loc[a.alert_type.eq("maintenance_due") & (a.value <= 0), "status"] = "not yet due (value <= 0)"
    # ONLY inside the vendor's window, in LOCAL time: outside it these alerts are real signal.
    a.loc[a.alert_type.isin(["device_unplugged", "camera_offline"]) & (a.local.dt.hour == 23), "status"] = "nightly OTA window (expected)"
    return a

def score_alerts(a, dtc):
    """WHY: every non-duplicate alert gets its own priority so the additional table is ordered, not arbitrary.
    Engine faults use the DTC REFERENCE urgency (not vendor_severity). Suppressed alerts score 0 (status says why)."""
    urg = dtc.assign(u=dtc.reference_urgency.str.split(" - ").str[0]).set_index("dtc_code").u
    a = a.copy(); t = a.alert_type
    a["dtc_urgency"] = a.dtc_code.map(urg)                               # WHAT: NaN for non-faults
    over = (a.value - a.posted_limit_kmh.fillna(100))                               # WHAT: km/h over limit; NaN if limit unknown
    conds = [t.eq("engine_fault"), t.str.startswith("harsh"), t.eq("speeding") & over.notna(), t.eq("speeding") & (a.posted_limit_kmh.notna() | (a.value > 100)),
             t.eq("idling"), t.isin(["device_unplugged", "camera_offline"]), t.eq("maintenance_due")]
    choices = [a.dtc_urgency.map(URG).fillna(30).values,                 # unknown DTC -> 30 (surface it, don't bury it)
               (5 + (a.value * 10).clip(upper=10)).values,               # harsher g-force -> higher
               (5 + over.clip(0, 25)).values,                            # further over a KNOWN limit -> higher
               np.full(len(a), 8.0),                                     # speeding, limit unknown: can't judge severity
               (3 + (a.value / 10).clip(upper=7)).values,                # longer idle -> higher
               np.full(len(a), 15.0),
               (5 + (a.value / 100).clip(0, 20)).values]                 # more km overdue -> higher
    s = pd.Series(np.select(conds, choices, default=0.0), index=a.index)
    s[a.status != ""] = 0                                                # expected noise / not due
    a["priority"] = s.round().where(~a.is_dup)                           # duplicates get no score
    return a

def build_issues(a, veh, dtc, wo):
    """WHY: one vehicle/driver problem = one action item, not 50 alerts."""
    a = a[~a.is_dup & a.status.eq("")]                                     # WHAT: only live, non-dup alerts form issues
    out = []; vi = veh.set_index("vehicle_id")
    dtc = dtc.assign(urg=dtc.reference_urgency.str.split(" - ").str[0]).set_index("dtc_code")
    wo = wo.assign(closed=pd.to_datetime(wo.closed_local).dt.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT"))
    def add(v, kind, score, why, action, ids):
        out.append(dict(vehicle=v, driver=vi.assigned_driver.get(v), depot=vi.depot.get(v), kind=kind, score=round(score), why=why, action=action, alert_ids=list(ids)))

    # 1) Engine faults: rank by DTC reference urgency, NOT vendor_severity (vendor label can disagree).
    ef = a[a.alert_type == "engine_fault"]
    nser = ef[ef.dtc_code.map(dtc.urg).isin(["Critical", "High"])].groupby("vehicle_id").dtc_code.nunique()   # WHAT: distinct serious DTCs per vehicle
    for (v, code), g in ef.groupby(["vehicle_id", "dtc_code"]):
        urg = dtc.urg.get(code, "Low"); sc = URG[urg] + min(len(g), 5) * 3
        last = g.local.max(); why = f"{code} ({dtc.meaning.get(code)}) x{len(g)}, last {last:%b %d %H:%M}. Reference urgency {urg}."
        vs = set(g.vendor_severity.str.lower())
        if urg in ("Critical", "High") and "high" not in vs: why += f" Vendor labelled it {'/'.join(sorted(vs)).upper()} - mislabeled."
        if nser.get(v, 0) >= 2 and urg in ("Critical", "High"): sc += 20; why += f" Vehicle has {nser[v]} distinct Critical/High faults."   # WHAT: compounding failures
        lr = g.sort_values("event_time_utc").iloc[-1]                    # WHAT: most recent reading as evidence
        if pd.notna(lr.value): why += f" Last reading {lr.value:g} {lr.unit}."
        late = (g.received_time_utc - g.event_time_utc).max().total_seconds() / 3600
        if late > 1: why += f" Delivered {late:.1f} h after the event (late gateway) - may still be on the road."
        w = wo[(wo.vehicle_id == v) & wo.closed.notna()]
        if len(w):
            wc = w.closed.max()
            if last > wc: sc += 25; why += f" RECURRED after {w.sort_values('closed').wo_id.iloc[-1]} closed {wc:%b %d %H:%M} - repair did not fix it."
            else: sc *= .4; why += " Last fault predates a closed work order - likely fixed; confirm."
        add(v, "engine_fault", sc, why, "Pull from route and inspect before dispatch" if urg in ("Critical", "High") else "Schedule at next service", g.alert_id)

    # 2) Drivers: harsh events per 1000 km, normalised against vehicles on the SAME firmware (4.2.1 over-detects).
    h = a[a.alert_type.str.startswith("harsh")].groupby("driver_id").size().rename("n")
    d = veh.groupby("assigned_driver").agg(km=("km_driven_14d", "sum"), fw=("device_firmware", "first"), v=("vehicle_id", "first")).join(h).fillna({"n": 0})
    d["rate"] = d.n / d.km * 1000; d["adj"] = d.rate / d.groupby("fw").rate.transform("median").replace(0, np.nan)
    d["raw_rank"] = d.rate.rank(ascending=False)
    for drv, r in d[(d.adj >= 2) & (d.n >= 8)].iterrows():
        sp = a[(a.driver_id == drv) & (a.alert_type == "speeding") & (a.value - a.posted_limit_kmh >= 15)]
        add(r.v, "driver", 40 + min(25, 10 * (r.adj - 2)) + min(len(sp), 5),
            f"{drv}: {int(r.n)} harsh events = {r.adj:.1f}x the median for firmware {r.fw} (raw rank #{int(r.raw_rank)}), {len(sp)} speeding >=15 km/h over known limits.",
            "Coaching conversation; review camera clips",
            list(a[(a.driver_id == drv) & a.alert_type.str.startswith("harsh")].alert_id) + list(sp.alert_id))
    # 3) Devices: only meaningful when outside the nightly window, and repeated.
    for (v, t), g in a[a.alert_type.isin(["device_unplugged", "camera_offline"])].groupby(["vehicle_id", "alert_type"]):
        if len(g) >= 3: add(v, "device", 40 + min(len(g) * 4, 25), f"{t} x{len(g)} OUTSIDE the nightly window (days: {g.local.dt.day.nunique()}). Possible tampering or hardware fault.", "Check device; ask driver", g.alert_id)
    # 4) Maintenance: only the latest positive overdue value per vehicle.
    m = a[a.alert_type == "maintenance_due"].sort_values("event_time_utc").groupby("vehicle_id").last()
    for v, r in m.iterrows(): add(v, "maintenance", 20 + min(r.value / 50, 30), f"{int(r.value)} km overdue for service.", "Book service slot", [r.alert_id])
    return pd.DataFrame(out).sort_values("score", ascending=False)

def lag_by_depot(a, veh):
    """WHY: vendor says connectivity is normal; delivery lag per depot tests that claim. Late alerts = stale dispatch decisions."""
    d = a.merge(veh[["vehicle_id", "depot"]], on="vehicle_id")           # WHAT: attach depot to each alert
    d["lag_min"] = (d.received_time_utc - d.event_time_utc).dt.total_seconds() / 60
    d["day"] = d.local.dt.date                                           # WHAT: Eastern calendar day of the event
    g = d.groupby("depot").lag_min
    out = pd.DataFrame({"alerts": g.size(), "median_min": g.median().round(1), "p95_min": g.quantile(.95).round(1),
                        "pct_over_60min": (d.lag_min.gt(60).groupby(d.depot).mean() * 100).round(1), "max_min": g.max().round(0)})
    late = d[d.lag_min.gt(60)]                                           # WHAT: >60 min = too old for same-shift action
    out["worst_day"] = late.groupby("depot").day.agg(lambda x: x.value_counts().idxmax())
    return out.fillna({"worst_day": "-"})

def run(d="data", n=10):
    """WHY: returns (top-N issues, all other non-duplicate alerts, duplicates, stats) so the UI can show every alert once."""
    raw, veh, dtc, wo = load(d); c = score_alerts(clean(raw), dtc)
    iss = build_issues(c, veh, dtc, wo).reset_index(drop=True)
    # Urgency band from score: gives the manager one scannable label next to the rank.
    iss["urgency"] = pd.cut(iss.score, [-1, 39, 69, 99, 1e9], labels=["Low", "Medium", "High", "Critical"])
    top = iss.head(n); top_ids = {i for ids in top.alert_ids for i in ids}
    owner = {}                                                           # alert -> highest-scoring issue it belongs to
    for r in iss.itertuples():
        for i in r.alert_ids: owner.setdefault(i, (r.score, r.kind))
    live = c[~c.is_dup]; rest = live[~live.alert_id.isin(top_ids)].copy()
    # Alerts inside a lower-ranked issue keep at least that issue's score; suppressed ones stay 0.
    rest["priority"] = [p if stt else max(p, owner.get(i, (0, ""))[0]) for p, stt, i in zip(rest.priority, rest.status, rest.alert_id)]
    rest["urgency"] = pd.cut(rest.priority, [-1, 39, 69, 99, 1e9], labels=["Low", "Medium", "High", "Critical"])   # WHY: every alert gets a label, same bands as the top 10
    # Vendor severity first (as requested), then our issue priority, then recency. Unknown labels sort last.
    rest["sev_rank"] = rest.vendor_severity.astype(str).str.lower().map({"high": 3, "medium": 2, "low": 1}).fillna(0)
    rest = rest.sort_values(["sev_rank", "priority", "local"], ascending=[False, False, False])
    st = dict(raw=len(raw), in_top=int(live.alert_id.isin(top_ids).sum()), additional=len(rest), duplicates=int(c.is_dup.sum()))
    st["as_of"] = c.received_time_utc.max().tz_convert(TZ)
    st["lag"] = lag_by_depot(c, veh)                                     # WHAT: delivery-lag table for the UI
    st["reconciles"] = st["in_top"] + st["additional"] + st["duplicates"] == st["raw"]   # nothing lost
    return top.reset_index(drop=True), rest, c[c.is_dup], st