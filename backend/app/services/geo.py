"""Geometry helpers (GeoJSON <-> PostGIS)."""

import json
from typing import Any

from geoalchemy2.elements import WKBElement, WKTElement

__all__ = ["WKTElement", "lonlat", "multipolygon_from_geojson", "point_wkt"]
from geoalchemy2.shape import to_shape
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.validation import make_valid


def point_wkt(lon: float, lat: float) -> WKTElement:
    return WKTElement(f"POINT({lon} {lat})", srid=4326)


def lonlat(geom: WKBElement | Any) -> tuple[float, float]:
    p = to_shape(geom)
    assert isinstance(p, Point)
    return p.x, p.y


def multipolygon_from_geojson(geometry: dict[str, Any] | str) -> WKTElement:
    """Accept a GeoJSON Polygon/MultiPolygon, repair it if needed, return a MULTIPOLYGON."""
    if isinstance(geometry, str):
        geometry = json.loads(geometry)
    geom = shape(geometry)
    if not geom.is_valid:
        geom = make_valid(geom)
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])
    elif not isinstance(geom, MultiPolygon):
        polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
        if not polys:
            raise ValueError("geometry must be a Polygon or MultiPolygon")
        geom = MultiPolygon(polys)
    return WKTElement(geom.wkt, srid=4326)
