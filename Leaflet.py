import psycopg2
import json
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

conn_params = {
    "dbname": "ragis",
    "user": "",
    "password": "",
    "host": "",
    "sslmode": "require"
}

query_polygons_public = """
SELECT k.ogc_fid, ST_AsGeoJSON(ST_Transform(k.geom, 4326)) as geojson
FROM krustijan_work.reki_sofia_plan r
JOIN krustijan_work.cadaster_all k ON ST_Intersects(ST_Transform(r.wkb_geometry, 7801), k.geom)
WHERE k.proptype IN ('Държавна публична', 'Общинска публична')
"""

query_polygons_other = """
SELECT k.ogc_fid, ST_AsGeoJSON(ST_Transform(k.geom, 4326)) as geojson
FROM krustijan_work.reki_sofia_plan r
JOIN krustijan_work.cadaster_all k ON ST_Intersects(ST_Transform(r.wkb_geometry, 7801), k.geom)
WHERE k.proptype NOT IN ('Държавна публична', 'Общинска публична')
"""

query_lines = """
SELECT r.id, 
  ST_AsGeoJSON(
    ST_Transform(
      ST_Difference(
        ST_Transform(r.wkb_geometry, 7801),
        COALESCE(
          ST_Union(k.geom),
          ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 7801)
        )
      ), 4326
    )
  ) AS geojson
FROM 
  krustijan_work.reki_sofia_plan r
LEFT JOIN
  krustijan_work.cadaster_all k
  ON ST_Intersects(
       ST_Transform(r.wkb_geometry, 7801),
       k.geom
     )
GROUP BY 
  r.id, r.wkb_geometry;
"""

query_lines2 = """
SELECT r.id, ST_AsGeoJSON(ST_Transform(r.wkb_geometry, 4326)) as geojson
FROM krustijan_work.reki_sofia_plan r
JOIN krustijan_work.cadaster_all k ON ST_Intersects(ST_Transform(r.wkb_geometry, 7801), k.geom)
"""

def fetch_geojson(query):
    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            features = []
            for row in cur.fetchall():
                features.append({
                    "type": "Feature",
                    "geometry": json.loads(row[1]),
                    "properties": {"id": row[0]}
                })
            return {
                "type": "FeatureCollection",
                "features": features
            }

@app.route("/")
def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Leaflet Map</title>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
        <style>
            #map { height: 100vh; }
        </style>
    </head>
    <body>
        <div id="map"></div>
        <script>
            let highlightedRiverLayer = null;
            let connectionLayer = null;
            var map = L.map('map').setView([0, 0], 2);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19
            }).addTo(map);

            Promise.all([
                fetch('/lines-extra').then(res => res.json()),
                fetch('/lines').then(res => res.json()),
                fetch('/polygons/public').then(res => res.json()),
                fetch('/polygons/other').then(res => res.json())
            ])
            .then(([linesExtraData, linesData, publicPolygons, otherPolygons]) => {
                L.geoJSON(otherPolygons, {style: {color: 'red', fillOpacity: 0.3}}).addTo(map);
                L.geoJSON(publicPolygons, {style: {color: 'orange', fillOpacity: 0.3}}).addTo(map);

                L.geoJSON(linesData, {style: {color: 'blue'}}).addTo(map);

                L.geoJSON(linesExtraData, {style: {color: 'black'}}).addTo(map);
            });

            map.on('click', function (e) {
                const latlng = e.latlng;
                const pointGeoJSON = {
                    type: "Point",
                    coordinates: [latlng.lng, latlng.lat]  
                };

                fetch('/nearest-line', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(pointGeoJSON)
                })
                .then(res => res.json())
                .then(data => {
                    if (highlightedRiverLayer) {
                        map.removeLayer(highlightedRiverLayer);
                        highlightedRiverLayer = null;
                    }
                    if (connectionLayer) {
                        map.removeLayer(connectionLayer);
                        connectionLayer = null;
                    }

                    if (data.riverLine) {
                        highlightedRiverLayer = L.geoJSON(data.riverLine, {
                            style: {color: 'green', weight: 5}
                        }).addTo(map);
            
                        if (data.connectionLine) {
                            connectionLayer = L.geoJSON(data.connectionLine, {
                                style: {color: 'yellow', weight: 3, dashArray: '5,5'}
                            }).addTo(map);
                        }

                        const group = new L.FeatureGroup();
                        if (highlightedRiverLayer) group.addLayer(highlightedRiverLayer);
                        if (connectionLayer) group.addLayer(connectionLayer);
                        map.fitBounds(group.getBounds(), {padding: [50, 50]});

                        alert(`Distance to nearest line: ${data.distance.toFixed(2)} meters`);
                    }
                })
                .catch(err => console.error('Error:', err));
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route("/polygons/public")
def polygons_public():
    return jsonify(fetch_geojson(query_polygons_public))

@app.route("/polygons/other")
def polygons_other():
    return jsonify(fetch_geojson(query_polygons_other))

@app.route("/lines")
def lines():
    return jsonify(fetch_geojson(query_lines))

@app.route("/lines-extra")
def lines_extra():
    return jsonify(fetch_geojson(query_lines2))


@app.route("/nearest-line", methods=["POST"])
def nearest_line_distance():
    data = request.get_json()
    lon, lat = data["coordinates"]

    query = f"""
    WITH rivers AS (
        SELECT r.id, ST_Difference(
            ST_Transform(r.wkb_geometry, 7801),
            COALESCE(
                (SELECT ST_Union(c.geom)
                FROM krustijan_work.cadaster_all c
                WHERE c.proptype NOT IN ('Държавна публична', 'Общинска публична')
                AND ST_Intersects(c.geom, ST_Transform(r.wkb_geometry, 7801))
            ),
            ST_GeomFromText('GEOMETRYCOLLECTION EMPTY', 7801))
        ) AS wkb_geometry
    FROM krustijan_work.reki_sofia_plan r
    ), 
    nearest AS (
        SELECT 
            ST_Transform(wkb_geometry, 4326) as wkb_geometry,
            ST_Distance(
                ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 3857),
                ST_Transform(wkb_geometry, 3857)
            ) AS distance,
            ST_ClosestPoint(
                ST_Transform(wkb_geometry, 3857),
                ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 3857)
            ) AS closest_point
        FROM rivers 
        WHERE NOT ST_IsEmpty(wkb_geometry)
        ORDER BY distance
        LIMIT 1
    )
    SELECT 
        distance,
        ST_AsGeoJSON(ST_Transform(wkb_geometry, 4326)) AS river_geojson,
        ST_AsGeoJSON(ST_Transform(
            ST_MakeLine(
                ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 3857),
                closest_point
            ), 4326)) AS connection_geojson
    FROM nearest
    """
    with psycopg2.connect(**conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (lon, lat, lon, lat, lon, lat))
            result = cur.fetchone()
            if result: 
                distance = result[0]
                river_geojson = json.loads(result[1]) if result[1] else None
                connection_geojson = json.loads(result[2]) if result[2] else None
                
                response = {
                    "distance": distance,
                    "riverLine": {
                        "type": "Feature",
                        "geometry": river_geojson,
                        "properties": {}
                    } if river_geojson else None,
                    "connectionLine": {
                        "type": "Feature",
                        "geometry": connection_geojson,
                        "properties": {}
                    } if connection_geojson else None
                }
                return jsonify(response)
            return jsonify({"distance": None, "riverLine": None, "connectionLine": None})

if __name__ == "__main__":
    app.run(debug=True)