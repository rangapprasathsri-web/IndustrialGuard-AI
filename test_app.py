import unittest
import os
import tempfile
from datetime import datetime, timedelta, timezone

# Keep integration-test writes isolated from the developer's local demo DB and
# any running server process.
_TEST_DB = tempfile.TemporaryDirectory(prefix="industrialshield-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TEST_DB.name, 'test.db')}"

import app
from community import propagate
from exposure import summarize
from intelligence import ML, forecast

class IntelligenceUnitTests(unittest.TestCase):
    def test_trained_models_evaluate_holdout_synthetic_labels(self):
        self.assertEqual(ML.metrics["dataset"], "synthetic-generated-v1")
        self.assertTrue(ML.metrics["synthetic_only"])
        self.assertGreater(ML.metrics["anomaly_eval_samples"], 100)
        self.assertGreaterEqual(ML.metrics["anomaly_precision"], 0)
        self.assertLessEqual(ML.metrics["anomaly_precision"], 1)
        normal=ML.assess({"temperature_c":28,"humidity_percent":55,"pm25_ug_m3":12,"pm10_ug_m3":35,"co_ppm":2,"co2_ppm":700,"h2s_ppm":.1,"o2_percent":20.8})
        abnormal=ML.assess({"temperature_c":29,"humidity_percent":50,"pm25_ug_m3":18,"pm10_ug_m3":40,"co_ppm":155,"co2_ppm":800,"h2s_ppm":26,"o2_percent":16.5})
        self.assertFalse(normal["anomaly"])
        self.assertTrue(abnormal["anomaly"])
        self.assertGreater(abnormal["anomaly_score"],normal["anomaly_score"])
        self.assertTrue(abnormal["explainability"])

    def test_forecast_projects_threshold_crossing_from_time_series(self):
        now=datetime.now(timezone.utc)
        history=[{"timestamp":(now-timedelta(hours=1)).isoformat(),"temperature_c":33.8},{"timestamp":now.isoformat(),"temperature_c":34.8}]
        result=forecast(history,30)
        pred=next(x for x in result["predictions"] if x["parameter"]=="temperature_c")
        self.assertTrue(pred["crossing_projected"])
        self.assertEqual(pred["hazard_type"],"PREDICTED")
        self.assertEqual(result["confidence"],"NOT_CALIBRATED")

    def test_exposure_and_plume_are_labelled_estimates(self):
        now=datetime.now(timezone.utc).isoformat()
        workers=[{"id":"W-1","department":"Process","facility_id":"chemical-01","zone":"Tank farm"}]
        readings=[{"facility_id":"chemical-01","timestamp":now,"h2s_ppm":15,"synthetic":True} for _ in range(3)]
        exposure=summarize(workers,readings,480)
        self.assertEqual(exposure["workers"][0]["estimated_exposure_minutes"],15)
        self.assertEqual(exposure["workers"][0]["indicator"],"ELEVATED")
        f=next(x for x in app.FACILITIES if x["id"]=="chemical-01")
        plume=propagate(f,{"h2s_ppm":25,"co_ppm":100,"o2_percent":17},245,4.6)
        self.assertEqual(plume["evidence_type"],"MODELLED_DISPERSION_FROM_SYNTHETIC_METEOROLOGY")
        self.assertEqual(len(plume["zones"]),3)
        self.assertGreater(plume["downwind_distance_m"],0)

    def test_missing_sensor_fields_are_not_reported_as_safe(self):
        result=app.rank_for({"facility_id":"chemical-01","timestamp":datetime.now(timezone.utc).isoformat(),"synthetic":False})
        self.assertEqual(result["risk_level"],"LOW")
        self.assertIn("do not interpret as safe",result["explanation"])
        self.assertEqual(result["data_quality"],"INCOMPLETE")
        ml=ML.assess({"h2s_ppm":24})
        self.assertEqual(ml["data_quality"],"IMPUTED_DEFAULTS · validate before interpretation")
        self.assertIn("temperature_c",ml["missing_features"])

class EndToEndDemoTests(unittest.TestCase):
    def test_gas_leak_alert_agent_intervention_and_report(self):
        login=app.login({"email":app._demo_email,"password":os.getenv("INDUSTRIALSHIELD_DEMO_PASSWORD","ChangeMe-For-Deployment")})
        user=app.auth("Bearer "+login["access_token"])
        for alert in app.alerts(user)["alerts"]:
            if alert["facility_id"]=="chemical-01" and alert["status"]!="RESOLVED":app.resolve(alert["id"],user)
        normal=app.scenario("normal",user)
        self.assertTrue(all(x["risk_level"]=="NORMAL" for x in normal["assessments"]))
        result=app.scenario("gas_leak",user)
        chemical=next(a for a in result["assessments"] if a["facility_id"]=="chemical-01")
        self.assertEqual(chemical["risk_level"],"CRITICAL")
        self.assertTrue(chemical["ml"]["anomaly"])
        alert=next(a for a in result["alerts"] if a["facility_id"]=="chemical-01" and a["status"]=="OPEN")
        self.assertIsNotNone(app.community(user)["modelled_dispersion"])
        answer=app.assistant({"question":"Why is Meridian Chemical Processing at risk?"},user)
        self.assertIn("anomaly=True",answer["answer"])
        app.acknowledge(alert["id"],user)
        app.intervention(alert["id"],{"action":"Evacuated scenario zone and verified gas monitor"},user)
        report=app.incident_report(alert["id"],user)
        self.assertGreaterEqual(len(report["timeline"]),3)
        self.assertTrue(any(e["event"]=="INTERVENTION_RECORDED" for e in report["timeline"]))
        self.assertGreaterEqual(len(app.store.load_readings()),3)
        persisted=app.store.load_state("simulator")
        self.assertEqual(persisted["scenario"],"gas_leak")
        self.assertEqual(persisted["meteorology"]["wind_from_degrees"],245.0)

if __name__ == "__main__": unittest.main()
