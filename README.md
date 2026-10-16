# Maple Freight: Sehar Ahmed

## How to run it
    pip install -r requirements.txt      # pandas, numpy, streamlit
    streamlit run app.py                 # run from the repo root


## Tonight's top 3 for Maple Freight
The app shows V-108 as two rows (ranks 1 and 2). It's one vehicle and one action, so I count it as one item.

1. **Item:** V-108 (Hamilton Depot, driver D-08). Two Critical faults.
   **Why it matters:** Coolant over-temperature and low oil pressure are both "stop vehicle" faults. Together they risk engine failure, and the vehicle may still be on the road.
   **Evidence from the data:** P0217 at 107 C (Sep 21, 12:05 ET) and P0524 at 88 kPa (15:41 ET). The vendor labelled both MEDIUM, but its own notes say severity follows the DTC, and the DTC reference says Critical. Both reached us 6.7 h and 3.1 h late, in Hamilton's 18:47 backfill. The faults recurred after WO-5512 closed on Sep 9, so that repair did not fix it.
   **Next action (who, by when):** The dispatcher calls D-08 now to pull the vehicle off the route. The shop inspects it before the 06:00 dispatch.

2. **Item:** V-137 (North Yard). Oil pressure fault after "fixed this morning".
   **Why it matters:** The maintenance lead said V-137 was fixed this morning. The data says a Critical fault came back after the repair.
   **Evidence from the data:** WO-5540 closed Sep 21 at 07:40. P0524 (oil pressure too low, 96 kPa) fired at 14:32 the same day. The vendor labelled it MEDIUM, and the vehicle has 2 distinct Critical/High faults. The overheating may have been a bad sensor, but oil pressure is a different fault.
   **Next action (who, by when):** The maintenance lead reopens WO-5540 and inspects the vehicle before 06:00 dispatch. Keep it off the road until then.

3. **Item:** V-158 (North Yard). Most recent High fault (P0300 misfire).
   **Why it matters:** The reference urgency is "inspect within 24 h". This is the freshest of the P0300 faults, so it is the one most likely to be on the road right now.
   **Evidence from the data:** P0300 at 17:09 ET on Sep 21, delivered on time. The vendor severity may disagree with the reference urgency. [FILL IN: vendor label for this alert]
   **Next action (who, by when):** The maintenance lead inspects it before dispatch tomorrow, and no later than 17:09 tomorrow.

## What changed after the new information
Hamilton's gateway held 10 alerts and delivered them at 18:47 ET. Some were up to 6.7 h old. `load()` now merges `hamilton_backfill.csv` into the alerts. The content-key duplicate check catches any overlap, and reconciliation still balances (raw = top 10 + additional + duplicates).

What changed: V-108 now shows two Critical faults (P0217 and P0524). That triggers the compounding-failure bonus, and V-108 moved to the top of the list. The "Data as of" line now uses the latest received time (18:47) instead of the 18:00 export. Engine faults delivered more than 1 hour late now say so in the explanation. The app warns when a depot has more than 5% of alerts over 1 h late, or any alert over 4 h late. Without the backfill, tonight's biggest problem would have been invisible.

I did not downgrade the rest of Hamilton's alerts. They're late, not unreliable. The vendor's "normal connectivity" claim does not hold for Hamilton.

## What I built, and for whom
A Streamlit page for the operations manager. They open it tonight and again before the 06:00 dispatch. It turns the alerts into a ranked top-10 action queue. Each item has a reason, a suggested action, and a "Done" tick. Below it is a table of every other alert with a High/Medium/Low label, a filter by alert type, a delivery-lag table per depot, and the flagged duplicates. Nothing is deleted, and every alert is visible somewhere.

## Assumptions I made
- Timestamps are UTC. All display and the 23:00 window use Eastern time (America/Toronto).
- The "now" for the export is 18:00 ET; the app shows the latest received time instead.
- The vendor's nightly-update window is 23:00-23:59 local. Unplugged and offline alerts inside it are expected. Outside it they are real, and 3 or more of the same type on one vehicle becomes an issue.
- `maintenance_due` with value <= 0 means not yet due. Only the latest positive value per vehicle is an issue.
- Duplicates share the same vehicle, type, exact timestamp, value and DTC. Duplicates are flagged, not deleted.
- Engine faults are ranked by the DTC reference urgency, not the vendor severity.
- A work order closed after the last fault suggests it's likely fixed (score x0.4). A fault after the closure means the repair did not hold (+25).
- Speeding with no posted limit is treated as a 100 km/h limit. This is crude, so I flag it.
- Driver harsh-event rates are normalised per 1000 km against the median for the same firmware, since 4.2.1 over-detects.

## What I deliberately did NOT do (and why)
- No machine learning. There are no labels, and the ops team has to be able to audit every rank.
- No deleting alerts. Everything is flagged and shown.
- No trusting vendor severity for engine faults. It's mislabeled in our top items.
- No time decay on old faults. A P0300 from Sep 9 still ranks like a fresh one. I ran out of time, and I flag this as a known weakness.
- No hosting, login, or database.
- No session state - did not have time to implement it, changes are lost on reload. 

## How I checked it's correct
- The app shows a reconciliation line: raw alerts = top 10 + additional + duplicates. It must say YES.
- I checked the top items against the raw alert, work-order and DTC data. 


## AI use
- **Tools I used:** Claude
- **What AI did for me:** A lot of the coding, I was mainly just guiding and checking outputs
- **One thing AI got wrong or incomplete, and how I caught it:** the first version silently dropped alerts that never formed a top issue; I caught it by asking whether useful alerts were being lost, and it was changed to flag-and-show.
- **Link to / export of my AI session:** https://claude.ai/chat/4b7077db-da2c-447d-a3bf-9c6026e7c4bd

## If I had two more days
- Decay old faults, and show whether each vehicle has recent activity.
- Learn per-depot lag so late-delivered alerts are labelled "may still be on the road" automatically.
- Add unit tests for the dedupe, the 23:00 window, and the work-order logic.
- Let the manager assign an owner and a deadline to each item, and keep the "Done" ticks between sessions.
