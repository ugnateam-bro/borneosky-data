"""Copernicus CAMS global smoke forecast → R2.

CAMS runs twice a day (00Z, 12Z). We request 0–120 h ahead in 3-hour steps
for the Borneo box and write:

  smoke/grid.json    PM2.5 and aerosol optical depth on the 0.4° grid,
                     per step (the map layer; pre-gzipped)
  smoke/towns.json   the same interpolated to every registry town, plus
                     daily mean/max in each town's local time
  smoke/wind.json    10 m wind (u east, v north, m/s x 10) on the same grid and
                     steps, for the map's wind layer. Fetched with its own request
                     after the smoke files are written: if it fails, the smoke
                     forecast is unaffected and the last good wind file stays.

This is model output. It is labelled as a forecast and must never be shown
as a reading or converted into an official index (API/PSI/ISPU).
"""

import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import geo
from .config import BBOX, require

ADS_URL = "https://ads.atmosphere.copernicus.eu/api"
DATASET = "cams-global-atmospheric-composition-forecasts"
META_KEY = "smoke/_meta.json"
WIND_KEY = "smoke/wind.json"
WIND_META_KEY = "smoke/_wind_meta.json"
WIND_VARS = ["10m_u_component_of_wind", "10m_v_component_of_wind"]
# After a failed wind request for a run, wait this long before asking again (the job runs hourly).
WIND_RETRY_AFTER = timedelta(hours=3)
# Faster than any real 10 m wind at the surface: anything beyond is a broken file, not weather.
MAX_WIND = 120.0

LEAD_HOURS = list(range(0, 121, 3))
RUN_HOURS = (0, 12)
# A run is requested once it is this old; earlier requests just queue and fail.
AVAILABLE_AFTER = timedelta(hours=10)
# The job reports failure only when the newest run we hold is older than this.
STALE_AFTER = timedelta(hours=36)

LABEL = ("Smoke forecast from the CAMS global atmospheric model. Modelled "
         "values, not measurements, and not an official air-quality index.")
WIND_LABEL = ("Wind forecast from the CAMS global atmospheric model, 10 m above the ground. "
              "Modelled values, not measurements.")
ATTRIBUTION = ("Generated using Copernicus Atmosphere Monitoring Service information "
               "{year}. Neither the European Commission nor ECMWF is responsible "
               "for any use that may be made of the information it contains.")


class CamsError(Exception):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def latest_run(now: datetime) -> datetime:
    """Most recent 00Z/12Z run that should already be published."""
    t = now - AVAILABLE_AFTER
    base = t.replace(minute=0, second=0, microsecond=0)
    hour = max(h for h in RUN_HOURS if h <= base.hour)
    return base.replace(hour=hour)


def _retrieve(run: datetime, workdir: Path, variables: list[str] | None = None) -> Path:
    import cdsapi  # imported here so the rest of the pipeline doesn't need it

    client = cdsapi.Client(url=ADS_URL, key=require("ADS_API_KEY"),
                           quiet=True, progress=False)
    west, south, east, north = BBOX
    request = {
        "variable": variables or ["particulate_matter_2.5um", "total_aerosol_optical_depth_550nm"],
        "date": [f"{run:%Y-%m-%d}/{run:%Y-%m-%d}"],
        "time": [f"{run:%H}:00"],
        "leadtime_hour": [str(h) for h in LEAD_HOURS],
        "type": ["forecast"],
        "data_format": "netcdf_zip",
        "area": [north, west, south, east],
    }
    target = workdir / "cams.zip"
    try:
        client.retrieve(DATASET, request, str(target))
    except Exception as e:  # cdsapi raises plain HTTPError/Exception subclasses
        msg = str(e).splitlines()[-1][:200] if str(e) else type(e).__name__
        raise CamsError(f"ADS request for {run:%Y-%m-%d %HZ} failed: {msg}") from None

    with zipfile.ZipFile(target) as z:
        names = [n for n in z.namelist() if n.endswith(".nc")]
        if not names:
            raise CamsError("ADS returned no NetCDF file")
        z.extract(names[0], workdir)
    return workdir / names[0]


def _read(path: Path) -> dict:
    import netCDF4

    with netCDF4.Dataset(path) as d:
        lat = np.asarray(d["latitude"][:], dtype=float)
        lon = np.asarray(d["longitude"][:], dtype=float)
        lead = np.asarray(d["forecast_period"][:], dtype=float)
        # (step, run, lat, lon) → (step, lat, lon)
        pm = np.ma.filled(d["pm2p5"][:, 0].astype(float), np.nan) * 1e9  # kg/m³ → µg/m³
        aod = np.ma.filled(d["aod550"][:, 0].astype(float), np.nan)
    if pm.shape != (len(lead), len(lat), len(lon)):
        raise CamsError(f"unexpected grid shape {pm.shape}")
    if not np.isfinite(pm).any():
        raise CamsError("PM2.5 grid is empty")
    return {"lat": lat, "lon": lon, "lead": lead, "pm25": pm, "aod": aod}


def _find_var(d, names, standard_name, long_hint):
    """A variable of the file by its usual short names, else by standard or long name."""
    for n in names:
        if n in d.variables:
            return d.variables[n]
    for v in d.variables.values():
        if getattr(v, "standard_name", "") == standard_name or long_hint in getattr(v, "long_name", "").lower():
            return v
    raise CamsError(f"wind file has no {names[0]} variable")


def _read_wind(path: Path) -> dict:
    """10 m wind from the wind request's file: u (east) and v (north), m/s, as (step, lat, lon)."""
    import netCDF4

    with netCDF4.Dataset(path) as d:
        lat = np.asarray(d["latitude"][:], dtype=float)
        lon = np.asarray(d["longitude"][:], dtype=float)
        lead = np.asarray(d["forecast_period"][:], dtype=float)
        out = {}
        for key, names, std, hint in (("u", ("u10", "10u"), "eastward_wind", "10 metre u"),
                                      ("v", ("v10", "10v"), "northward_wind", "10 metre v")):
            var = _find_var(d, names, std, hint)
            if tuple(var.dimensions[-2:]) != ("latitude", "longitude"):
                raise CamsError(f"unexpected wind dimensions {var.dimensions}")
            a = np.ma.filled(var[:].astype(float), np.nan)
            try:
                out[key] = a.reshape(len(lead), len(lat), len(lon))   # (step, [run of 1], lat, lon)
            except ValueError:
                raise CamsError(f"unexpected wind grid shape {a.shape}") from None
    for k in ("u", "v"):
        if not np.isfinite(out[k]).any():
            raise CamsError(f"wind {k} grid is empty")
        if np.nanmax(np.abs(out[k])) > MAX_WIND:
            raise CamsError(f"wind {k} has values beyond {MAX_WIND:.0f} m/s")
    return {"lat": lat, "lon": lon, "lead": lead, **out}


def build_wind(wind: dict, grid: dict, run: datetime, now: datetime) -> dict:
    """smoke/wind.json: the wind on exactly the smoke grid and steps, so the map can use one time slider."""
    if (wind["lat"].shape != grid["lat"].shape or wind["lon"].shape != grid["lon"].shape
            or not np.allclose(wind["lat"], grid["lat"]) or not np.allclose(wind["lon"], grid["lon"])
            or wind["lead"].shape != grid["lead"].shape or not np.allclose(wind["lead"], grid["lead"])):
        raise CamsError("wind grid or steps differ from the smoke grid")
    times = [run + timedelta(hours=float(h)) for h in grid["lead"]]
    return {
        "generated_utc": _iso(now),
        "run_utc": _iso(run),
        "label": WIND_LABEL,
        "attribution": ATTRIBUTION.format(year=run.year),
        "units": {"u": "eastward wind, m/s x 10 (surface, modelled)", "v": "northward wind, m/s x 10 (surface, modelled)"},
        "times": [_iso(t) for t in times],
        "lat": [round(float(v), 3) for v in grid["lat"]],
        "lon": [round(float(v), 3) for v in grid["lon"]],
        "layout": "u[step] and v[step] are flat row-major arrays, lat (rows) × lon (cols), as in grid.json",
        "u": [_ints(wind["u"][s], 10) for s in range(len(times))],
        "v": [_ints(wind["v"][s], 10) for s in range(len(times))],
    }


def _bilinear(lat_axis, lon_axis, field, lat, lon):
    """Interpolate field[..., lat, lon] at one point. Axes may be descending."""
    def frac_index(axis, v):
        if axis[0] > axis[-1]:
            axis = axis[::-1]
            i = np.interp(v, axis, np.arange(len(axis)))
            return len(axis) - 1 - i
        return np.interp(v, axis, np.arange(len(axis)))

    fi, fj = frac_index(lat_axis, lat), frac_index(lon_axis, lon)
    i0, j0 = int(np.floor(fi)), int(np.floor(fj))
    i1, j1 = min(i0 + 1, len(lat_axis) - 1), min(j0 + 1, len(lon_axis) - 1)
    di, dj = fi - i0, fj - j0
    return ((1 - di) * (1 - dj) * field[..., i0, j0] + (1 - di) * dj * field[..., i0, j1]
            + di * (1 - dj) * field[..., i1, j0] + di * dj * field[..., i1, j1])


def _ints(a, scale=1.0):
    return [None if not np.isfinite(v) else int(round(v * scale)) for v in np.ravel(a)]


def _daily(times: list[datetime], values: np.ndarray, tz: ZoneInfo) -> list[dict]:
    days: dict[str, list[float]] = {}
    for t, v in zip(times, values):
        if np.isfinite(v):
            days.setdefault(t.astimezone(tz).date().isoformat(), []).append(float(v))
    return [{"date": d, "pm25_mean": round(float(np.mean(vs))),
             "pm25_max": round(float(np.max(vs))), "steps": len(vs),
             "partial": len(vs) < 8}
            for d, vs in sorted(days.items())]


def build(grid: dict, run: datetime, now: datetime) -> tuple[dict, dict]:
    times = [run + timedelta(hours=float(h)) for h in grid["lead"]]
    meta = {
        "generated_utc": _iso(now),
        "run_utc": _iso(run),
        "label": LABEL,
        "attribution": ATTRIBUTION.format(year=run.year),
        "units": {"pm25": "µg/m³ (surface, modelled)",
                  "aod": "aerosol optical depth at 550 nm × 100"},
        "times": [_iso(t) for t in times],
    }

    grid_out = {
        **meta,
        "lat": [round(float(v), 3) for v in grid["lat"]],
        "lon": [round(float(v), 3) for v in grid["lon"]],
        "layout": "values[step] is a flat row-major array, lat (rows) × lon (cols)",
        "pm25": [_ints(grid["pm25"][s]) for s in range(len(times))],
        "aod": [_ints(grid["aod"][s], 100) for s in range(len(times))],
    }

    towns = {}
    for t in geo.towns():
        lat, lon = t["point"]["lat"], t["point"]["lon"]
        pm = _bilinear(grid["lat"], grid["lon"], grid["pm25"], lat, lon)
        aod = _bilinear(grid["lat"], grid["lon"], grid["aod"], lat, lon)
        towns[t["slug"]] = {
            "pm25": _ints(pm),
            "aod": _ints(aod, 100),
            "daily": _daily(times, pm, ZoneInfo(t["timezone"])),
        }
    return grid_out, {**meta, "towns": towns}


def _run_wind(put_json, get_json, want: datetime, grid: dict, now: datetime, *, force: bool, log) -> dict:
    """The wind file for run `want`, on the smoke grid. Never raises: wind is an extra, and a failure here must not
    touch the smoke forecast. A failed request is retried after WIND_RETRY_AFTER."""
    try:
        wm = get_json(WIND_META_KEY) or {}
        if wm.get("run_utc") == _iso(want) and not force:
            return {"status": "up_to_date"}
        tried = wm.get("tried_utc")
        if (tried and wm.get("tried_run") == _iso(want) and not force
                and now - datetime.fromisoformat(tried.replace("Z", "+00:00")) < WIND_RETRY_AFTER):
            return {"status": "waiting"}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                wind = _read_wind(_retrieve(want, Path(tmp), WIND_VARS))
            out = build_wind(wind, grid, want, now)
            put_json(WIND_KEY, out, gzipped=True)
            put_json(WIND_META_KEY, {"run_utc": _iso(want), "generated_utc": _iso(now)}, gzipped=False)
            return {"status": "updated", "run_utc": _iso(want)}
        except (Exception, SystemExit) as e:  # noqa: BLE001 - see docstring (the R2 helper exits on an upload error)
            msg = str(e).splitlines()[-1][:200] if str(e) else type(e).__name__
            log(f"  wind: {msg}")
            try:
                put_json(WIND_META_KEY, {**wm, "tried_utc": _iso(now), "tried_run": _iso(want)}, gzipped=False)
            except (Exception, SystemExit):  # noqa: BLE001
                pass
            return {"status": "failed", "error": msg}
    except (Exception, SystemExit) as e:  # noqa: BLE001
        return {"status": "failed", "error": str(e).splitlines()[-1][:200] if str(e) else type(e).__name__}


def run(put_json, get_json, *, force: bool = False, log=print) -> dict:
    now = datetime.now(timezone.utc)
    want = latest_run(now)
    meta = get_json(META_KEY) or {}
    have = meta.get("run_utc")

    if have == _iso(want) and not force:
        # The smoke files are current; the wind may still be missing (its request failed, or it was added later).
        # Its grid and steps are those of the smoke file we published.
        wind = {"status": "failed", "error": "smoke grid not readable"}
        pub = get_json("smoke/grid.json")
        if pub and pub.get("lat") and pub.get("lon") and pub.get("times"):
            run_dt = datetime.fromisoformat(pub["run_utc"].replace("Z", "+00:00"))
            leads = [(datetime.fromisoformat(t.replace("Z", "+00:00")) - run_dt).total_seconds() / 3600 for t in pub["times"]]
            grid_pub = {"lat": np.asarray(pub["lat"], dtype=float), "lon": np.asarray(pub["lon"], dtype=float),
                        "lead": np.asarray(leads, dtype=float)}
            wind = _run_wind(put_json, get_json, want, grid_pub, now, force=force, log=log)
        return {"status": "up_to_date", "run_utc": have, "wind": wind}

    try:
        with tempfile.TemporaryDirectory() as tmp:
            grid = _read(_retrieve(want, Path(tmp)))
    except CamsError as e:
        have_dt = datetime.fromisoformat(have.replace("Z", "+00:00")) if have else None
        stale = have_dt is None or now - have_dt > STALE_AFTER
        return {"status": "failed", "error": str(e), "run_utc": have, "stale": stale}

    grid_out, towns_out = build(grid, want, now)
    put_json("smoke/grid.json", grid_out, gzipped=True)
    put_json("smoke/towns.json", towns_out, gzipped=False)
    put_json(META_KEY, {"run_utc": _iso(want), "generated_utc": _iso(now)}, gzipped=False)
    wind = _run_wind(put_json, get_json, want, grid, now, force=force, log=log)
    return {"status": "updated", "run_utc": _iso(want),
            "pm25_max": int(np.nanmax(grid["pm25"])), "wind": wind}
