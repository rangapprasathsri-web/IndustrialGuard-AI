"""Synthetic-data-trained ML and transparent short-horizon temporal analytics."""
from __future__ import annotations
from datetime import datetime
from typing import Any
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

FEATURES = ["temperature_c", "humidity_percent", "pm25_ug_m3", "pm10_ug_m3", "co_ppm", "co2_ppm", "h2s_ppm", "o2_percent"]
CLASSES = ["NORMAL", "LOW", "MODERATE", "HIGH", "CRITICAL"]
RNG = np.random.default_rng(20261101)

def synthetic_training_data(n_normal: int = 6000, n_events: int = 2400):
    """Construct a reproducible labelled synthetic training/evaluation set."""
    normal = np.column_stack([
        RNG.normal(28, 2.2, n_normal), RNG.normal(55, 11, n_normal),
        RNG.lognormal(2.2, .5, n_normal), RNG.lognormal(3.2, .45, n_normal),
        RNG.lognormal(.5, .55, n_normal), RNG.normal(700, 150, n_normal),
        RNG.lognormal(-2, .6, n_normal), RNG.normal(20.8, .25, n_normal),
    ])
    normal = np.clip(normal, [10, 5, 0, 0, 0, 300, 0, 15], [50, 100, 80, 250, 30, 3000, 8, 22])
    events = normal[RNG.integers(0, len(normal), n_events)].copy()
    anomaly = np.ones(n_events, dtype=int)
    kinds = RNG.integers(0, 5, n_events)
    for i, k in enumerate(kinds):
        if k == 0: events[i, 0] += RNG.uniform(8, 18); events[i, 1] += RNG.uniform(15, 35)
        elif k == 1: events[i, 2] *= RNG.uniform(5, 15); events[i, 3] *= RNG.uniform(3, 9)
        elif k == 2: events[i, 4] += RNG.uniform(40, 110); events[i, 6] += RNG.uniform(8, 35)
        elif k == 3: events[i, 5] += RNG.uniform(1800, 5000); events[i, 7] -= RNG.uniform(1.5, 3.5)
        else: events[i, 0] += RNG.uniform(5, 12); events[i, 2] += RNG.uniform(50, 120); events[i, 4] += RNG.uniform(20, 60)
    X = np.vstack([normal, events])
    y = np.concatenate([np.zeros(n_normal, dtype=int), anomaly])
    return normal, X, y

def rule_class(row: np.ndarray) -> str:
    t, rh, pm25, pm10, co, co2, h2s, o2 = row
    sev = 0
    for value, limits, inverse in [
        (t,(32,35,40),False),(pm25,(35,75,150),False),(pm10,(100,250,500),False),
        (co,(25,50,100),False),(co2,(1000,2000,5000),False),(h2s,(5,10,20),False),(o2,(19.5,19,18),True),
    ]:
        s = (4 if value < limits[2] else 3 if value < limits[1] else 2 if value < limits[0] else 0) if inverse else (4 if value >= limits[2] else 3 if value >= limits[1] else 2 if value >= limits[0] else 0)
        sev=max(sev,s)
    if sev==0 and (t>34 or pm25>60 or co>35 or co2>1500 or h2s>3): sev=1
    return CLASSES[sev]

class IndustrialIntelligence:
    def __init__(self):
        normal, X, y_anomaly = synthetic_training_data()
        normal_train, normal_eval = train_test_split(normal, test_size=.25, random_state=42)
        event_eval=train_test_split(X[y_anomaly==1],test_size=.25,random_state=42)[1]
        Xb=np.vstack([normal_eval,event_eval]);yb=np.concatenate([np.zeros(len(normal_eval),dtype=int),np.ones(len(event_eval),dtype=int)])
        self.detector = IsolationForest(n_estimators=240, contamination=.04, random_state=42, n_jobs=1).fit(normal_train)
        y_class = np.array([rule_class(r) for r in X])
        Xtr, Xte, ytr, yte = train_test_split(X, y_class, test_size=.25, random_state=42, stratify=y_class)
        self.scaler = StandardScaler().fit(Xtr)
        self.classifier = RandomForestClassifier(n_estimators=260, min_samples_leaf=2, class_weight="balanced", random_state=42, n_jobs=1).fit(self.scaler.transform(Xtr), ytr)
        self.baseline_mean = normal_train.mean(axis=0); self.baseline_std = normal_train.std(axis=0).clip(1e-3)
        binary_pred = self.detector.predict(Xb)==-1
        tp=int(np.sum(binary_pred & (yb==1))); fp=int(np.sum(binary_pred & (yb==0))); fn=int(np.sum(~binary_pred & (yb==1)))
        self.metrics = {
            "dataset":"synthetic-generated-v1", "synthetic_only":True,
            "anomaly_eval_samples":len(yb), "anomaly_precision":round(tp/max(1,tp+fp),4), "anomaly_recall":round(tp/max(1,tp+fn),4),
            "classifier_eval_samples":len(yte), "classifier_accuracy":round(float(accuracy_score(yte,self.classifier.predict(self.scaler.transform(Xte)))),4),
            "classifier_macro_f1":round(float(f1_score(yte,self.classifier.predict(self.scaler.transform(Xte)),average="macro",zero_division=0)),4),
            "label_note":"Evaluation labels are synthetic rules; metrics do not predict field performance or regulatory validity.",
        }

    @staticmethod
    def vector(reading: dict[str,Any]):
        defaults=[28,55,15,35,2,700,.15,20.8]
        return np.array([[float(reading.get(f,d)) if reading.get(f) is not None else d for f,d in zip(FEATURES,defaults)]], dtype=float)

    def assess(self, reading: dict[str,Any]):
        missing=[f for f in FEATURES if not isinstance(reading.get(f),(int,float))]
        x=self.vector(reading); anomaly_score=float(-self.detector.decision_function(x)[0]); outlier=self.detector.predict(x)[0]==-1
        row=x[0]; transformed=self.scaler.transform(x); pred=self.classifier.predict(transformed)[0]
        confidence=float(self.classifier.predict_proba(transformed)[0].max())
        z=np.abs((row-self.baseline_mean)/self.baseline_std); order=np.argsort(z)[::-1][:3]
        return {
            "model_version":"isolation-forest-rf-synthetic-v1", "anomaly":bool(outlier), "anomaly_score":round(anomaly_score,5),
            "classifier_risk":str(pred), "classifier_confidence":round(confidence,4),
            "confidence_limit":"Classifier confidence is agreement within a synthetic rule-labelled training set, not a real-world probability.",
            "explainability":[{"feature":FEATURES[i],"reading":round(float(row[i]),3),"baseline_mean":round(float(self.baseline_mean[i]),3),"baseline_std":round(float(self.baseline_std[i]),3),"deviation_in_std":round(float(z[i]),2)} for i in order],
            "source":"ML_MODEL", "synthetic_training_data":True,"missing_features":missing,"data_quality":"COMPLETE" if not missing else "IMPUTED_DEFAULTS · validate before interpretation",
        }

def temporal_features(history: list[dict[str,Any]], window: int=6):
    rows=history[-window:]; out={}
    rate_caps={"temperature_c":1.5,"humidity_percent":12.0,"pm25_ug_m3":60.0,"pm10_ug_m3":180.0,"co_ppm":45.0,"co2_ppm":700.0,"h2s_ppm":5.0,"o2_percent":1.0}
    for feature in FEATURES:
        valid=[r for r in rows if isinstance(r.get(feature),(int,float))]
        vals=[float(r[feature]) for r in valid]
        if not vals:continue
        try: ts=[datetime.fromisoformat(r["timestamp"].replace("Z","+00:00")).timestamp() for r in valid]
        except (ValueError,KeyError): ts=[]
        # The simulator represents a five-minute sample interval, even when scenario clicks happen rapidly.
        hours=max((ts[-1]-ts[0])/3600,5/60) if len(ts)>1 else 1/12
        raw_slope=(vals[-1]-vals[0])/hours; cap=rate_caps[feature]
        slope=float(np.clip(raw_slope,-cap,cap))
        out[feature]={"mean":round(float(np.mean(vals)),3),"max":round(float(np.max(vals)),3),"min":round(float(np.min(vals)),3),"delta":round(vals[-1]-vals[0],3),"slope_per_hour":round(slope,3),"uncapped_slope_per_hour":round(raw_slope,3),"rate_capped":abs(raw_slope)>cap}
    return out

def forecast(history: list[dict[str,Any]], horizon_minutes: int=30):
    feats=temporal_features(history)
    if not history:return {"status":"INSUFFICIENT_HISTORY","horizon_minutes":horizon_minutes,"confidence":"NOT_CALIBRATED","predictions":[]}
    latest=history[-1]; thresholds={"temperature_c":35,"wbgt_c":30,"pm25_ug_m3":75,"pm10_ug_m3":250,"co_ppm":50,"co2_ppm":2000,"h2s_ppm":10,"o2_percent":19}
    preds=[]
    for key,limit in thresholds.items():
        d=feats.get(key)
        if not d:continue
        value=float(latest.get(key,0)); slope=d["slope_per_hour"]; projected=value+slope*horizon_minutes/60
        crossing=(projected<limit and value>=limit) if key=="o2_percent" else (projected>=limit and value<limit)
        preds.append({"parameter":key,"current":round(value,3),"predicted":round(projected,3),"trend_per_hour":slope,"demo_screening_threshold":limit,"crossing_projected":bool(crossing),"hazard_type":"PREDICTED","confidence":"LOW · uncalibrated linear extrapolation"})
    return {"status":"FORECAST_AVAILABLE" if len(history)>=2 else "SINGLE_POINT_NO_TREND","horizon_minutes":horizon_minutes,"confidence":"NOT_CALIBRATED","method":"recent linear trend extrapolation with per-feature rate caps; not a validated predictive model","predictions":preds,"features":feats}

ML = IndustrialIntelligence()
