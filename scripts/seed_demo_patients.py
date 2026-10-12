#!/usr/bin/env python3
"""Seed synthetic demo patients (no real PHI) into a local OpenEMR database.

Prints SQL to stdout. Deterministic (seeded). Includes deliberate data-quality
defects (missing DOB/allergy, duplicate patient, stale labs) for the audit.
Usage: python3 scripts/seed_demo_patients.py | docker exec -i <mysql> mariadb -uroot -proot openemr
"""
import random
import uuid

random.seed(42)


def u():
    """Deterministic v4 UUID as SQL (matches OpenEMR format; MariaDB UUID() is v1)."""
    return "UNHEX('%s')" % uuid.UUID(int=random.getrandbits(128), version=4).hex
NAMES = [("Maria", "Alvarez", "F"), ("James", "Okafor", "M"), ("Linda", "Chen", "F"),
         ("Robert", "Hughes", "M"), ("Aisha", "Rahman", "F"), ("Thomas", "Novak", "M"),
         ("Grace", "Kim", "F"), ("Daniel", "Murphy", "M"), ("Priya", "Patel", "F"),
         ("George", "Walker", "M"), ("Helen", "Rossi", "F"), ("Carlos", "Mendez", "M")]
PROBLEMS = [("Type 2 diabetes mellitus", "ICD10:E11.9"), ("Essential hypertension", "ICD10:I10"),
            ("Hyperlipidemia", "ICD10:E78.5"), ("Chronic kidney disease stage 3", "ICD10:N18.3"),
            ("Asthma", "ICD10:J45.909"), ("Atrial fibrillation", "ICD10:I48.91")]
MEDS = [("Metformin", "500 mg", "860975"), ("Lisinopril", "10 mg", "314076"),
        ("Atorvastatin", "20 mg", "617310"), ("Warfarin", "5 mg", "855332"),
        ("Albuterol", "90 mcg", "745752"), ("Amlodipine", "5 mg", "197361")]
ALLERGIES = ["Penicillin", "Sulfa drugs", "Latex", "Codeine"]
LABS = [("4548-4", "Hemoglobin A1c", "%", "4.0-5.6"), ("2160-0", "Creatinine", "mg/dL", "0.6-1.3"),
        ("2345-7", "Glucose", "mg/dL", "70-99"), ("13457-7", "LDL Cholesterol", "mg/dL", "0-100")]


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


out = ["SET FOREIGN_KEY_CHECKS=0;",
       # Idempotent: remove any previously seeded rows (pid >= 100, order ids >= 1000) first.
       "DELETE FROM uuid_registry WHERE uuid IN (SELECT uuid FROM patient_data WHERE pid>=100 AND uuid IS NOT NULL);",
       "DELETE r FROM procedure_result r JOIN procedure_report p ON r.procedure_report_id=p.procedure_report_id WHERE p.procedure_report_id>=1000;",
       "DELETE FROM procedure_report WHERE procedure_report_id>=1000;",
       "DELETE FROM procedure_order_code WHERE procedure_order_id>=1000;",
       "DELETE FROM procedure_order WHERE procedure_order_id>=1000;",
       "DELETE FROM forms WHERE encounter>=1000;",
       "DELETE FROM form_encounter WHERE encounter>=1000;",
       "DELETE FROM form_vitals WHERE pid>=100;",
       "DELETE FROM prescriptions WHERE patient_id>=100;",
       "DELETE FROM lists WHERE pid>=100;",
       "DELETE FROM patient_data WHERE pid>=100;"]
enc_no = 1000
oid = 1000
for i, (fn, ln, sex) in enumerate(NAMES):
    pid = 100 + i
    prov = random.choice([1, 5, 6])
    dob = f"{random.randint(1940, 1995)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    dob_sql = "NULL" if i == 7 else q(dob)  # defect: missing DOB
    out.append(
        f"INSERT INTO patient_data (id,pid,pubpid,fname,lname,DOB,sex,providerID,date,regdate,uuid,"
        f"street,city,state,postal_code,phone_home,email) VALUES ({pid},{pid},{q('DEMO%d' % pid)},"
        f"{q(fn)},{q(ln)},{dob_sql},{q('Female' if sex == 'F' else 'Male')},{prov},NOW(),NOW(),{u()},"
        f"{q('%d Demo Street' % (10 + i))},'Austin','TX','78701',{q('512-555-01%02d' % i)},"
        f"{q(fn.lower() + '@example.invalid')});")
    for t, dx in random.sample(PROBLEMS, random.randint(1, 3)):
        out.append(f"INSERT INTO lists (uuid,date,type,title,diagnosis,begdate,activity,pid,user) "
                   f"VALUES ({u()},NOW(),'medical_problem',{q(t)},{q(dx)},'2019-03-01',1,{pid},'admin');")
    if i % 3 != 0:  # defect: some patients have no allergy documented
        out.append(f"INSERT INTO lists (uuid,date,type,title,begdate,activity,pid,user,reaction,severity_al) "
                   f"VALUES ({u()},NOW(),'allergy',{q(random.choice(ALLERGIES))},'2015-01-01',1,{pid},'admin','rash','moderate');")
    for drug, dose, rx in random.sample(MEDS, random.randint(1, 4)):
        out.append(f"INSERT INTO prescriptions (uuid,patient_id,provider_id,drug,dosage,rxnorm_drugcode,"
                   f"start_date,date_added,active,txDate,usage_category_title,request_intent_title,quantity,refills) "
                   f"VALUES ({u()},{pid},{prov},{q(drug)},{q(dose)},{q(rx)},'2023-06-01',NOW(),1,'2023-06-01','','','30',3);")
    for v in range(random.randint(2, 4)):  # encounters over time
        enc_no += 1
        days = 400 - v * 120 - random.randint(0, 20)
        dt = f"DATE_SUB(NOW(), INTERVAL {days} DAY)"
        reason = random.choice(["Follow-up", "Annual physical", "Medication review", "Cough", ""])
        out.append(
            f"INSERT INTO form_encounter (uuid,date,reason,facility,facility_id,pid,encounter,provider_id,pc_catid,"
            f"billing_facility,class_code) VALUES ({u()},{dt},{q(reason)},'Great Clinic',3,{pid},{enc_no},{prov},9,3,'AMB');")
        out.append(
            f"INSERT INTO forms (date,encounter,form_name,form_id,pid,user,groupname,authorized,deleted,formdir) "
            f"VALUES ({dt},{enc_no},'New Patient Encounter',LAST_INSERT_ID(),{pid},'admin','Default',1,0,'newpatient');")
        out.append(
            f"INSERT INTO form_vitals (uuid,date,pid,user,groupname,authorized,activity,bps,bpd,weight,height,pulse,temperature) "
            f"VALUES ({u()},{dt},{pid},'admin','Default',1,1,{random.randint(105, 165)},{random.randint(65, 100)},"
            f"{random.randint(120, 260)},{random.randint(60, 74)},{random.randint(56, 105)},{random.choice([98.2, 98.6, 99.1])});")
    # labs: one order + report per patient; skip some patients (defect), some stale (~2y old)
    if i % 4 != 3:
        oid += 1
        age = 700 if i % 5 == 0 else random.randint(10, 120)
        d = f"DATE_SUB(NOW(), INTERVAL {age} DAY)"
        out.append(f"INSERT INTO procedure_order (procedure_order_id,uuid,provider_id,patient_id,encounter_id,date_ordered,"
                   f"date_collected,order_status) VALUES ({oid},{u()},{prov},{pid},{enc_no},{d},{d},'complete');")
        out.append(f"INSERT INTO procedure_order_code (procedure_order_id,procedure_order_seq,procedure_code,procedure_name) "
                   f"VALUES ({oid},1,'CMP','Metabolic panel');")
        out.append(f"INSERT INTO procedure_report (procedure_report_id,uuid,procedure_order_id,date_collected,date_report,"
                   f"report_status) VALUES ({oid},{u()},{oid},{d},{d},'final');")
        for code, name, unit, rng in LABS:
            val = round(random.uniform(0.5, 11.0) if code == "4548-4" else random.uniform(0.6, 190), 1)
            flag = random.choice(["", "", "high", "low"])
            out.append(f"INSERT INTO procedure_result (uuid,procedure_report_id,result_code,result_text,date,units,result,"
                       f"`range`,abnormal,result_status) VALUES ({u()},{oid},{q(code)},{q(name)},{d},{q(unit)},{q(val)},{q(rng)},{q(flag)},'final');")
# defect: duplicate patient record (same name/DOB as first patient)
out.append("INSERT INTO patient_data (id,pid,pubpid,fname,lname,DOB,sex,date,regdate,uuid) "
           "SELECT 120,120,'DEMO120',fname,lname,DOB,sex,NOW(),NOW()," + u() + " FROM patient_data WHERE id=100;")
# Register every seeded uuid so the FHIR/REST layer treats rows like natively created ones.
for tbl in ("patient_data", "lists", "prescriptions", "form_encounter", "form_vitals",
            "procedure_order", "procedure_report", "procedure_result"):
    out.append(f"INSERT IGNORE INTO uuid_registry (uuid,table_name,table_id,created) "
               f"SELECT uuid,'{tbl}','id',NOW() FROM {tbl} WHERE uuid IS NOT NULL;")
out.append("UPDATE sequences SET id=GREATEST(id,2000);")
out.append("SET FOREIGN_KEY_CHECKS=1;")
print("\n".join(out))
