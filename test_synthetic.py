"""Teste sintético: popula o banco com dados fake para validar relatórios."""
import os
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import yaml

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from storage import Storage
import report
import notify

ROOT = Path(__file__).resolve().parent if "__file__" in dir() else Path("/sessions/kind-zen-fermat/mnt/outputs/flight-monitor")
DB_DIR = Path(os.environ.get("FLIGHT_MONITOR_TEST_DB_DIR", "/tmp"))
DB_PATH = DB_DIR / "flights_test.db"
CONFIG_PATH = ROOT / "routes.yaml"
REPORTS_DIR = Path("/tmp/reports_test")
REPORTS_DIR.mkdir(exist_ok=True)

if DB_PATH.exists():
    DB_PATH.unlink()
for ext in ("-journal", "-wal", "-shm"):
    p = Path(str(DB_PATH) + ext)
    if p.exists():
        p.unlink()

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = yaml.safe_load(f)
for k in ("outbound_date","return_date"):
    v = config["trip"].get(k)
    if v and not isinstance(v, str):
        config["trip"][k] = v.isoformat()

trip = config["trip"]
hubs = config["hubs"]
storage = Storage(str(DB_PATH))

base_gru = {"MIA":3200,"JFK":3800,"EWR":3700,"MAD":4100,"LIS":3500,"BCN":4500,
            "CDG":4300,"AMS":4400,"LHR":4600,"FRA":4500,"MUC":4800,"ZRH":5100}
base_kef = {"MIA":2800,"JFK":1900,"EWR":1850,"MAD":1400,"LIS":1500,"BCN":1450,
            "CDG":1300,"AMS":1250,"LHR":1350,"FRA":1400,"MUC":1450,"ZRH":1500}
al_gru = {"MIA":"AA","JFK":"LA","EWR":"UA","MAD":"IB","LIS":"TP","BCN":"UX",
          "CDG":"AF","AMS":"KL","LHR":"BA","FRA":"LH","MUC":"LH","ZRH":"LX"}

random.seed(42)
now = datetime.utcnow()

for sweep_idx in range(14):
    fetched_at = (now - timedelta(days=7) + timedelta(hours=sweep_idx*12)).isoformat(timespec="seconds")
    trend = 1.0 - (sweep_idx * 0.01)
    for hub in hubs:
        code = hub["code"]
        if code in ("BCN","LIS") and sweep_idx < 3:
            continue
        pa = round(base_gru[code] * trend * random.uniform(0.95,1.05), 2)
        pb = round(base_kef[code] * trend * random.uniform(0.95,1.05), 2)
        storage.save_quote(source="travelpayouts", origin=trip["origin"], destination=code,
            outbound_date=trip["outbound_date"], return_date=trip["return_date"],
            direct=True, price=pa, currency="BRL", airline=al_gru[code],
            flight_number=str(random.randint(100,999)),
            departure_time=f"{trip['outbound_date']}T22:30:00-03:00",
            duration_min=720, transfers=0,
            booking_link=f"/search/GRU2509{code}1110?demo",
            raw={"demo":True}, fetched_at=fetched_at)
        storage.save_quote(source="travelpayouts", origin=code, destination=trip["destination"],
            outbound_date=trip["outbound_date"], return_date=trip["return_date"],
            direct=True, price=pb, currency="BRL", airline="FI",
            flight_number=str(random.randint(100,999)),
            departure_time=f"{trip['outbound_date']}T08:00:00+02:00",
            duration_min=180, transfers=0,
            booking_link=f"/search/{code}2509KEF1110?demo",
            raw={"demo":True}, fetched_at=fetched_at)

# SerpAPI fake para top 3
for code in ["AMS","LHR","CDG"]:
    storage.save_quote(source="serpapi", origin=trip["origin"], destination=code,
        outbound_date=trip["outbound_date"], return_date=trip["return_date"],
        direct=True, price=round(base_gru[code]*0.92*1.05, 2), currency="BRL",
        airline=al_gru[code], duration_min=720, transfers=0, raw={"sp":True})
    storage.save_quote(source="serpapi", origin=code, destination=trip["destination"],
        outbound_date=trip["outbound_date"], return_date=trip["return_date"],
        direct=True, price=round(base_kef[code]*0.92*1.05, 2), currency="BRL",
        airline="FI", duration_min=180, transfers=0, raw={"sp":True})

print(f"DB populado: {DB_PATH}")
xlsx = REPORTS_DIR / "flights.xlsx"
html = REPORTS_DIR / "index.html"
report.generate_excel(storage, config, str(xlsx))
report.generate_html(storage, config, str(html))
print(f"Excel: {xlsx} ({xlsx.stat().st_size} bytes)")
print(f"HTML:  {html} ({html.stat().st_size} bytes)")

# Copia relatórios para o mount FUSE (cópia atômica funciona)
shutil.copy(str(xlsx), str(ROOT / "reports" / "flights.xlsx"))
shutil.copy(str(html), str(ROOT / "reports" / "index.html"))
print("Relatórios copiados para o mount.")
