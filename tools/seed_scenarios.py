"""Adds realistic outbreak stories for the defense, on top of tools/seed_patients.py's everyday patients.

    python tools/seed_scenarios.py

November in Naval is rainy and typhoon season, so the stories are the outbreaks that season brings:

  1. Dengue builds over three weeks in Caraycaray (alert, then epidemic) and spreads to its neighbors.
  2. Diarrhea after heavy rains and flooding in the coastal barangays, mostly small children.
  3. Leptospirosis in farmers who waded in flooded rice fields.
  4. Everyday record keeping: a suspected dengue case confirmed by a lab test (a correction), and a
     consultation voided because it was entered under the wrong patient.

No urgent (single-case) disease is added on purpose: enter one live during the defense instead.
Everyone is fictional (Data Privacy Act); every case is dated in the last three weeks, never in the future.
Records go in like the BHU page saves them, so the RHU's pipeline, analytics and alerts handle them
as real reports. Fixed IDs: running this twice adds nothing.
"""

import argparse
import random
import sys
import uuid
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seed_patients import ATTENDING, MALE, FEMALE, SITIOS, SURNAMES, vitals  # noqa: E402

from rhu import config, db, rules  # noqa: E402
from rhu.bhu_store import BhuStores, RecordNotFound, station_id  # noqa: E402
from rhu.models import ClientFields, ConsultationFields  # noqa: E402

NAMESPACE = uuid.UUID("a3d0f6a2-1e0b-4c55-8f0e-2f6c1b9d7e44")

# How each disease presents. ages: (youngest, oldest, weight) bands; male: share of men.
DISEASES = {
    "DEN": {
        "ages": [(5, 14, 5), (15, 29, 4), (30, 60, 2)], "male": 0.5, "status": ["SUSPECTED", "CONFIRMED", "CONFIRMED"],
        "complaints": ["High fever for 3 days with headache and body aches", "Fever with pain behind the eyes",
                       "Fever for 2 days, rashes on the arms", "Fever, vomiting and abdominal pain"],
        "findings": ["Petechiae on forearms, positive tourniquet test", "Flushed skin, retro-orbital pain, no bleeding",
                     "Rashes on extremities; neighbors also have fever", "Abdominal tenderness: warning sign"],
        "treatment": ["Oral rehydration, paracetamol only (no ibuprofen); return if bleeding or severe abdominal pain",
                      "Increase fluids, paracetamol; daily follow-up for platelet count"],
        "refer": 0.25, "follow_up": 1,
    },
    "DIA": {
        "ages": [(1, 5, 6), (6, 12, 2), (20, 70, 2)], "male": 0.5, "status": ["CONFIRMED"],
        "complaints": ["Watery stools 5 times since yesterday", "Loose stools and vomiting since last night",
                       "Diarrhea for 2 days after the flood"],
        "findings": ["Some dehydration, sunken eyes; family drinks from a shallow well since the flooding",
                     "Mild dehydration; household water source flooded last week", "No blood in stool, drinks eagerly"],
        "treatment": ["Oral rehydration salts after every loose stool, zinc for 10 days; boil drinking water",
                      "ORS, continue feeding; chlorinate the household water container"],
        "refer": 0.1, "follow_up": 2,
    },
    "LEP": {
        "ages": [(22, 58, 1)], "male": 0.85, "status": ["SUSPECTED", "CONFIRMED"],
        "complaints": ["Fever and calf pain after wading in a flooded rice field", "Fever, headache and muscle pains for 4 days"],
        "findings": ["Calf tenderness, red eyes (conjunctival suffusion); farmer, waded in floodwater barefoot",
                     "Severe calf pain, mild jaundice; works in the rice fields"],
        "treatment": ["Doxycycline started; referred for leptospirosis test", "Referred to the provincial hospital for evaluation"],
        "refer": 0.6, "follow_up": 2,
    },
}

# (barangay, disease, cases in the week of Nov 2, of Nov 9, of Nov 16 so far)
OUTBREAKS = [
    ("BRGY-08", "DEN", (3, 5, 8)),  # Caraycaray: where it starts (+1 lab-confirmed case last week, below)
    ("BRGY-18", "DEN", (0, 2, 3)),  # P. Inocentes Garcia, next door
    ("BRGY-06", "DEN", (0, 0, 3)),  # Calumpang
    ("BRGY-05", "DEN", (0, 0, 1)),  # Cabungaan: first case
    ("BRGY-20", "DIA", (0, 3, 5)),  # Sabang, coastal
    ("BRGY-01", "DIA", (0, 0, 3)),  # Agpangi, coastal
    ("BRGY-16", "LEP", (0, 1, 3)),  # Lucsoon, rice fields
    ("BRGY-11", "LEP", (0, 0, 1)),  # Lico: one case stays below the 3-case minimum, so no alert (no false alarm)
]


def week_days(today: date, weeks_ago: int) -> list[date]:
    """The days of a Monday-to-Sunday week that have already happened."""
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks_ago)
    return [d for d in (monday + timedelta(days=i) for i in range(7)) if d <= today]


def make_person(rng: random.Random, disease: dict, visit_day: date) -> dict:
    low, high, _ = rng.choices(disease["ages"], weights=[a[2] for a in disease["ages"]])[0]
    age = rng.randint(low, high)
    sex = "M" if rng.random() < disease["male"] else "F"
    born = visit_day - timedelta(days=age * 365 + rng.randint(30, 330))
    return {
        "last_name": rng.choice(SURNAMES), "first_name": rng.choice(MALE if sex == "M" else FEMALE), "middle_name": rng.choice(SURNAMES),
        "birthdate": born.isoformat(), "sex": sex, "purok": rng.randint(1, 7), "address": rng.choice(SITIOS),
        "civil_status": "Single" if age < 20 else rng.choice(["Married", "Married", "Single"]),
        "contact": f"0900 000 {rng.randint(0, 9999):04d}" if age >= 18 else None,
    }


def make_case(rng: random.Random, code: str, person: dict, visit_day: date, today: date) -> dict:
    disease = DISEASES[code]
    age = (visit_day - date.fromisoformat(person["birthdate"])).days // 365
    refer = rng.random() < disease["refer"]
    follow_up = visit_day + timedelta(days=disease["follow_up"])
    fields = {
        "visit_date": visit_day.isoformat(),
        "onset_date": (visit_day - timedelta(days=rng.randint(1, 4))).isoformat(),
        "chief_complaint": rng.choice(disease["complaints"]),
        **vitals("fever" if code != "DIA" or rng.random() < 0.5 else "normal", age, rng),
        "findings": rng.choice(disease["findings"]),
        "diagnosis_code": code,
        "case_status": rng.choice(disease["status"]),
        "treatment": rng.choice(disease["treatment"]),
        "disposition": "Referred to hospital" if refer else "For follow-up",
        "follow_up_date": None if refer else follow_up.isoformat(),
        "attended_by": rng.choice(ATTENDING),
    }
    if code == "DEN" and fields["temperature"]:
        fields["pulse"] = max(fields.get("pulse", 0), rng.randint(98, 118))
    return fields


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help="the RHU database; BHU records are in its bhu folder")
    args = parser.parse_args()

    stores = BhuStores(args.db.parent / "bhu")
    today = date.today()
    names = {b["barangay_id"]: b["name"] for b in rules.barangays()}
    added = skipped = 0

    def register(bid: str) -> None:
        # As the BHU page does before sending: the RHU only accepts reports from a registered station.
        with closing(db.connect(args.db)) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO nodes (node_id, barangay_id, registered_at) VALUES (?, ?, ?)",
                         (station_id(bid), bid, db.utc_now()))

    def new_patient(key: str, person: dict) -> tuple[str, bool]:
        client_id = str(uuid.uuid5(NAMESPACE, key))
        try:
            stores.get_client(key.split("/")[0], client_id)
            return client_id, False
        except RecordNotFound:
            stores.save_client(key.split("/")[0], client_id, ClientFields.model_validate(person).model_dump(mode="json"))
            return client_id, True

    def save(bid: str, client_id: str, key: str, fields: dict) -> str:
        visit_id = str(uuid.uuid5(NAMESPACE, key))
        stores.save_consultation(bid, client_id, visit_id, ConsultationFields.model_validate(fields).model_dump(mode="json"))
        return visit_id

    # 1-3. The outbreaks, week by week.
    for bid, code, per_week in OUTBREAKS:
        register(bid)
        rng = random.Random(f"healthpulz-scenario-{bid}-{code}")
        made = 0
        for weeks_ago, count in zip((2, 1, 0), per_week):
            days = week_days(today, weeks_ago)
            for n in range(count):
                visit_day = sorted(rng.choice(days) for _ in range(2))[1] if weeks_ago == 0 else rng.choice(days)  # this week: lean recent
                person = make_person(rng, DISEASES[code], visit_day)
                case = make_case(rng, code, person, visit_day, today)
                key = f"{bid}/{code}/w{weeks_ago}/{n}"
                client_id, is_new = new_patient(key, person)
                if not is_new:
                    skipped += 1
                    continue
                save(bid, client_id, key + "/visit", case)
                made += 1
        added += made
        print(f"  {names[bid]:32} {made:2} {code} case(s)")

    # 4a. A suspected dengue case in Caraycaray, confirmed two days later by a lab test.
    bid, rng = "BRGY-08", random.Random("healthpulz-scenario-correction")
    visit_day = today - timedelta(days=3)
    person = make_person(rng, DISEASES["DEN"], visit_day)
    client_id, is_new = new_patient(f"{bid}/correction", person)
    if is_new:
        case = make_case(rng, "DEN", person, visit_day, today) | {"case_status": "SUSPECTED", "findings": "Fever for 3 days, positive tourniquet test; NS1 test requested"}
        visit_id = save(bid, client_id, f"{bid}/correction/visit", case)
        confirmed = case | {"case_status": "CONFIRMED", "findings": "Fever for 3 days, positive tourniquet test; NS1 antigen test positive"}
        stores.correct_consultation(bid, visit_id, ConsultationFields.model_validate(confirmed).model_dump(mode="json"),
                                    "NS1 dengue test came back positive",
                                    [{"field": "Case status", "from": "Suspected", "to": "Confirmed"},
                                     {"field": "Findings", "from": case["findings"], "to": confirmed["findings"]}])
        added += 1
        print(f"  {names[bid]:32}  1 dengue case corrected: suspected -> confirmed (NS1 positive)")

    # 4b. A diarrhea consultation saved under the wrong child in Sabang: voided, then entered for the right one.
    bid, rng = "BRGY-20", random.Random("healthpulz-scenario-void")
    visit_day = today - timedelta(days=1)
    sibling_a = make_person(rng, DISEASES["DIA"], visit_day)
    sibling_b = make_person(rng, DISEASES["DIA"], visit_day) | {"last_name": sibling_a["last_name"], "purok": sibling_a["purok"], "address": sibling_a["address"]}
    wrong_id, is_new = new_patient(f"{bid}/void/a", sibling_a)
    right_id, _ = new_patient(f"{bid}/void/b", sibling_b)
    if is_new:
        case = make_case(rng, "DIA", sibling_b, visit_day, today)
        wrong_visit = save(bid, wrong_id, f"{bid}/void/a/visit", case)
        stores.void_consultation(bid, wrong_visit, f"Entered under the wrong child: it was {sibling_b['first_name']}, the sibling")
        save(bid, right_id, f"{bid}/void/b/visit", case)
        added += 1
        print(f"  {names[bid]:32}  1 consultation voided (wrong sibling) and entered for the right child")

    print(f"\nAdded {added} case(s)" + (f"; {skipped} were already there from an earlier run." if skipped else "."))
    print("If the server is running, the RHU's analytics pick them up within seconds and raise the alerts.")


if __name__ == "__main__":
    main()
