"""Grounded, auditable local workflows; no LLM is allowed to alter safety rules."""
from datetime import datetime, timezone

REGISTRY={
 "hazard-analysis":{"version":"1","stages":["read latest platform readings","run deterministic safety rules","run Isolation Forest and classifier","return evidence and trend features"],"data_scope":"authorized platform readings and model outputs"},
 "safety-recommendations":{"version":"1","stages":["read rule findings and alert severity","map findings to approved response playbook","return supervisor action list"],"data_scope":"specific assessment; recommendations are advisory"},
 "incident-summary":{"version":"1","stages":["load alert lifecycle and audit events","order events by timestamp","summarize observations, model results, acknowledgement and intervention"],"data_scope":"alert and durable audit records"},
 "agentfoundry":{"status":"UNAVAILABLE","reason":"No AgentFoundry connector, invited account, or project credentials are available in this runtime; no external workflow has been deployed."}
}

def recommendations(assessment:dict):
    actions=[]
    for finding in assessment.get("findings",[]):
        p=finding["parameter"]
        if p in {"h2s_ppm","co_ppm","o2_percent"}: actions.append("Follow the facility gas release / oxygen deficiency response plan, evacuate or isolate the affected zone as required, and verify with approved instruments.")
        elif p in {"temperature_c","wbgt_c"}: actions.append("Reduce heat exposure, provide recovery and hydration according to site procedure, and have the safety officer reassess conditions.")
        elif p in {"pm25_ug_m3","pm10_ug_m3"}: actions.append("Check dust controls and respiratory protection requirements; verify with a calibrated particulate monitor.")
        elif p=="co2_ppm": actions.append("Inspect ventilation and confirm with a second instrument; follow site procedures.")
    if not actions:actions.append("Review current readings, sensor health, and data freshness before concluding the zone is safe.")
    return {"workflow":"safety-recommendations","actions":list(dict.fromkeys(actions)),"authority":"advisory only; deterministic safety assessment remains authoritative"}

def incident_summary(alert:dict,audit:list[dict]):
    events=[e for e in audit if e.get("alert_id")==alert["id"]]
    events.sort(key=lambda x:x.get("timestamp",""))
    return {"workflow":"incident-summary","alert_id":alert["id"],"facility_id":alert["facility_id"],"severity":alert["severity"],"status":alert["status"],"created_at":alert["created_at"],"sensor_reading":alert.get("sensor_reading"),"assessment":alert["assessment"],"recommendations":recommendations(alert["assessment"]),"timeline":events,"interventions":alert.get("interventions",[]),"generated_at":datetime.now(timezone.utc).isoformat(),"synthetic":alert["assessment"].get("synthetic",False)}
