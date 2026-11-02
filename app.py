"""IndustrialShield AI demonstrator API. Synthetic data only; not a certified safety system."""
from __future__ import annotations

import asyncio, csv, io, math, os, random, secrets, threading
import base64, hashlib, hmac, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Header, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

VERCEL_RUNTIME=os.getenv("VERCEL")=="1"
if VERCEL_RUNTIME:
    _required_runtime_env=("INDUSTRIALSHIELD_SECRET","INDUSTRIALSHIELD_DEMO_EMAIL","INDUSTRIALSHIELD_DEMO_PASSWORD","REDIS_URL")
    _missing_runtime_env=[name for name in _required_runtime_env if not os.getenv(name)]
    if _missing_runtime_env:
        raise RuntimeError("Missing required Vercel environment variables: "+", ".join(_missing_runtime_env))
    if len(os.environ["INDUSTRIALSHIELD_SECRET"])<32 or os.environ["INDUSTRIALSHIELD_SECRET"]=="local-demo-secret-change-me":
        raise RuntimeError("INDUSTRIALSHIELD_SECRET must be a unique random value of at least 32 characters on Vercel.")
    if len(os.environ["INDUSTRIALSHIELD_DEMO_PASSWORD"])<16 or os.environ["INDUSTRIALSHIELD_DEMO_PASSWORD"]=="ChangeMe-For-Deployment":
        raise RuntimeError("Set a unique INDUSTRIALSHIELD_DEMO_PASSWORD of at least 16 characters on Vercel.")

import database as store
from community import propagate
from exposure import summarize as exposure_summary
from intelligence import ML, forecast as forecast_window, temporal_features
from workflows import REGISTRY as WORKFLOW_REGISTRY, incident_summary, recommendations
from redis.asyncio import Redis

app = FastAPI(title="IndustrialShield AI", version="0.2.0", description="Persistent, ML-enabled industrial safety intelligence platform")
PROJECT_ROOT=Path(__file__).resolve().parent
PUBLIC_DIR=PROJECT_ROOT/"public"
STATIC_DIR=PUBLIC_DIR/"static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
LOCK = threading.RLock()
NOW = lambda: datetime.now(timezone.utc)
FACILITIES = [
    {"id":"textile-01","name":"Northstar Textile Works","type":"Textile manufacturing","city":"Coimbatore","lat":11.0168,"lon":76.9558},
    {"id":"chemical-01","name":"Meridian Chemical Processing","type":"Chemical processing","city":"Pune","lat":18.5204,"lon":73.8567},
    {"id":"cement-01","name":"Granite Ridge Cement","type":"Cement manufacturing","city":"Hyderabad","lat":17.3850,"lon":78.4867},
]
ZONES = {"textile-01":"Weaving floor","chemical-01":"Solvent handling","cement-01":"Kiln line"}
THRESHOLDS = {"temperature_c": {"moderate":32,"high":35,"critical":40}, "wbgt_c":{"moderate":28,"high":30,"critical":32}, "pm25_ug_m3":{"moderate":35,"high":75,"critical":150}, "pm10_ug_m3":{"moderate":100,"high":250,"critical":500}, "co_ppm":{"moderate":25,"high":50,"critical":100}, "co2_ppm":{"moderate":1000,"high":2000,"critical":5000}, "h2s_ppm":{"moderate":5,"high":10,"critical":20}, "o2_percent":{"moderate":19.5,"high":19,"critical":18}}
# Demonstration screening thresholds only. Validate applicable regulations and site-specific controls before use.
if not VERCEL_RUNTIME:
    store.initialize()
READING_HISTORY: list[dict[str,Any]]=store.load_readings()
ALERTS: list[dict[str,Any]]=store.load_alerts()
AUDIT: list[dict[str,Any]]=store.load_audit()
_latest={}
for _r in READING_HISTORY: _latest[_r["facility_id"]]=_r
READINGS: list[dict[str,Any]]=list(_latest.values())
_persistent_state=store.load_state("simulator")
SCENARIO=_persistent_state.get("scenario","normal")
METEO=_persistent_state.get("meteorology",{"wind_from_degrees":270.0,"wind_speed_m_s":3.2,"synthetic":True})
DEMO_MODE=os.getenv("INDUSTRIALSHIELD_DEMO","true").lower() in {"1","true","yes"}
EVENT_LOOP=None
WS_CLIENTS:set[asyncio.Queue]=set()
_demo_email=os.getenv("INDUSTRIALSHIELD_DEMO_EMAIL","admin@industrialshield.demo")
_demo_password=os.getenv("INDUSTRIALSHIELD_DEMO_PASSWORD","ChangeMe-For-Deployment")
_demo_salt=secrets.token_bytes(16)
USERS={_demo_email:{"password_salt":_demo_salt.hex(),"password_hash":hashlib.pbkdf2_hmac("sha256",_demo_password.encode(),_demo_salt,240000).hex(),"role":"System Administrator","facility_ids":[f["id"] for f in FACILITIES]}}
del _demo_password,_demo_salt
WORKERS=[{"id":"W-104","name":"Worker 104","department":"Production","facility_id":"textile-01","zone":"Weaving floor"},{"id":"W-217","name":"Worker 217","department":"Operations","facility_id":"chemical-01","zone":"Solvent handling"},{"id":"W-309","name":"Worker 309","department":"Kiln Operations","facility_id":"cement-01","zone":"Kiln line"}]

def refresh_persistent_state():
    """Reload shared state so warm Vercel instances see writes from other instances."""
    global READING_HISTORY, READINGS, ALERTS, AUDIT, SCENARIO, METEO
    with LOCK:
        READING_HISTORY=store.load_readings()
        latest={}
        for reading in READING_HISTORY:
            latest[reading["facility_id"]]=reading
        READINGS=list(latest.values())
        ALERTS=store.load_alerts()
        AUDIT=store.load_audit()
        state=store.load_state("simulator")
        SCENARIO=state.get("scenario","normal")
        METEO=state.get("meteorology",{"wind_from_degrees":270.0,"wind_speed_m_s":3.2,"synthetic":True})

@app.middleware("http")
async def refresh_serverless_state(request, call_next):
    if VERCEL_RUNTIME and not request.url.path.startswith("/static/") and request.url.path!="/api/health":
        await run_in_threadpool(refresh_persistent_state)
    return await call_next(request)

def reading_for(f:dict, scenario:str, index:int=0)->dict:
    rng=random.Random(f["id"]+scenario+str(index))
    v={"temperature_c":round(26+rng.random()*4,1),"humidity_percent":round(45+rng.random()*18,1),"wbgt_c":round(23+rng.random()*3,1),"pm25_ug_m3":round(8+rng.random()*12,1),"pm10_ug_m3":round(20+rng.random()*30,1),"co_ppm":round(rng.random()*4,1),"co2_ppm":round(500+rng.random()*250,0),"h2s_ppm":round(rng.random()*.2,2),"o2_percent":round(20.7+rng.random()*.2,1),"ventilation":"Normal"}
    if scenario in ("extreme_heat","multiple_hazards") and f["id"]=="textile-01": v.update(temperature_c=round(35+rng.random()*3,1),humidity_percent=round(76+rng.random()*10,1),wbgt_c=round(30+rng.random()*2,1))
    if scenario in ("poor_ventilation","multiple_hazards") and f["id"]=="chemical-01": v.update(co2_ppm=round(2300+rng.random()*600),ventilation="Poor")
    if scenario in ("air_pollution","multiple_hazards") and f["id"]=="cement-01": v.update(pm25_ug_m3=round(90+rng.random()*50,1),pm10_ug_m3=round(300+rng.random()*180,1))
    if scenario in ("gas_leak","multiple_hazards") and f["id"]=="chemical-01": v.update(h2s_ppm=round(22+rng.random()*6,1),co_ppm=round(120+rng.random()*45,1),o2_percent=round(16.8-rng.random()*.8,1))
    return {"facility_id":f["id"],"sensor_id":f"S-{f['id'].split('-')[0].upper()}-01","timestamp":NOW().isoformat(),"synthetic":True,**v}

def rank_for(r):
    level=0; findings=[]
    observed=sum(1 for key in THRESHOLDS if isinstance(r.get(key),(int,float)))
    for key,val in r.items():
        if key not in THRESHOLDS or not isinstance(val,(int,float)): continue
        t=THRESHOLDS[key]
        if key=="o2_percent": sev=4 if val<t["critical"] else 3 if val<t["high"] else 2 if val<t["moderate"] else 0
        else: sev=4 if val>=t["critical"] else 3 if val>=t["high"] else 2 if val>=t["moderate"] else 0
        if sev:
            level=max(level,sev); findings.append({"parameter":key,"value":val,"threshold":t[{2:"moderate",3:"high",4:"critical"}[sev]],"severity":{2:"MODERATE",3:"HIGH",4:"CRITICAL"}[sev]})
    # Anomalies are an explicit baseline deviation screen. No model accuracy or probability is implied.
    anomaly=any(r.get(k,0)>v for k,v in (("temperature_c",34), ("pm25_ug_m3",60), ("co_ppm",35), ("co2_ppm",1500), ("h2s_ppm",3)))
    if anomaly: level=max(level,1)
    levels=["NORMAL","LOW","MODERATE","HIGH","CRITICAL"]
    score=min(100, max(0, level*25 + min(20,len(findings)*6) + (8 if anomaly else 0)))
    incomplete=observed < len(THRESHOLDS)
    if observed==0: level=max(level,1);score=max(score,10)
    return {"risk_level":levels[level],"risk_score":score,"findings":findings,"anomaly_flag":anomaly,"data_quality":"INCOMPLETE" if incomplete else "COMPLETE","explanation":("Insufficient sensor readings; do not interpret as safe. " if observed==0 else "")+(("Threshold exceeded: "+", ".join(x["parameter"] for x in findings)) if findings else ("Baseline anomaly screen flagged an unusual reading." if anomaly else "No configured demonstration threshold was exceeded.")),"recommendation":"Restore or verify sensor coverage before treating conditions as normal." if observed==0 else ("Verify the sensor and investigate the unusual reading." if anomaly else recommend(level,findings)),"timestamp":r["timestamp"],"facility_id":r["facility_id"],"synthetic":r["synthetic"]}

def recommend(level, findings):
    if level>=3: return "Follow site emergency procedures; move people away from the affected zone, notify the safety officer, and verify with approved instruments."
    if level==2: return "Investigate promptly, check ventilation and sensor status, and consider reducing exposure while a qualified supervisor reviews."
    if findings: return "Review the indicated condition, verify the reading, and monitor the zone for change."
    return "Continue routine monitoring and follow site safety procedures."

def enriched_assessment(r):
    out=rank_for(r)
    out["ml"]=ML.assess(r)
    h=[x for x in READING_HISTORY if x["facility_id"]==r["facility_id"]]
    out["temporal_features"]=temporal_features(h)
    out["forecast"]=forecast_window(h,30)
    try:
        stamp=datetime.fromisoformat(r["timestamp"].replace("Z","+00:00"))
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        age_minutes=max(0,(NOW()-stamp.astimezone(timezone.utc)).total_seconds()/60)
    except (ValueError,KeyError,TypeError):age_minutes=float("inf")
    out["freshness"]={"status":"STALE" if age_minutes>10 else "FRESH","age_minutes":None if not math.isfinite(age_minutes) else round(age_minutes,1),"stale_after_minutes":10}
    if age_minutes>10:
        out["data_quality"]="STALE"
        if out["risk_level"] in ("NORMAL","LOW"):
            out["risk_level"]="LOW";out["risk_score"]=max(10,out["risk_score"])
            out["explanation"]="Latest sensor data is stale; do not interpret the last known normal values as safe."
            out["recommendation"]="Restore sensor updates and verify the affected zone with approved instruments."
        out["forecast"]["status"]="STALE_HISTORY"
        out["forecast"]["confidence"]="UNAVAILABLE · latest observation is stale"
    out["current_vs_predicted"]={"current_hazard":out["risk_level"] in ("HIGH","CRITICAL"),"predicted_hazards":[p for p in out["forecast"]["predictions"] if p["crossing_projected"]]}
    return out

def audit(event, alert_id, actor):
    item={"event":event,"alert_id":alert_id,"actor":actor,"timestamp":NOW().isoformat()}
    AUDIT.append(item);store.save_audit(item);publish_event({"type":"audit","event":item})

async def _broadcast(event):
    redis_url=os.getenv("REDIS_URL")
    if redis_url:
        client=Redis.from_url(redis_url,decode_responses=True)
        try:await client.publish("industrialshield.events",json.dumps(event,default=str))
        except Exception:pass
        finally:await client.aclose()
        return
    for q in list(WS_CLIENTS):
        try:q.put_nowait(event)
        except asyncio.QueueFull:
            try:q.get_nowait();q.put_nowait(event)
            except (asyncio.QueueEmpty,asyncio.QueueFull):pass

def publish_event(event):
    if EVENT_LOOP and EVENT_LOOP.is_running():
        EVENT_LOOP.call_soon_threadsafe(lambda: asyncio.create_task(_broadcast(event)))

def process_reading(r):
    with LOCK:
        READING_HISTORY.append(r)
        if len(READING_HISTORY)>10000:del READING_HISTORY[:-10000]
        existing=next((x for x in READINGS if x["facility_id"]==r["facility_id"]),None)
        if existing:READINGS[READINGS.index(existing)]=r
        else:READINGS.append(r)
        store.save_reading(r)
        assessment=enriched_assessment(r)
        active=next((x for x in ALERTS if x["facility_id"]==r["facility_id"] and x["status"]!="RESOLVED"),None)
        if assessment["risk_level"] in ("HIGH","CRITICAL") and not active:
            alert={"id":secrets.token_hex(5),"facility_id":r["facility_id"],"severity":assessment["risk_level"],"status":"OPEN","title":f"{assessment['risk_level']} environmental hazard","assessment":assessment,"sensor_reading":r,"created_at":NOW().isoformat(),"acknowledged_at":None,"resolved_at":None,"interventions":[]}
            ALERTS.insert(0,alert);store.save_alert(alert);audit("CREATED",alert["id"],"risk-engine")
            publish_event({"type":"critical_alert","alert":alert})
        publish_event({"type":"sensor_reading","facility_id":r["facility_id"],"timestamp":r["timestamp"]})
        return assessment

def evaluate_all():
    for i,f in enumerate(FACILITIES):
        r=reading_for(f,SCENARIO,len(READING_HISTORY)+i)
        process_reading(r)
_stale_synthetic=bool(READINGS) and all(r.get("synthetic",False) for r in READINGS) and any((NOW()-datetime.fromisoformat(r["timestamp"].replace("Z","+00:00")).astimezone(timezone.utc)).total_seconds()>600 for r in READINGS)
if not READINGS or (DEMO_MODE and _stale_synthetic):evaluate_all()

class Ingest(BaseModel):
    facility_id:str
    sensor_id:str
    timestamp:datetime
    temperature_c:float|None=None; humidity_percent:float|None=None; wbgt_c:float|None=None
    pm25_ug_m3:float|None=None; pm10_ug_m3:float|None=None; co_ppm:float|None=None; co2_ppm:float|None=None; h2s_ppm:float|None=None; o2_percent:float|None=None

def auth(authorization:str|None=Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Sign in required")
    try:
        head,payload,sig=authorization[7:].split(".")
        expected=base64.urlsafe_b64encode(hmac.new(os.getenv("INDUSTRIALSHIELD_SECRET","local-demo-secret-change-me").encode(),f"{head}.{payload}".encode(),hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(sig,expected):raise ValueError("signature")
        claims=json.loads(base64.urlsafe_b64decode(payload+"="*(-len(payload)%4)))
        if claims["exp"]<NOW().timestamp():raise ValueError("expired")
        return claims
    except Exception:raise HTTPException(401,"Invalid or expired session")

def issue_token(email,user):
    header=base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload=base64.urlsafe_b64encode(json.dumps({"sub":email,"role":user["role"],"facility_ids":user["facility_ids"],"exp":NOW().timestamp()+1800},separators=(",",":")).encode()).decode().rstrip("=")
    body=f"{header}.{payload}"
    sig=base64.urlsafe_b64encode(hmac.new(os.getenv("INDUSTRIALSHIELD_SECRET","local-demo-secret-change-me").encode(),body.encode(),hashlib.sha256).digest()).decode().rstrip("=")
    return f"{body}.{sig}"

def ensure_scope(u,facility_id):
    if facility_id not in u.get("facility_ids",[]):raise HTTPException(403,"Facility access denied")

@app.get("/",response_class=HTMLResponse)
def index():
    return (PUBLIC_DIR/"index.html").read_text(encoding="utf8")
@app.post("/api/auth/login")
def login(body:dict):
    email=body.get("email",""); user=USERS.get(email)
    if not user:raise HTTPException(401,"Invalid credentials")
    candidate=hashlib.pbkdf2_hmac("sha256",str(body.get("password","")).encode(),bytes.fromhex(user["password_salt"]),240000).hex()
    if not secrets.compare_digest(candidate,user["password_hash"]): raise HTTPException(401,"Invalid credentials")
    token=issue_token(email,user)
    return {"access_token":token,"token_type":"bearer","user":{"email":email,"role":user["role"]}}
@app.get("/api/me")
def me(u=Depends(auth)): return {"email":u["sub"],"role":u["role"],"expires_at":datetime.fromtimestamp(u["exp"],timezone.utc).isoformat()}
@app.get("/api/overview")
def overview(u=Depends(auth)):
    facilities=[f for f in FACILITIES if f["id"] in u.get("facility_ids",[])]
    readings=[r for r in READINGS if r["facility_id"] in u.get("facility_ids",[])]
    assessments=[enriched_assessment(r) for r in readings]
    allowed=set(u.get("facility_ids",[]))
    history=[r for r in READING_HISTORY if r["facility_id"] in allowed]
    exposures=exposure_summary([w for w in WORKERS if w["facility_id"] in allowed],history,480)
    history_preview=[]
    for facility in facilities:history_preview.extend([r for r in history if r["facility_id"]==facility["id"]][-60:])
    return {"facilities":facilities,"readings":readings,"history":history_preview,"assessments":assessments,"alerts":[a for a in ALERTS if a["facility_id"] in allowed],"workers":worker_view(allowed),"exposure":exposures,"scenario":SCENARIO,"synthetic":True,"thresholds":THRESHOLDS,"model_metrics":ML.metrics,"community":community_intelligence(allowed),"updated_at":NOW().isoformat()}
@app.get("/api/facilities")
def facilities(u=Depends(auth)):return [f for f in FACILITIES if f["id"] in u.get("facility_ids",[])]
@app.get("/api/sensors")
def sensors(u=Depends(auth)):
    return [{"sensor_id":r["sensor_id"],"facility_id":r["facility_id"],"last_seen":r["timestamp"],"status":"ONLINE","synthetic":r["synthetic"]} for r in READINGS if r["facility_id"] in u.get("facility_ids",[])]
@app.post("/api/ingest")
def ingest(body:Ingest,u=Depends(auth)):
    if body.facility_id not in {x["id"] for x in FACILITIES}:raise HTTPException(404,"Unknown facility")
    ensure_scope(u,body.facility_id)
    if body.timestamp.tzinfo is None or body.timestamp.utcoffset() is None:raise HTTPException(422,"Timestamp must include a timezone")
    if body.timestamp>NOW()+timedelta(minutes=5):raise HTTPException(422,"Future timestamp outside allowed clock skew")
    values=body.model_dump(); values["timestamp"]=body.timestamp.isoformat(); values["synthetic"]=False
    if any(v is not None and (not math.isfinite(v) or v<0) for k,v in values.items() if k not in ("timestamp","facility_id","sensor_id","synthetic")):raise HTTPException(422,"Readings must be finite non-negative values")
    if values.get("humidity_percent") is not None and values["humidity_percent"]>100:raise HTTPException(422,"Humidity must be at most 100 percent")
    if values.get("o2_percent") is not None and values["o2_percent"]>100:raise HTTPException(422,"Oxygen concentration must be at most 100 percent")
    assessment=process_reading(values)
    return {"reading":values,"assessment":assessment}
@app.post("/api/simulator/{scenario}")
def scenario(scenario:str,u=Depends(auth)):
    global SCENARIO
    if u.get("role")!="System Administrator":raise HTTPException(403,"Only system administrators may change global simulator scenarios")
    valid={"normal","extreme_heat","poor_ventilation","air_pollution","gas_leak","multiple_hazards"}
    if scenario not in valid:raise HTTPException(422,"Unknown scenario")
    SCENARIO=scenario
    if scenario in {"gas_leak","multiple_hazards"}:METEO.update({"wind_from_degrees":245.0,"wind_speed_m_s":4.6,"synthetic":True})
    else:METEO.update({"wind_from_degrees":270.0,"wind_speed_m_s":3.2,"synthetic":True})
    store.save_state("simulator",{"scenario":SCENARIO,"meteorology":METEO,"updated_at":NOW().isoformat()})
    evaluate_all()
    publish_event({"type":"scenario_changed","scenario":SCENARIO})
    return {"scenario":SCENARIO,"readings":READINGS,"assessments":[enriched_assessment(r) for r in READINGS],"alerts":ALERTS}
@app.get("/api/alerts")
def alerts(u=Depends(auth)):
    allowed=set(u.get("facility_ids",[]))
    return {"alerts":[a for a in ALERTS if a["facility_id"] in allowed],"audit":[e for e in AUDIT if any(a["id"]==e["alert_id"] and a["facility_id"] in allowed for a in ALERTS)]}
@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id:str,u=Depends(auth)):
    a=next((x for x in ALERTS if x["id"]==alert_id),None)
    if not a:raise HTTPException(404,"Alert not found")
    ensure_scope(u,a["facility_id"])
    if a["status"]=="OPEN":a["status"]="ACKNOWLEDGED";a["acknowledged_at"]=NOW().isoformat();store.save_alert(a);audit("ACKNOWLEDGED",a["id"],u["sub"])
    store.save_alert(a);return a
@app.post("/api/alerts/{alert_id}/interventions")
def intervention(alert_id:str,body:dict,u=Depends(auth)):
    a=next((x for x in ALERTS if x["id"]==alert_id),None)
    if not a:raise HTTPException(404,"Alert not found")
    ensure_scope(u,a["facility_id"])
    text=str(body.get("action",""))[:500].strip()
    if not text:raise HTTPException(422,"Action is required")
    item={"action":text,"actor":u["sub"],"timestamp":NOW().isoformat()};a["interventions"].append(item);store.save_alert(a);audit("INTERVENTION_RECORDED",a["id"],u["sub"]);return item
@app.post("/api/alerts/{alert_id}/resolve")
def resolve(alert_id:str,u=Depends(auth)):
    a=next((x for x in ALERTS if x["id"]==alert_id),None)
    if not a:raise HTTPException(404,"Alert not found")
    ensure_scope(u,a["facility_id"])
    a["status"]="RESOLVED";a["resolved_at"]=NOW().isoformat();store.save_alert(a);audit("RESOLVED",a["id"],u["sub"]);return a
def worker_view(allowed=None):
    allowed=set(allowed) if allowed is not None else {w["facility_id"] for w in WORKERS}
    tracked=exposure_summary([w for w in WORKERS if w["facility_id"] in allowed],[r for r in READING_HISTORY if r["facility_id"] in allowed],480)["workers"]
    items=[]
    for w in tracked:
        a=next((rank_for(r) for r in READINGS if r["facility_id"]==w["facility_id"] and r["facility_id"] in allowed),None)
        items.append({**w,"exposure_indicator":w["indicator"],"risk_level":a["risk_level"] if a else "UNKNOWN"})
    return items
@app.get("/api/workers")
def workers(u=Depends(auth)): return worker_view(u.get("facility_ids",[]))

@app.get("/api/exposures")
def exposures(window_minutes:int=480,sample_interval_minutes:int=5,u=Depends(auth)):
    if window_minutes<5 or window_minutes>10080:raise HTTPException(422,"window_minutes must be between 5 and 10080")
    if sample_interval_minutes<1 or sample_interval_minutes>60:raise HTTPException(422,"sample_interval_minutes must be between 1 and 60")
    authorized=[w for w in WORKERS if any(r["facility_id"]==w["facility_id"] for r in READINGS if r["facility_id"] in u.get("facility_ids",[]))]
    return exposure_summary(authorized,READING_HISTORY,window_minutes,sample_interval_minutes)

def community_intelligence(allowed=None):
    allowed=set(allowed) if allowed is not None else {f["id"] for f in FACILITIES}
    observations=[]
    for r in READINGS:
        if r["facility_id"] not in allowed:continue
        f=next(x for x in FACILITIES if x["id"]==r["facility_id"])
        observations.append({"facility_id":r["facility_id"],"facility":f["name"],"lat":f["lat"],"lon":f["lon"],"risk_level":rank_for(r)["risk_level"],"pm25_ug_m3":r.get("pm25_ug_m3"),"timestamp":r["timestamp"],"evidence_type":"MEASURED_SYNTHETIC_OBSERVATION" if not r.get("synthetic") else "SIMULATED_OBSERVATION","attribution":"No source attribution inferred"})
    chemical=next((r for r in READINGS if r["facility_id"]=="chemical-01" and r["facility_id"] in allowed),None)
    plume=None
    if chemical and (chemical.get("h2s_ppm",0)>=THRESHOLDS["h2s_ppm"]["high"] or chemical.get("co_ppm",0)>=THRESHOLDS["co_ppm"]["high"]):
        facility=next(f for f in FACILITIES if f["id"]=="chemical-01")
        plume=propagate(facility,chemical,METEO["wind_from_degrees"],METEO["wind_speed_m_s"],30)
    return {"observations":observations,"meteorology":{**METEO,"source":"SYNTHETIC_SCENARIO"},"modelled_dispersion":plume}
@app.get("/api/community")
def community(u=Depends(auth)):
    return community_intelligence(u.get("facility_ids",[]))
@app.post("/api/assistant")
def assistant(body:dict,u=Depends(auth)):
    q=str(body.get("question","")).lower(); assessments=[(next(f for f in FACILITIES if f["id"]==r["facility_id"]),enriched_assessment(r),r) for r in READINGS if r["facility_id"] in u.get("facility_ids",[])]
    if not assessments:raise HTTPException(403,"No facility is assigned to this account")
    target=next((x for x in assessments if x[0]["name"].lower() in q or x[0]["id"] in q),None)
    if "worker" in q or "exposure" in q:
        ws=workers(u); elevated=[w for w in ws if w["exposure_indicator"]=="ELEVATED"]
        answer=f"Observed: {len(elevated)} of {len(ws)} synthetic worker records show elevated time-weighted zone exposure. IDs: {', '.join(w['id'] for w in elevated) or 'none'}. This is an exposure-screening indicator, not a medical assessment."
    elif target:
        f,a,r=target; readings=", ".join(f"{x['parameter']} {x['value']} (demo threshold {x['threshold']})" for x in a["findings"]) or "no configured thresholds exceeded"
        ml=a["ml"]; rec=recommendations(a)
        answer=f"Observed at {f['name']} ({a['timestamp']}): deterministic assessment {a['risk_level']} with demo score {a['risk_score']}/100. Contributing readings: {readings}. Model: anomaly={ml['anomaly']}, anomaly score={ml['anomaly_score']}, classifier={ml['classifier_risk']} (synthetic-data confidence {ml['classifier_confidence']}). {rec['actions'][0]} {ml['confidence_limit']} "
    else:
        f,a,r=max(assessments,key=lambda x:x[1]["risk_score"])
        answer=f"Current highest assessed facility: {f['name']} at {a['risk_level']} (demo score {a['risk_score']}/100), timestamp {a['timestamp']}. {a['explanation']} ML anomaly={a['ml']['anomaly']} (score {a['ml']['anomaly_score']}). {a['recommendation']}"
    return {"answer":answer,"mode":"grounded-local-workflow","workflow":"hazard-analysis","synthetic":True,"facts":[{"facility_id":f["id"],"timestamp":a["timestamp"],"risk_level":a["risk_level"],"ml":a["ml"]} for f,a,r in ([target] if target else assessments)],"recommendations":recommendations((target or max(assessments,key=lambda x:x[1]["risk_score"]))[1])}
@app.get("/api/reports/daily.csv")
def report(u=Depends(auth)):
    out=io.StringIO();w=csv.writer(out);w.writerow(["facility","timestamp","synthetic","risk_level","risk_score","ml_anomaly","ml_anomaly_score","ml_classifier_risk","temperature_c","humidity_percent","wbgt_c","pm25_ug_m3","pm10_ug_m3","co_ppm","co2_ppm","h2s_ppm","o2_percent"])
    for r in READINGS:
        if r["facility_id"] not in u.get("facility_ids",[]):continue
        a=enriched_assessment(r); f=next(x for x in FACILITIES if x["id"]==r["facility_id"]);w.writerow([f["name"],r["timestamp"],r["synthetic"],a["risk_level"],a["risk_score"],a["ml"]["anomaly"],a["ml"]["anomaly_score"],a["ml"]["classifier_risk"],*[r.get(k,"") for k in ["temperature_c","humidity_percent","wbgt_c","pm25_ug_m3","pm10_ug_m3","co_ppm","co2_ppm","h2s_ppm","o2_percent"]]])
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=industrialshield-daily.csv"})
@app.get("/api/health")
def health():return {"status":"ok","mode":"persistent-sqlalchemy","database":store.DATABASE_URL.split(":")[0],"ml_model":ML.metrics["dataset"],"synthetic":True}

@app.get("/api/model/metrics")
def model_metrics(u=Depends(auth)):return ML.metrics

@app.get("/api/workflows")
def workflows(u=Depends(auth)):return WORKFLOW_REGISTRY

@app.get("/api/alerts/{alert_id}/report")
def incident_report(alert_id:str,u=Depends(auth)):
    a=next((x for x in ALERTS if x["id"]==alert_id),None)
    if not a:raise HTTPException(404,"Alert not found")
    ensure_scope(u,a["facility_id"])
    return incident_summary(a,AUDIT)

@app.on_event("startup")
async def remember_loop():
    global EVENT_LOOP
    EVENT_LOOP=asyncio.get_running_loop()

@app.websocket("/ws/stream")
async def websocket_stream(ws:WebSocket):
    await ws.accept()
    try:
        hello=await asyncio.wait_for(ws.receive_json(),timeout=5)
        u=auth(f"Bearer {hello.get('token','')}")
    except (HTTPException,asyncio.TimeoutError,ValueError):
        await ws.close(code=4401);return
    queue=asyncio.Queue(maxsize=100);redis_url=os.getenv("REDIS_URL")
    try:
        await ws.send_json({"type":"connected","at":NOW().isoformat(),"role":u["role"]})
        if redis_url:
            client=Redis.from_url(redis_url,decode_responses=True);pubsub=client.pubsub();await pubsub.subscribe("industrialshield.events")
            try:
                while True:
                    message=await pubsub.get_message(ignore_subscribe_messages=True,timeout=1)
                    if message:await ws.send_text(message["data"])
                    else:await asyncio.sleep(.05)
            finally:await pubsub.aclose();await client.aclose()
        else:
            WS_CLIENTS.add(queue)
            while True: await ws.send_json(await queue.get())
    except (WebSocketDisconnect,RuntimeError,asyncio.CancelledError):pass
    finally:WS_CLIENTS.discard(queue)
