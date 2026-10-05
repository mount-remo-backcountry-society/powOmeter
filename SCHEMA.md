# POW-O-METER published data: schema v1

This is the contract for the files under `v1/`. **Changes are add-only:**
columns and variables are never renamed, removed or reordered. A breaking
change gets a new `v2/` alongside `v1/`. All changes are listed in
`CHANGELOG.md`. Machine-readable version: `datapackage.json`.

## Files

| File | Content |
|---|---|
| `observations.csv` | Every point reading: all variables and statistics, with labels |
| `best.csv` | Best available readings: median distance and snow depth, plus the point variables. Values with quality `poor` or `missing` are left out |
| `best_hourly.csv` | `best.csv` interpolated to whole hours. **Not hourly means** |
| `latest.json` | The most recent value of each variable in `best.csv` |
| `status.json` | Station health: last observation and message, clock offset, battery, decode errors, and `alarms` (a list of `{id, message}`; empty when all is well) |
| `sites.json` | Stations and sites: coordinates, periods, licence per source |
| `datapackage.json` | Column definitions, units, CF standard names, licence |

## Columns (all CSV files, in this order)

| Column | Meaning |
|---|---|
| `time_utc` | UTC instant of the reading, RFC 3339 with `Z`. In `best_hourly.csv`: the whole hour |
| `station_id` | `powometer` |
| `site_id` | `shames_top_a` (original site, until 2026-09-26) or `shames_top_b` (current site). **Never combine snow depth across sites** |
| `variable` | See below |
| `statistic` | `point`; or for the ultrasonic burst: `min`, `max`, `median` |
| `value` | Number; empty when missing |
| `unit` | See below |
| `source` | `sd` (SD card, authoritative), `radio` (satellite; fills where the SD card has no reading), `derived` (in `best_hourly.csv`: interpolated). Snow depth keeps the source of the distance it comes from |
| `level` | `qc` (automatic checks applied) or `corrected` (a manual correction applied). Raw data is in `raw/` of the repository |
| `approval` | `working` (shown as **Provisional**), `in_review`, `approved` |
| `quality` | WaterML 2.0 codes (WMO "WaterML2"): `good`, `suspect`, `estimate`, `poor`, `unchecked`, `missing` |
| `qualifiers` | Semicolon-separated reasons, from the list below, plus correction ids such as `C002` |

Rows are sorted by `station_id`, `site_id`, `variable`, `statistic`, then
`time_utc` and `source`.

In `best_hourly.csv`, `quality` is the worse of the two readings either side
of the hour, `approval` the less approved, and `level` `corrected` if either
was corrected.

## Variables

| `variable` | CF standard name | Unit | Notes |
|---|---|---|---|
| `snow_depth` | `surface_snow_thickness` | cm (whole) | Mount reference − distance. `median` uses the median distance; `max` and `min` come from the burst's min and max distance. **Zero is the top of the alpine heather** under the sensor (ankle-high), not the soil: heather grows over a summer, and the first snow compresses it, so early-season values can read slightly negative |
| `distance_to_surface` | – | m | Sensor face to the snow or ground surface |
| `air_temperature` | `air_temperature` | degC | |
| `relative_humidity` | `relative_humidity` | % | |
| `air_pressure` | `surface_air_pressure` | hPa | Station level, SD card only |
| `battery_voltage` | – | V | SD card only |
| `enclosure_temperature` | – | degC | Inside the logger box (pressure sensor), SD card only. Diagnostic: LiPo charging stops below about 0 °C |

## Qualifiers

| Qualifier | Meaning |
|---|---|
| `no_echo` | The sensor received no echo; no distance |
| `too_close` | The sensor's minimum-range reading (0.50 m): something nearer than 50 cm, in storms usually falling snow; no distance |
| `sensor_fault` | The temperature/humidity sensor returned no reading |
| `out_of_range` | Physically implausible value |
| `spike` | Departs sharply from neighbouring readings |
| `site_visit` | People were at the station |
| `negative_depth` | Snow depth below −15 cm (sensor scatter is about ±8 cm on bare ground) |
| `reference_estimate` | The snow-depth reference for this mounting is an estimate |
| `timestamp_repaired` | Time recovered from clock syncs (the station clock was wrong) |
| `timestamp_estimated` | Time reconstructed (frozen clock 2025-01-31 → 02-17) |
| `timed_by_transmit` | Radio reading timed from the satellite transmit time |
| `interpolated` | Hourly value interpolated between readings (a reading exactly on the hour is used as is, without this qualifier) |
| `gap_filled` | Value filled by a `gap_fill` correction |
| `C001`, `C002` … | Ids of manual corrections (`config/corrections.yaml` in the repository) |

## Licence

POW-O-METER data: CC BY 4.0, credit "Mount Remo Backcountry Society". Data
from other stations, if ever included, keeps its owner's licence, given per
source in `sites.json`. Station data are not a forecast; see
<https://avalanche.ca>.
