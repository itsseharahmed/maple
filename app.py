"""Streamlit UI. WHY: ops manager gets a 10-item queue; session_state keeps 'done' ticks across reruns."""
import streamlit as st, triage

st.set_page_config(page_title="Maple Freight - Tonight", layout="wide")
top, rest, dups, s = triage.run()
st.title("Maple Freight: act on these first")
st.caption(f"Data as of {s['as_of']:%a %b %d, %H:%M} ET | {s['raw']} raw alerts = {s['in_top']} in top 10 + {s['additional']} additional + {s['duplicates']} flagged duplicates"
           f" | reconciles: {'YES' if s['reconciles'] else 'NO - investigate'}")
bad = s["lag"][(s["lag"].pct_over_60min >= 5) | (s["lag"].max_min >= 240)]   # WHAT: >=5% late OR any alert >4h late (a burst outage is a small %)                           # WHAT: depots where >=5% of alerts arrive >1h late
if len(bad): st.warning(f"Delivery lag: {', '.join(bad.index)} - alerts there arrive late despite the vendor's 'normal connectivity'. Treat their recent alerts as older than they look.")
with st.expander("Alert delivery lag by depot"): st.dataframe(s["lag"])
if "done" not in st.session_state: st.session_state.done = set()
for i, r in top.iterrows():
    k = f"{r.vehicle}-{r.kind}-{i}"
    c1, c2 = st.columns([1, 12]); done = c1.checkbox("Done", key=k, value=k in st.session_state.done)
    (st.session_state.done.add if done else st.session_state.done.discard)(k)
    c2.markdown(f"{'~~' if done else ''}**{i+1}. {r.vehicle}** ({r.depot}) | **{r.urgency}** | {r.kind} | score {r.score}{'~~' if done else ''}  \n{r.why}  \n**Next:** {r.action}")
st.subheader("Additional alerts (not duplicates, not in the top 10)")
types = st.multiselect("Filter by alert type", sorted(rest.alert_type.unique()))
view = rest[rest.alert_type.isin(types)] if types else rest
st.dataframe(view[["urgency","priority", "status", "vehicle_id", "driver_id", "alert_type", "vendor_severity", "local", "value", "unit", "posted_limit_kmh", "dtc_code", "alert_id"]],
             use_container_width=True, hide_index=True)
with st.expander(f"Flagged duplicates ({len(dups)}): same vehicle, type, timestamp, value and DTC"):
    st.dataframe(dups[["alert_id", "vehicle_id", "alert_type", "local", "value", "dtc_code"]], hide_index=True)