"""MGRS <-> WGS84 conversion (no external GIS dependency).

Implements the standard MGRS/USNG construction on top of the WGS84 UTM
projection (Snyder series). It is intentionally dependency-free so the
capability works on the existing plain-PostgreSQL host, where PostGIS is not
installed.

Accuracy: UTM series are good to well under a metre in the normal latitude
range; MGRS at 5-digit precision resolves to 1 m. Round-trip tests assert
sub-2 m agreement for reference points.

Grid letters follow the standard scheme:

* easting 100 km letters cycle in three 8-letter sets by zone;
* northing 100 km letters use a 20-letter alphabet offset by 5 squares in
  even-numbered zones.

The two reference points asserted in the tests (Eiffel Tower -> ``31UDQ`` and
Honolulu -> ``4QFJ``) pin both the set selection and the 1-based easting index,
which is the detail most hand-rolled implementations get wrong.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# WGS84 ellipsoid
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_K0 = 0.9996

_BAND_LETTERS = "CDEFGHJKLMNPQRSTUVWX"  # 20 bands, 8 deg each (X covers 12)
_EASTING_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_NORTHING_LETTERS = "ABCDEFGHJKLMNPQRSTUV"  # 20 letters, I and O omitted

_MGRS_RE = re.compile(
    r"^(?P<zone>\d{1,2})(?P<band>[C-HJ-NP-X])"
    r"(?P<e100k>[A-HJ-NP-Z])(?P<n100k>[A-HJ-NP-V])"
    r"(?P<digits>\d*)$",
    re.IGNORECASE,
)


class MGRSError(ValueError):
    """Raised when a coordinate or MGRS string is invalid."""


@dataclass(frozen=True)
class UTM:
    zone: int
    northern: bool
    easting: float
    northing: float
    band: str

    @property
    def hemisphere(self) -> str:
        return "N" if self.northern else "S"


def _zone_for(lat: float, lon: float) -> int:
    zone = int((lon + 180.0) / 6.0) + 1
    # Norway: zone 32 is widened to cover 3-12E / 56-64N.
    if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
        zone = 32
    # Svalbard: zones 31/33/35/37 replace the normal grid.
    if 72.0 <= lat <= 84.0:
        if 0.0 <= lon < 9.0:
            zone = 31
        elif 9.0 <= lon < 21.0:
            zone = 33
        elif 21.0 <= lon < 33.0:
            zone = 35
        elif 33.0 <= lon < 42.0:
            zone = 37
    return zone


def _band_for(lat: float) -> str:
    if lat < -80.0 or lat > 84.0:
        raise MGRSError("Latitude outside the UTM/MGRS range (-80 to 84 degrees).")
    index = int((lat + 80.0) / 8.0)
    if index >= len(_BAND_LETTERS):
        index = len(_BAND_LETTERS) - 1
    return _BAND_LETTERS[index]


def latlon_to_utm(lat: float, lon: float) -> UTM:
    """Project WGS84 lat/lon into UTM."""
    if not (-90.0 <= lat <= 90.0):
        raise MGRSError("Latitude must be between -90 and 90 degrees.")
    if not (-180.0 <= lon <= 180.0):
        raise MGRSError("Longitude must be between -180 and 180 degrees.")

    zone = _zone_for(lat, lon)
    band = _band_for(lat)
    lon_origin = (zone - 1) * 6 - 180 + 3
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon_origin_rad = math.radians(lon_origin)

    ep2 = _E2 / (1 - _E2)
    n = _A / math.sqrt(1 - _E2 * math.sin(lat_rad) ** 2)
    t = math.tan(lat_rad) ** 2
    c = ep2 * math.cos(lat_rad) ** 2
    a = math.cos(lat_rad) * (lon_rad - lon_origin_rad)

    m = _A * (
        (1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256) * lat_rad
        - (3 * _E2 / 8 + 3 * _E2**2 / 32 + 45 * _E2**3 / 1024) * math.sin(2 * lat_rad)
        + (15 * _E2**2 / 256 + 45 * _E2**3 / 1024) * math.sin(4 * lat_rad)
        - (35 * _E2**3 / 3072) * math.sin(6 * lat_rad)
    )

    easting = _K0 * n * (
        a
        + (1 - t + c) * a**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120
    ) + 500000.0

    northing = _K0 * (
        m
        + n
        * math.tan(lat_rad)
        * (
            a**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720
        )
    )
    northern = lat >= 0
    if not northern:
        northing += 10000000.0

    return UTM(zone=zone, northern=northern, easting=easting, northing=northing, band=band)


def utm_to_latlon(
    zone: int, northern: bool, easting: float, northing: float
) -> tuple[float, float]:
    """Inverse UTM projection back to WGS84 lat/lon."""
    if not (1 <= zone <= 60):
        raise MGRSError("UTM zone must be between 1 and 60.")

    x = easting - 500000.0
    y = northing if northern else northing - 10000000.0

    ep2 = _E2 / (1 - _E2)
    m = y / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256))

    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )

    c1 = ep2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * _K0)

    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(phi1)

    lon_origin = math.radians((zone - 1) * 6 - 180 + 3)
    return math.degrees(lat), math.degrees(lon + lon_origin)


def to_mgrs(lat: float, lon: float, precision: int = 5) -> str:
    """Convert WGS84 lat/lon to an MGRS string.

    ``precision`` is the number of digits per axis (1-5), i.e. 5 -> 1 m.
    """
    if not 1 <= precision <= 5:
        raise MGRSError("MGRS precision must be between 1 and 5 digits.")

    utm = latlon_to_utm(lat, lon)

    e100k = int(utm.easting // 100000)
    n100k = int(utm.northing % 2000000) // 100000

    set_index = (utm.zone - 1) % 3
    # 1-based index into the 8-letter easting set.
    if not 1 <= e100k <= 8:
        raise MGRSError("Easting produced an invalid 100 km square.")
    east_letter = _EASTING_SETS[set_index][e100k - 1]

    offset = 5 if utm.zone % 2 == 0 else 0
    north_letter = _NORTHING_LETTERS[(n100k + offset) % 20]

    divisor = 10 ** (5 - precision)
    east_digits = int(utm.easting % 100000) // divisor
    north_digits = int(utm.northing % 100000) // divisor

    return (
        f"{utm.zone:02d}{utm.band}{east_letter}{north_letter}"
        f"{east_digits:0{precision}d}{north_digits:0{precision}d}"
    )


def parse_mgrs(value: str) -> tuple[int, str, str, str, int, int, int]:
    """Parse an MGRS string into its components.

    Returns ``(zone, band, e100k, n100k, easting_digits, northing_digits, precision)``.
    """
    cleaned = re.sub(r"\s+", "", value or "").upper()
    if not cleaned:
        raise MGRSError("MGRS coordinate is empty.")

    match = _MGRS_RE.match(cleaned)
    if not match:
        raise MGRSError(
            "Invalid MGRS coordinate. Expected format: ZZBXXdddddddd "
            "(e.g. 51PUB1234567890)."
        )

    digits = match.group("digits")
    if len(digits) % 2 != 0:
        raise MGRSError("MGRS numeric part must contain an even number of digits.")
    precision = len(digits) // 2
    if precision > 5:
        raise MGRSError("MGRS supports at most 5 digits per axis.")

    east_digits = int(digits[:precision]) if precision else 0
    north_digits = int(digits[precision:]) if precision else 0

    zone = int(match.group("zone"))
    if not 1 <= zone <= 60:
        raise MGRSError("MGRS zone must be between 01 and 60.")

    return (
        zone,
        match.group("band"),
        match.group("e100k"),
        match.group("n100k"),
        east_digits,
        north_digits,
        precision,
    )


def from_mgrs(value: str) -> tuple[float, float]:
    """Convert an MGRS string to WGS84 ``(lat, lon)``."""
    zone, band, e100k, n100k, east_digits, north_digits, precision = parse_mgrs(value)

    set_index = (zone - 1) % 3
    easting_set = _EASTING_SETS[set_index]
    if e100k not in easting_set:
        raise MGRSError(
            f"Easting letter {e100k!r} is not valid for UTM zone {zone}."
        )
    e100k_index = easting_set.index(e100k) + 1  # 1-based

    offset = 5 if zone % 2 == 0 else 0
    north_index = (_NORTHING_LETTERS.index(n100k) - offset) % 20
    if n100k not in _NORTHING_LETTERS:
        raise MGRSError(f"Northing letter {n100k!r} is not a valid MGRS letter.")

    scale = 10 ** (5 - precision) if precision else 100000
    easting = e100k_index * 100000 + east_digits * scale
    northing_base = north_index * 100000 + north_digits * scale

    northern = band >= "N"

    # The northing repeats every 2,000,000 m; recover the cycle that puts the
    # point in the latitude band of the supplied letter.
    best: tuple[float, float] | None = None
    best_delta = 1e9
    for cycle in range(0, 5):
        candidate_northing = northing_base + cycle * 2000000
        if northern:
            if candidate_northing > 10000000:
                continue
        else:
            candidate_northing += 10000000
        lat, lon = utm_to_latlon(zone, northern, easting, candidate_northing)
        if -80 <= lat <= 84 and _band_for(lat) == band:
            delta = abs(lat)
            if delta < best_delta:
                best, best_delta = (lat, lon), delta
    if best is None:
        # Fall back to the first plausible cycle rather than failing outright.
        candidate = northing_base + (10000000 if not northern else 0)
        best = utm_to_latlon(zone, northern, easting, candidate)

    lat, lon = best
    if lon > 180:
        lon -= 360
    if lon < -180:
        lon += 360
    return lat, lon


def validate_mgrs(value: str) -> str:
    """Normalise and validate an MGRS string, returning the canonical form."""
    from_mgrs(value)  # raises MGRSError when unusable
    return re.sub(r"\s+", "", value).upper()
