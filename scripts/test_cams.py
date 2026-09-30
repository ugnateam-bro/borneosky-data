#!/usr/bin/env python3
"""Checks for the CAMS smoke and wind pipeline (borneosky/cams.py) with a made-up NetCDF file shaped like the
Atmosphere Data Store's. No network, no R2:
    python scripts/test_cams.py
The real request's variable names could not be tried here (no key); the reader finds the wind variables by their
usual short names, then by standard or long name, and these checks cover each."""

import json
import sys
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import netCDF4
import numpy as np

warnings.filterwarnings("ignore", category=DeprecationWarning)   # netCDF4 vs NumPy 2.5 noise

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from borneosky import cams  # noqa: E402

LAT = [7.2, 6.8, 6.4]              # descending, as CAMS delivers it
LON = [108.8, 109.2, 109.6, 110.0]
LEAD = [0.0, 3.0, 6.0]
TMP = Path(tempfile.mkdtemp())


def make_nc(name, *, wind=True, lat=LAT, u_scale=1.0, wind_names=("u10", "v10"), attrs=False, drop_v=False):
    path = TMP / name
    with netCDF4.Dataset(path, "w") as d:
        d.createDimension("forecast_period", len(LEAD)); d.createDimension("forecast_reference_time", 1)
        d.createDimension("latitude", len(lat)); d.createDimension("longitude", len(LON))
        for n, vals in (("forecast_period", LEAD), ("latitude", lat), ("longitude", LON)):
            d.createVariable(n, "f8", (n,))[:] = vals
        dims = ("forecast_period", "forecast_reference_time", "latitude", "longitude")
        shape = (len(LEAD), 1, len(lat), len(LON))
        base = np.arange(np.prod(shape), dtype=float).reshape(shape)
        d.createVariable("pm2p5", "f8", dims)[:] = (base + 5) * 1e-9          # kg/m3 -> 5.. ug/m3
        d.createVariable("aod550", "f8", dims)[:] = base / 100
        if wind:
            u = d.createVariable(wind_names[0], "f8", dims); u[:] = (base % 7 - 3) * u_scale        # m/s east
            if attrs:
                u.standard_name = "eastward_wind"
            if not drop_v:
                v = d.createVariable(wind_names[1], "f8", dims); v[:] = -(base % 5) + 1.25          # m/s north
                if attrs:
                    v.long_name = "10 metre V wind component"
    return path


class Store:
    def __init__(self):
        self.d = {}
        self.puts = []

    def put(self, key, obj, gzipped=False):
        self.d[key] = json.loads(json.dumps(obj))
        self.puts.append(key)

    def get(self, key):
        return self.d.get(key)


def fake_retrieve(smoke_path, wind_path=None, wind_error=None, calls=None):
    def f(run, workdir, variables=None):
        if calls is not None:
            calls.append("wind" if variables else "smoke")
        if variables:
            assert variables == cams.WIND_VARS
            if wind_error:
                raise cams.CamsError(wind_error)
            return wind_path
        return smoke_path
    return f


def run_with(store, retrieve, **kw):
    cams._retrieve = retrieve
    return cams.run(store.put, store.get, log=lambda *_: None, **kw)


def read_grid():
    return cams._read(make_nc("smoke.nc", wind=False))


def check_wind_is_read_by_name_standard_name_or_long_name():
    for kw in ({}, {"wind_names": ("10u", "10v")}, {"wind_names": ("eastw", "northw"), "attrs": True}):
        w = cams._read_wind(make_nc("w.nc", **kw))
        assert w["u"].shape == (3, 3, 4) and w["v"].shape == (3, 3, 4), kw
        assert abs(w["u"][0, 0, 1] - (1 % 7 - 3)) < 1e-9, "values are m/s as they stand"
    try:
        cams._read_wind(make_nc("nov.nc", drop_v=True)); raise AssertionError("a file without v must be refused")
    except cams.CamsError as e:
        assert "v10" in str(e)


def check_wind_is_refused_when_it_is_not_a_plausible_match_for_the_smoke_grid():
    grid = read_grid(); run = datetime(2026, 9, 30, tzinfo=timezone.utc); now = run + timedelta(hours=11)
    try:
        cams._read_wind(make_nc("fast.nc", u_scale=100.0)); raise AssertionError("300 m/s is not weather")
    except cams.CamsError as e:
        assert "m/s" in str(e)
    other = cams._read_wind(make_nc("other.nc", lat=[7.2, 6.8, 6.5]))
    try:
        cams.build_wind(other, grid, run, now); raise AssertionError("a different grid must be refused")
    except cams.CamsError as e:
        assert "differ" in str(e)
    ok = cams._read_wind(make_nc("ok.nc"))
    ok["lead"] = ok["lead"] + 1
    try:
        cams.build_wind(ok, grid, run, now); raise AssertionError("different steps must be refused")
    except cams.CamsError:
        pass


def check_wind_file_shape():
    grid = read_grid(); run = datetime(2026, 9, 30, tzinfo=timezone.utc); now = run + timedelta(hours=11)
    out = cams.build_wind(cams._read_wind(make_nc("ok.nc")), grid, run, now)
    smoke, _ = cams.build(grid, run, now)
    for k in ("lat", "lon", "times", "run_utc", "attribution"):
        assert out[k] == smoke[k], f"{k} is the smoke file's"
    assert "not measurements" in out["label"] and "Copernicus" in out["attribution"]
    assert len(out["u"]) == len(out["v"]) == len(out["times"]) == 3
    assert len(out["u"][0]) == len(LAT) * len(LON) and all(isinstance(x, int) for x in out["u"][0])
    raw = cams._read_wind(make_nc("ok.nc"))
    assert out["u"][0][1] == round(raw["u"][0, 0, 1] * 10) and out["v"][2][5] == round(raw["v"][2, 1, 1] * 10), "tenths of m/s, row-major"
    assert len(json.dumps(out)) < 4000


def check_first_run_writes_smoke_and_wind():
    st = Store(); calls = []
    res = run_with(st, fake_retrieve(make_nc("s.nc", wind=False), make_nc("w.nc"), calls=calls))
    assert res["status"] == "updated" and res["wind"]["status"] == "updated", res
    for key in ("smoke/grid.json", "smoke/towns.json", cams.META_KEY, cams.WIND_KEY, cams.WIND_META_KEY):
        assert key in st.d, key
    assert st.d[cams.WIND_KEY]["run_utc"] == st.d[cams.META_KEY]["run_utc"]
    assert calls == ["smoke", "wind"], "wind is a second request, after the smoke"
    assert st.puts.index(cams.META_KEY) < st.puts.index(cams.WIND_KEY), "smoke is written first"


def check_a_failing_wind_request_never_touches_the_smoke_forecast():
    st = Store()
    res = run_with(st, fake_retrieve(make_nc("s.nc", wind=False), wind_error="ADS request failed: no such variable"))
    assert res["status"] == "updated" and res["wind"]["status"] == "failed" and "no such variable" in res["wind"]["error"]
    assert cams.WIND_KEY not in st.d and "smoke/grid.json" in st.d and cams.META_KEY in st.d
    # the last good wind file is kept: a later failure does not remove or overwrite it
    st.d[cams.WIND_KEY] = {"run_utc": "2000-01-01T00:00:00Z"}
    res = run_with(st, fake_retrieve(make_nc("s.nc", wind=False), wind_error="again"), force=True)
    assert st.d[cams.WIND_KEY]["run_utc"] == "2000-01-01T00:00:00Z" and res["wind"]["status"] == "failed"


def check_a_wind_upload_error_is_contained_too():
    st = Store()
    real = st.put

    def put(key, obj, gzipped=False):
        if key == cams.WIND_KEY:
            sys_exit = SystemExit("R2 upload of smoke/wind.json failed")   # what the R2 helper does on an error
            raise sys_exit
        real(key, obj, gzipped)
    cams._retrieve = fake_retrieve(make_nc("s.nc", wind=False), make_nc("w.nc"))
    res = cams.run(put, st.get, log=lambda *_: None)
    assert res["status"] == "updated" and res["wind"]["status"] == "failed" and "smoke/grid.json" in st.d


def check_failed_wind_is_retried_after_three_hours_not_every_run():
    st = Store(); calls = []
    smoke = make_nc("s.nc", wind=False)
    run_with(st, fake_retrieve(smoke, wind_error="down", calls=calls))
    assert st.d[cams.WIND_META_KEY]["tried_run"] == st.d[cams.META_KEY]["run_utc"]
    calls.clear()
    res = run_with(st, fake_retrieve(smoke, make_nc("w.nc"), calls=calls))
    assert res["status"] == "up_to_date" and res["wind"]["status"] == "waiting" and calls == [], "no request inside the wait"
    st.d[cams.WIND_META_KEY]["tried_utc"] = cams._iso(datetime.now(timezone.utc) - timedelta(hours=4))
    res = run_with(st, fake_retrieve(smoke, make_nc("w.nc"), calls=calls))
    assert res["status"] == "up_to_date" and res["wind"]["status"] == "updated" and calls == ["wind"], res
    assert st.d[cams.WIND_KEY]["lat"] == st.d["smoke/grid.json"]["lat"], "built on the published smoke grid"
    res = run_with(st, fake_retrieve(smoke, make_nc("w.nc"), calls=calls))
    assert res["wind"]["status"] == "up_to_date"


def check_when_the_smoke_request_fails_no_wind_is_asked_for():
    st = Store(); calls = []

    def boom(run, workdir, variables=None):
        calls.append("wind" if variables else "smoke")
        raise cams.CamsError("ADS down")
    res = run_with(st, boom)
    assert res["status"] == "failed" and calls == ["smoke"] and "wind" not in res


CHECKS = [v for k, v in dict(globals()).items() if k.startswith("check_")]

if __name__ == "__main__":
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok    {fn.__name__[6:]}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__[6:]}: {e}")
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    sys.exit(1 if failed else 0)
