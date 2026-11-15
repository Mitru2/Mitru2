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
    Convention("sapient.validation", "is_mandatory / is_uuid / is_ulid din (field_options) si (oneof_options), plus "
               "regulile suplimentare ale validatorului Dstl (icd_version, dependent_nodes, concurrent_tasks, "
               "follow_object_id, prediction_location, derived_detection, increderi 0-1)", "verificat",
               "proto_options.proto + SapientServices/Data/Validation (Test Harness Dstl, 6a2999d); pe cei 231 de "
               "vectori oficiali: 38/38 valide fara abateri, 191/193 invalide detectate (2 vectori depasiti)"),
    Convention("sapient.utm_latlon_quirk", "lat/lon pusi in campuri UTM_M (mostrele Dstl, cu latitudinea pe x): "
               "ordinea aleasa dupa plauzibilitate si apropierea de nod/zona de operare, cu nota de abatere",
               "verificat", "toate cele 203 locatii din vectorii Dstl au aceasta forma (x~51.2, y~-1.8, zona 30U)"),
    Convention("sapient.framing", "fiecare mesaj precedat de lungimea lui pe 4 octeti, little-endian; TCP obligatoriu",
               "raport", "BSI Flex 335 v2.0 §4.1-4.2 (citat in raport)"),
    Convention("sapient.covariance", "covarianta circula ca perechi text in object_info: type='covariance_xx' etc.",
               "raport", "exemplu NCIA, oct. 2026 (citat in raport); nestandardizat"),
    # ---------------- STANAG 4607
    Convention("stanag4607.d32_7", "viteza pe linia de vizare in cm/s, corectata pentru miscarea platformei, "
               "pozitiva la departarea de senzor",
               "raport", "AEDP-7, reprodus in raportul DTIC RADARSAT-2 (citat in raport)"),
    Convention("stanag4607.classification", "1 senile, 2 roti, 3 aripa rotativa, 4 aripa fixa, 5 rotator stationar, "
               "6 maritim, 7 baliza, 8 amfibiu, 9 persoana, 10 vehicul, 11 animal, 12/13 retur multiplu mare, "
               "126 altul, 127 necunoscut, +128 simulat, 143 dispozitiv de marcare; NU exista clasa UAS",
               "verificat", "disectorul Wireshark packet-stanag4607.c (stanag4607_target_vals)"),
    Convention("stanag4607.codings", "SA32 LSB 45/2^30 grd; BA32 360/2^32; SA16/BA16 90/2^14; B16 = v/128; "
               "masca D2...D32.18 de la bitul cel mai semnificativ; P7: +128 exercitiu, +1 simulat, +2 sintetizat; "
               "geoidul in segmentul job (octetul 67)", "verificat",
               "disectorul Wireshark packet-stanag4607.c; verificarea a gasit si corectat SA32 si B16"),
    Convention("stanag4607.mdv", "D31 in decimetri/s", "raport", "DTIC ADA479333 (citat in raport)"),
    # ---------------- ASTERIX
    Convention("asterix.editions", "editii curente: CAT048 1.32, CAT062 1.21, CAT021 2.7, CAT034 1.29, CAT015 1.2, "
               "CAT205 1.0, CAT247 1.3", "verificat",
               "asterix-specs (transcrierea structurata EUROCONTROL), revizia 5cf64b63; coincide cu raportul"),
    Convention("asterix.uap_tables", "UAP-urile si structura articolelor sunt generate din asterix-specs "
               "(santinela/asterix_spec.py); interpretoarele scrise de mana sunt testate camp cu camp contra lor",
               "verificat", "asterix-specs revizia 5cf64b63 (BSD-3); verificarea a gasit si corectat 3 erori"),
    Convention("asterix.domains", "valorile in afara domeniului din specificatie (ex. latitudine > 90) refuza "
               "inregistrarea: semn de editie gresita sau date corupte", "verificat",
               "asterix-specs (restrictiile campurilor); 3 restrictii imposibile din sursa (I015/627, I015/631) ignorate"),
    Convention("asterix.real_traffic", "pe capturile reale Croatia Control: 1940/1940 campuri CAT034/048 cu aceiasi "
               "octeti ca decodorul de referinta, toate valorile verificate egale; CAT001/002 (incadrare ORADIS) si "
               "CAT062 identice; I001/200: referinta foloseste LSB 0,22 kt, specificatia 2^-14 NM/s",
               "verificat", "CroatiaControlLtd/asterix, commit 790bca7 (capturi + iesiri de referinta)"),
    # ---------------- NFFI / FFT
    Convention("nffi.standard", "NFFI a devenit ADatP-36 (STANAG 5527); FMN Spiral 5 cere FFI MTF (APP-11), IP1 preferat",
               "raport", "citat in raport"),
    # ---------------- TAK / CoT
    Convention("tak.protocol_v1", "mesh UDP: bf 01 bf + TakMessage; stream TCP: bf + varint(lungime) + TakMessage; "
               "timpi in ms (uint64)", "verificat", "descriptorii takproto 3.0.1 (MIT), citit ca date"),
    Convention("cot.ce", "ce = raza erorii circulare in metri (folosim sqrt(lambda_max) a covariantei de pozitie, ~1 sigma); "
               "9999999 = necunoscut", "presupus", "schema CoT MITRE nu fixeaza nivelul de incredere"),
    # ---------------- geodezie
    Convention("wmm2025", "declinatia magnetica din World Magnetic Model 2025 (valabil 2025.0-2030.0); azimut "
               "adevarat = azimut magnetic + D", "verificat",
               "coeficientii NOAA (identici in 2 redistribuiri); valorile oficiale de test: 12 puncte (D/I la 0,005 grd, "
               "X/Y/Z la 0,001 nT) si 100 de puncte (sub 0,05 nT)"),
    Convention("geoid.egm96", "altitudinile din tracker sunt fata de nivelul marii: H = h_HAE - N(EGM96); grila "
               "regionala 41-50 N, 18-33 E, pas 5'", "verificat",
               "grila globala EGM96 5' (pachetul egm96, MIT) reproduce cele 6 puncte de test NGA la sub 5 mm"),
    # ---------------- MISB (STANAG 4609)
    Convention("misb.st0601", "mapari liniare domeniu -> interval pe tag; indicator de eroare = valoarea minima "
               "cu semn; suma de control = suma cuvintelor de 16 biti; tagurile 43/44 = x2 pixeli",
               "verificat", "66 de exemple ST 0601.11 sectiunea 8 (prin testele klvdata, MIT)"),
    Convention("misb.st0903", "pachet de locatie (tag 17): lat/lon IMAPB 4 o., HAE IMAPB(-900,19000,2), sigma "
               "IMAPB(0,650,2), rho IMAPB(-1,1,2); versiunea < 4 = codare liniara veche; decalaje IMAPB(+-19.2,3)",
               "verificat", "exemplele din anexa ST 0903 (prin testele jmisb, MIT)"),
    # ---------------- Remote ID: autentificare (ASTM F3411 / DRIP)
    Convention("remoteid.auth_page", "octetul 1: tipul autentificarii (4 biti sus) si pagina (4 biti jos); pagina 0: "
               "LastPageIndex, Length, Timestamp (uint32 LE, secunde de la 2019-01-01) si 17 octeti de date; "
               "paginile 1-15: 23 de octeti", "verificat",
               "opendroneid-core-c (opendroneid.h, ODID_Auth_encoded_page_zero/non_zero; Apache-2.0), citit ca date"),
    Convention("drip.formats", "SAM 0x01 Link (aviz parinte: VNB, VNA, DET copil, cheie copil, DET parinte, "
               "semnatura), 0x02 Wrapper (max. 4 mesaje), 0x03 Manifest (hash-uri de 8 o.), 0x04 Frame; semnatura "
               "Ed25519 peste VNB|VNA|dovada|DET; hash de mesaj = cSHAKE128(mesaj, 64, '', 'Remote ID Auth Hash'); "
               "FEC = paritate XOR pe paginile de 23 o.", "verificat",
               "RFC 9575 sectiunile 3-5; exemplul B.2.1 verificat complet (semnaturi, 8 hash-uri, FEC octet cu octet)"),
    Convention("drip.det_hash", "DET = 2001:30::/28 | RAA 14 b | HDA 14 b | suita 8 b | cSHAKE128(primii 8 o. din DET "
               "| cheia Ed25519 de 32 o., 64, '', Context ID 00B5A69C...); HOST_ID fara eticheta curbei",
               "verificat", "RFC 9374 sectiunea 3.5; varianta reproduce DET-ul din RFC 9575 B.2.1 (cu eticheta, nu)"),
    Convention("drip.example_deviations", "exemplul RFC 9575 B.2.1 se abate de la text: Link cu SAM 0x04 (nu 0x01), "
               "hash-ul manifestului curent include hash-ul Link-ului, hash-ul Link-ului e peste aviz (fara SAM), "
               "VNB/VNA in epoca Unix; codul urmeaza textul si accepta variantele exemplului, cu nota; epoca e "
               "configurabila (drip_epoch)", "verificat", "comparatie text - exemplu in RFC 9575"),
    # ---------------- simbologie
    Convention("app6.sidc", "SIDC de 20 de cifre (MIL-STD-2525D/APP-6D); UAV = set 01 (aer), entitate 110300",
               "presupus", "MIL-STD-2525D (din memorie); de confirmat pe APP-6 nationala"),
]

CONVENTIONS = {c.key: c for c in _C}


def get(key: str) -> Convention:
    return CONVENTIONS[key]


def report() -> list[dict]:
    return [{"cheie": c.key, "valoare": c.value, "statut": c.status, "sursa": c.source} for c in _C]
