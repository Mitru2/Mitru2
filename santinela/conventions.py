"""Stratul de normalizare semantica: conventiile pe care se bazeaza decodoarele, cu sursa fiecareia.

Fiecare conventie are un statut:

* ``verificat`` - citit direct din sursa oficiala (fisier .proto, descriptor), cu amprenta;
* ``raport`` - din raportul de interoperabilitate din 13.11.2026, care citeaza o sursa
  (standardul sau o reproducere publica); nu am putut citi sursa primara aici;
* ``presupus`` - ales de noi, fara sursa; trebuie confirmat.

Codul foloseste valorile de aici (nu constante imprastiate), iar testele verifica ca
decodoarele respecta exact aceste conventii. ``python -m santinela.cli --conventions``
le afiseaza.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Convention:
    key: str
    value: str
    status: str  # verificat | raport | presupus
    source: str


_C = [
    # ---------------- SAPIENT (BSI Flex 335 v2.0)
    Convention("sapient.location.x", "longitudine pentru LAT_LNG_*; est pentru UTM_M (y = latitudine / nord)",
               "verificat", "location.proto (Dstl, sha256 3a7d3c00…): 'X-coordinate (normally longitude)'"),
    Convention("sapient.location.coordinate_system", "1 LAT_LNG_DEG_M, 2 LAT_LNG_RAD_M, 5 UTM_M (3, 4 rezervate)",
               "verificat", "location.proto, enum LocationCoordinateSystem"),
    Convention("sapient.location.datum", "1 WGS84_E: z = inaltime elipsoidala; 2 WGS84_G: z fata de geoid (MSL)",
               "verificat", "location.proto, enum LocationDatum"),
    Convention("sapient.utm.zone", "zona din Registration (DetectionDefinition.location_type.zone) sau Location.utm_zone; "
               "format oficial '30N' (litera = emisfera); litere de banda (ex. '35T') acceptate; 'S' e ambiguu",
               "verificat", "registration.proto: 'provide the UTM zone in this field (e.g. 30N)'; location.proto utm_zone"),
    Convention("sapient.range_bearing.units", "1 grade/m, 2 radiani/m, 3 grade/km, 4 radiani/km",
               "verificat", "range_bearing.proto, enum RangeBearingCoordinateSystem"),
    Convention("sapient.range_bearing.north", "1 nord adevarat, 2 magnetic (+ declinatia nodului), 3 grila UTM "
               "(+ convergenta meridianelor), 4 capul platformei (+ capul nodului)",
               "verificat", "range_bearing.proto, enum RangeBearingDatum"),
    Convention("sapient.velocity", "ENUVelocity (est, nord, sus); unitatile din Registration: 1 m/s, 2 km/h",
               "verificat", "velocity.proto + registration.proto (VelocityType.enu_velocity_units)"),
    Convention("sapient.range_rate", "nu exista camp standard pentru viteza radiala (Doppler) in v2.0",
               "verificat", "detection_report.proto (campurile 1-23)"),
    Convention("sapient.validation", "is_mandatory / is_uuid / is_ulid din (field_options) si (oneof_options)",
               "verificat", "proto_options.proto, ValidationOptions"),
    Convention("sapient.framing", "fiecare mesaj precedat de lungimea lui pe 4 octeti, little-endian; TCP obligatoriu",
               "raport", "BSI Flex 335 v2.0 §4.1-4.2 (citat in raport)"),
    Convention("sapient.covariance", "covarianta circula ca perechi text in object_info: type='covariance_xx' etc.",
               "raport", "exemplu NCIA, oct. 2026 (citat in raport); nestandardizat"),
    # ---------------- STANAG 4607
    Convention("stanag4607.d32_7", "viteza pe linia de vizare in cm/s, corectata pentru miscarea platformei, "
               "pozitiva la departarea de senzor",
               "raport", "AEDP-7, reprodus in raportul DTIC RADARSAT-2 (citat in raport)"),
    Convention("stanag4607.classification", "1 senile, 2 roti, 3 aripa rotativa, 4 aripa fixa, 6 maritim, 7 baliza, "
               "8 amfibiu, 9 persoana, 10 vehicul, 11 animal, 12/13 retur multiplu mare, 126 altul, 127 necunoscut; "
               "+128 simulat; NU exista clasa UAS",
               "raport", "disectorul Wireshark packet-stanag4607.c (citat in raport)"),
    Convention("stanag4607.mdv", "D31 in decimetri/s", "raport", "DTIC ADA479333 (citat in raport)"),
    # ---------------- ASTERIX
    Convention("asterix.editions", "editii curente: CAT048 1.32, CAT062 1.21, CAT021 2.7, CAT034 1.29",
               "raport", "EUROCONTROL (citat in raport)"),
    Convention("asterix.uap_tables", "tabelele UAP din asterix.py au fost scrise dupa specificatia publica, "
               "fara textul editiei curente la indemana", "presupus", "de comparat cu specificatiile EUROCONTROL"),
    # ---------------- NFFI / FFT
    Convention("nffi.standard", "NFFI a devenit ADatP-36 (STANAG 5527); FMN Spiral 5 cere FFI MTF (APP-11), IP1 preferat",
               "raport", "citat in raport"),
    # ---------------- TAK / CoT
    Convention("tak.protocol_v1", "mesh UDP: bf 01 bf + TakMessage; stream TCP: bf + varint(lungime) + TakMessage; "
               "timpi in ms (uint64)", "verificat", "descriptorii takproto 3.0.1 (MIT), citit ca date"),
    Convention("cot.ce", "ce = raza erorii circulare in metri (folosim sqrt(lambda_max) a covariantei de pozitie, ~1 sigma); "
               "9999999 = necunoscut", "presupus", "schema CoT MITRE nu fixeaza nivelul de incredere"),
    # ---------------- simbologie
    Convention("app6.sidc", "SIDC de 20 de cifre (MIL-STD-2525D/APP-6D); UAV = set 01 (aer), entitate 110300",
               "presupus", "MIL-STD-2525D (din memorie); de confirmat pe APP-6 nationala"),
]

CONVENTIONS = {c.key: c for c in _C}


def get(key: str) -> Convention:
    return CONVENTIONS[key]


def report() -> list[dict]:
    return [{"cheie": c.key, "valoare": c.value, "statut": c.status, "sursa": c.source} for c in _C]
