"""Simplified Gaussian-inspired plume geometry for synthetic demonstration only."""
from math import cos, radians, sin

def propagate(facility:dict, reading:dict, wind_from_degrees:float, wind_speed_m_s:float, horizon_minutes:int=30):
    downwind=(wind_from_degrees+180)%360
    distance_m=min(15000,max(0,wind_speed_m_s*horizon_minutes*60))
    lat0,lon0=facility["lat"],facility["lon"]
    lat_rad=radians(lat0)
    points=[]
    # Nested scenario polygons widen with downwind distance; rough local tangent-plane conversion.
    for fraction,width in ((.35,180),(.68,380),(1.0,650)):
        dist=distance_m*fraction; ang=radians(downwind)
        cx=dist*sin(ang); cy=dist*cos(ang)
        ring=[]
        for j in range(17):
            theta=2*3.141592653589793*j/16
            x=cx+width*fraction*cos(theta); y=cy+width*fraction*sin(theta)
            ring.append([round(lon0+x/(111320*cos(lat_rad)),6),round(lat0+y/110540,6)])
        points.append({"distance_m":round(dist),"estimated_width_m":round(width*fraction*2),"geometry_type":"MODELLED_SCENARIO_POLYGON","coordinates":ring})
    return {"facility_id":facility["id"],"wind_from_degrees":wind_from_degrees,"wind_speed_m_s":wind_speed_m_s,"plume_bearing_degrees":downwind,"horizon_minutes":horizon_minutes,"downwind_distance_m":round(distance_m),"zones":points,"evidence_type":"MODELLED_DISPERSION_FROM_SYNTHETIC_METEOROLOGY","observed_pollution":{"h2s_ppm":reading.get("h2s_ppm"),"co_ppm":reading.get("co_ppm"),"o2_percent":reading.get("o2_percent")},"source_attribution":"Scenario facility is a configured simulation origin; no real-world source attribution is made.","model_limitations":"Simple advection geometry; no terrain, stability class, deposition, chemistry, validated dispersion coefficients, or measured community stations."}
