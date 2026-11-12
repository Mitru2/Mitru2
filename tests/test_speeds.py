"""Viteze pe clase: portile din tabelul planului v2."""
import pytest

from santinela.speeds import SpeedTable, load_speeds_from_drones_js, parse_speed_text, speed_table_from_site

PLAN_GATES = {  # m/s, coloana "Poarta v_max" din plan
    "FPV": 43, "Supercam S350": 43, "ZALA 421-16": 47, "Orlan-10": 54, "Gerbera": 58,
    "Geran-2": 67, "Shahed-136": 67, "Geran-3": 123, "Geran-4": 181, "Banderol": 235, "Dan-T": 235,
}


@pytest.mark.parametrize("cls,gate", PLAN_GATES.items())
def test_gate_matches_plan(cls, gate):
    assert round(SpeedTable().vmax(cls)) == gate


def test_lancet_cruise_and_dive():
    st = SpeedTable()
    assert round(st.vmax("Lancet-3")) == 40
    assert round(st.vmax("Lancet-3", dive=True)) == 108
    assert round(st.vmax_any("Lancet-3")) == 108


@pytest.mark.parametrize("cls", ["Molniya-2", "Italmas", "V2U", "KUB-BLA", None, "necunoscut"])
def test_unknown_class_envelope(cls):
    assert SpeedTable().vmax(cls) == 235.0


def test_parse_speed_text():
    assert parse_speed_text("~80–110 km/h croaziera; ~300 in picaj") == {"cruise": 110.0, "dive": 300.0}
    assert parse_speed_text("60-120 km/h") == {"cruise": 120.0}


def test_load_from_drones_js(tmp_path):
    js = tmp_path / "drones.js"
    js.write_text("""
    export const drones = [
      { id: "geran-2", name: "Geran-2", ttx: { range: "2000 km", speed: "~185 km/h" } },
      { id: "lancet-3", ttx: { speed: '~80–110 km/h; ~300 în picaj' } },
    ];""", encoding="utf-8")
    loaded = load_speeds_from_drones_js(js)
    assert loaded["geran-2"] == {"cruise": 185.0}
    assert loaded["lancet-3"] == {"cruise": 110.0, "dive": 300.0}
    st = speed_table_from_site(js)
    assert round(st.vmax("geran-2")) == 67
