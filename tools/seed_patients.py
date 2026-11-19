"""Adds made-up patients to every BHU, so the demo has realistic records to open.

    python tools/seed_patients.py            # 5-10 patients per BHU, 1-2 consultations each
    python tools/seed_patients.py --min 3 --max 5

Everyone here is fictional: real residents' names, birthdates and health details are protected by the
Data Privacy Act (RA 10173), so no real person's data is used. Contact and PhilHealth numbers use
patterns that are never issued (0900 000 ..., 00-000...).

Records go in through the BHU record store, the same path as the BHU page: each consultation's
de-identified report waits in that BHU's outbox, and the running server sends it to the RHU within
seconds (or on its next start). Each patient has a fixed ID, so running this twice adds nothing new.
"""

import argparse
import random
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextlib import closing

from rhu import config, db, rules
from rhu.bhu_store import BhuStores, RecordNotFound, station_id
from rhu.models import ClientFields, ConsultationFields

NAMESPACE = uuid.UUID("6f1c2a52-6b0e-4d55-9a57-6c2b1d0f9e31")

SURNAMES = [
    "Dela Cruz", "Garcia", "Reyes", "Ramos", "Mendoza", "Santos", "Flores", "Gonzales", "Bautista", "Villanueva",
    "Fernandez", "Cruz", "De Guzman", "Lopez", "Perez", "Castillo", "Rivera", "Aquino", "Castro", "Torres",
    "De Leon", "Domingo", "Soriano", "Delos Santos", "Diaz", "Tolentino", "Valdez", "Morales", "Mercado", "Navarro",
    "Gomez", "Dizon", "Del Rosario", "Javier", "Corpuz", "Salvador", "Velasco", "Miranda", "Sarmiento", "Pacana",
    "Bacalso", "Cabahug", "Montecillo", "Espina", "Gerona", "Dadizon", "Lagado", "Cabrera", "Ortega", "Sabenorio",
]
MALE = ["Juan", "Jose", "Mark", "John Paul", "Christian", "Rodel", "Ramon", "Jerome", "Kenneth", "Arnel", "Romeo",
        "Daniel", "Nestor", "Joel", "Rogelio", "Bryan", "Carlo", "Jericho", "Ernesto", "Francis", "Paolo", "Miguel"]
FEMALE = ["Maria", "Ana", "Rosario", "Kristine", "Jessa", "Liza", "Marites", "Angelica", "Grace", "Lorna", "Elena",
          "Mary Joy", "Rowena", "Shiela", "Nenita", "Cristina", "Jocelyn", "Andrea", "Princess", "Teresita", "Divina"]
SITIOS = ["Sitio Proper", "Sitio Riverside", "Sitio Ilaya", "Sitio Ubos", "Sitio Centro", "Sitio Mangga", "Sitio Baybay"]
ATTENDING = ["Midwife Rosa Lagado", "Nurse Ana Espina", "BHW Lorna Cabahug", "Midwife Nenita Gerona", "Nurse Paolo Dadizon"]

VISITS = [
    (14, "HTN", None, ["Headache and dizziness", "Nape pain", "BP check-up"], "Elevated BP on two readings", "Amlodipine 5 mg once daily, low-salt diet", "htn"),
    (14, "ARI", None, ["Cough and colds for 3 days", "Sore throat and runny nose", "Cough with phlegm"], "Clear breath sounds, mild throat redness", "Increased fluids, paracetamol as needed", "fever"),
    (8, "WND", None, ["Cut on the foot from bolo", "Abrasion on the knee after a fall", "Laceration on the hand"], "Clean wound, no signs of infection", "Wound cleaning and dressing, tetanus toxoid", "normal"),
    (7, "ART", None, ["Knee pain when walking", "Joint pain in the fingers"], "Tender, mildly swollen joint", "Paracetamol, warm compress", "normal"),
    (6, "SKN", None, ["Itchy rashes on the arms", "Fungal patches on the back"], "Scaly patches, no discharge", "Antifungal cream twice daily", "normal"),
    (4, "DIA", None, ["Loose stools for 2 days", "Watery stools and stomach ache"], "Mild dehydration", "Oral rehydration salts, zinc", "fever"),
    (4, "FEV", None, ["Fever for 2 days", "On and off fever with chills"], "No rashes, no localizing signs", "Paracetamol, tepid sponge bath", "fever"),
    (3, "KID", None, ["Painful urination", "Lower back pain"], "Flank tenderness", "Referred for urinalysis", "normal"),
    (2, "DEN", None, ["High fever for 3 days with body aches"], "Petechiae on forearms, positive tourniquet test", "Oral fluids, paracetamol; watch for warning signs", "fever"),
    (9, "OTH", "Prenatal check-up", ["Prenatal check-up", "Routine pregnancy check-up"], "Fundic height appropriate for age of gestation", "Ferrous sulfate with folic acid", "normal"),
    (6, "OTH", "Diabetes follow-up", ["Diabetes follow-up", "Blood sugar check"], "Fasting blood sugar slightly high", "Continue metformin, diet counseling", "normal"),
    (5, "OTH", "Gastritis", ["Stomach pain after meals", "Burning epigastric pain"], "Epigastric tenderness", "Antacid, small frequent meals", "normal"),
]


def vitals(kind: str, age: int, rng: random.Random) -> dict:
    adult = age >= 18
    fields = {"temperature": round(rng.uniform(36.2, 37.2), 1)}
    if kind == "fever":
        fields["temperature"] = round(rng.uniform(37.6, 39.4), 1)
    if adult:
        sys_bp, dia_bp = (rng.randint(142, 168), rng.randint(90, 104)) if kind == "htn" else (rng.randint(108, 132), rng.randint(68, 84))
        fields |= {"bp_systolic": sys_bp, "bp_diastolic": dia_bp, "pulse": rng.randint(66, 96), "respiratory_rate": rng.randint(14, 20)}
        fields |= {"weight": round(rng.uniform(45, 78), 1), "height": round(rng.uniform(148, 172), 1)}
    else:
        fields |= {"pulse": rng.randint(80, 120), "respiratory_rate": rng.randint(18, 30)}
        weight, height = (8 + 2 * age, 75 + 6 * age) if age >= 1 else (7, 66)
        fields |= {"weight": round(weight * rng.uniform(0.9, 1.1), 1), "height": round(height * rng.uniform(0.96, 1.04), 1)}
    return fields


def birthdate(rng: random.Random, today: date) -> date:
    age = rng.choices([rng.randint(1, 12), rng.randint(13, 25), rng.randint(26, 59), rng.randint(60, 84)], weights=[3, 2, 5, 3])[0]
    return today - timedelta(days=age * 365 + rng.randint(0, 364))


def make_patient(rng: random.Random, today: date) -> dict:
    sex = rng.choice("MF")
    born = birthdate(rng, today)
    age = (today - born).days // 365
    civil = "Single" if age < 20 else rng.choice(["Married", "Married", "Single", "Widowed" if age > 55 else "Live-in"])
    return {
        "last_name": rng.choice(SURNAMES),
        "first_name": rng.choice(MALE if sex == "M" else FEMALE),
        "middle_name": rng.choice(SURNAMES),
        "birthdate": born.isoformat(),
        "sex": sex,
        "civil_status": civil,
        "purok": rng.randint(1, 7),
        "address": rng.choice(SITIOS),
        "contact": f"0900 000 {rng.randint(0, 9999):04d}" if age >= 18 and rng.random() < 0.7 else None,
        "philhealth": f"00-000{rng.randint(0, 999999):06d}-{rng.randint(0, 9)}" if rng.random() < 0.6 else None,
    }


def make_visit(rng: random.Random, patient: dict, visit_day: date, today: date) -> dict:
    age = (visit_day - date.fromisoformat(patient["birthdate"])).days // 365
    options = [v for v in VISITS if not (v[2] == "Prenatal check-up" and (patient["sex"] == "M" or not 17 <= age <= 42))
               and not (v[1] in ("HTN", "ART") and age < 30) and not (v[2] == "Diabetes follow-up" and age < 30)]
    _, code, other, complaints, findings, treatment, kind = rng.choices(options, weights=[v[0] for v in options])[0]
    onset = visit_day - timedelta(days=rng.randint(1, 4))
    follow_up = visit_day + timedelta(days=rng.choice([7, 14])) if code in ("HTN", "WND", "DEN") or other else None
    return {
        "visit_date": visit_day.isoformat(),
        "onset_date": onset.isoformat(),
        "chief_complaint": rng.choice(complaints),
        **vitals(kind, age, rng),
        "findings": findings,
        "diagnosis_code": code,
        "diagnosis_text": other,
        "case_status": rng.choice(["SUSPECTED", "CONFIRMED"]) if code in ("DEN", "DIA", "FEV") else "CONFIRMED",
        "treatment": treatment,
        "disposition": "For follow-up" if follow_up and follow_up >= today else "Home care",
        "follow_up_date": follow_up.isoformat() if follow_up else None,
        "attended_by": rng.choice(ATTENDING),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min", type=int, default=5, help="fewest patients per BHU (default 5)")
    parser.add_argument("--max", type=int, default=10, help="most patients per BHU (default 10)")
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help="the RHU database; BHU records are in its bhu folder")
    args = parser.parse_args()

    stores = BhuStores(args.db.parent / "bhu")
    today = date.today()
    added_patients = added_visits = skipped = 0
    for barangay in rules.barangays():
        bid = barangay["barangay_id"]
        with closing(db.connect(args.db)) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO nodes (node_id, barangay_id, registered_at) VALUES (?, ?, ?)",
                         (station_id(bid), bid, db.utc_now()))
        rng = random.Random(f"healthpulz-demo-{bid}")
        count = rng.randint(args.min, args.max)
        new_here = 0
        for n in range(count):
            client_id = str(uuid.uuid5(NAMESPACE, f"{bid}/patient/{n}"))
            patient = make_patient(rng, today)
            visits = [make_visit(rng, patient, today - timedelta(days=rng.randint(0, 45)), today) for _ in range(rng.choice([1, 1, 2]))]
            try:
                stores.get_client(bid, client_id)
                skipped += 1
                continue
            except RecordNotFound:
                pass
            fields = ClientFields.model_validate(patient).model_dump(mode="json")
            stores.save_client(bid, client_id, fields)
            for v, visit in enumerate(sorted(visits, key=lambda x: x["visit_date"])):
                visit_id = str(uuid.uuid5(NAMESPACE, f"{bid}/patient/{n}/visit/{v}"))
                stores.save_consultation(bid, client_id, visit_id, ConsultationFields.model_validate(visit).model_dump(mode="json"))
                added_visits += 1
            added_patients += 1
            new_here += 1
        print(f"  {barangay['name']:32} {new_here} new patient(s)")
    print(f"\nAdded {added_patients} patients and {added_visits} consultations"
          + (f"; {skipped} were already there from an earlier run." if skipped else "."))
    print("Their reports go to the RHU within seconds if the server is running, or when it next starts.")


if __name__ == "__main__":
    main()
