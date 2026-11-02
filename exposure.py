"""Time weighted worker and department exposure indicators; not medical diagnoses."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DEMO_LIMITS={"temperature_c":35,"wbgt_c":30,"pm25_ug_m3":75,"pm10_ug_m3":250,"co_ppm":50,"co2_ppm":2000,"h2s_ppm":10,"o2_percent":19}

def summarize(workers:list[dict], history:list[dict], window_minutes:int=480, sample_minutes:int=5):
    cutoff=datetime.now(timezone.utc)-timedelta(minutes=window_minutes)
    recent=[]
    for r in history:
        try: stamp=datetime.fromisoformat(r["timestamp"].replace("Z","+00:00"))
        except Exception:continue
        if stamp>=cutoff:recent.append(r)
    output=[]; departments=defaultdict(list)
    for worker in workers:
        readings=[r for r in recent if r.get("facility_id")==worker["facility_id"]]
        hazardous=[]; repeated=[]
        for key,limit in DEMO_LIMITS.items():
            vals=[float(r[key]) for r in readings if isinstance(r.get(key),(int,float))]
            if key=="o2_percent": exceed=[v for v in vals if v<limit]
            else:exceed=[v for v in vals if v>=limit]
            if exceed:
                hazardous.append({"parameter":key,"exceedance_samples":len(exceed),"estimated_exposure_minutes":len(exceed)*sample_minutes,"max_reading":round(max(exceed) if key!="o2_percent" else min(exceed),3),"demo_screening_limit":limit})
                if len(exceed)>=2:repeated.append(key)
        exposure_minutes=sum(x["estimated_exposure_minutes"] for x in hazardous)
        indicator="ELEVATED" if hazardous else "ROUTINE"
        rec="Supervisor review recommended; verify sensor readings and reduce exposure per site procedure." if hazardous else "Continue normal checks; confirm monitor coverage."
        item={**worker,"window_minutes":window_minutes,"samples":len(readings),"estimated_exposure_minutes":exposure_minutes,"indicator":indicator,"hazards":hazardous,"repeated_exposure_patterns":repeated,"recommendation":rec,"medical_diagnosis":False,"synthetic":all(r.get("synthetic",False) for r in readings) if readings else True}
        output.append(item);departments[worker.get("department","Unassigned")].append(item)
    return {"window_minutes":window_minutes,"sample_interval_minutes":sample_minutes,"workers":output,"departments":[{"department":d,"workers":len(ws),"elevated_workers":sum(w["indicator"]=="ELEVATED" for w in ws),"estimated_exposure_minutes":sum(w["estimated_exposure_minutes"] for w in ws),"hazards":sorted({h["parameter"] for w in ws for h in w["hazards"]})} for d,ws in departments.items()],"model":"time weighted sample-count estimate against illustrative screening limits"}
