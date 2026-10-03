# Standards and publishing options for POW-O-METER data

Desk research, 2026-10-02. This informs the published data format (design doc §6.6, §8). It does not redesign the pipeline.
Each claim is tagged **[verified: URL]** (read in the primary source) or **[unverified]**.

## 1. Summary: recommendations

1. **Keep CSV + JSON + Parquet. Add one sidecar metadata file.** Publish a `datapackage.json` (Frictionless Data Package v2) or a CSVW `*-metadata.json` next to the CSVs. For each variable it gives the CF `standard_name`, the unit string, `cell_methods` and a plain-English description. Do not adopt NetCDF or WaterML until an institution asks. If one does, write NetCDF-CF (featureType `timeSeries`) from the same store.
2. **Use CF names behind readable column values.** Keep the long format, with short `variable` values (`snow_depth`, `air_temperature`, `relative_humidity`, `air_pressure`, `battery_voltage`…). Add a `statistic` column (`point`/`mean`/`max`/`sum`) and a `variables` table that maps each variable to its CF standard name and unit. Snow depth goes in **cm**, as WMO requires; the other units are listed in A.
3. **Time:** UTC, RFC 3339/ISO 8601 with `Z`. Each hourly value is stamped at the **end of its hour**, which matches CF example 7.5 and WaterML "preceding interval". Say so in SCHEMA.md. Watch out: pandas `resample` labels bins at the **start** by default, so set `label="right", closed="right"`.
4. **Split the quality labels into three fields:**
   - `quality`: one value per data point, from the WaterML 2.0 list (good / suspect / estimate / poor / unchecked / missing). WMO registers this list as a quality flagging system.
   - `approval`: unreviewed / in review / approved. This maps one-to-one to USGS working / analyzed / approved, shown publicly as Provisional / Approved.
   - `qualifiers`: a free list of codes such as `site_visit`, `sensor_fault`, `gap_filled` or `adjusted`.

   Publish a crosswalk to QARTOD, WMO BUFR 0 33 020 and ECCC DMS codes (table C2). One point for discussion: the **level** value "provisional" clashes with how USGS and WaterML use "provisional" for *approval*. Consider renaming the levels `raw` / `qc` / `corrected`.
5. **Licensing risk to resolve before publishing third-party data.** BC MoTI's observation archive (PAWS) is catalogued as **"Access Only"**, which means *reproduction is not permitted without written permission*. PCDS passes data on "subject to the terms of use of each source organization". Only the MoTI *station locations* are under the Open Government Licence – BC. So do not put MoTI values into the CC BY 4.0 files (`hourly_all.csv` currently would) until permission is obtained. Carry a per-source `licence` field in `sites.json`.
6. **Station metadata:** fill in the checklist in D. It follows the WIGOS mandatory elements plus the siting items that CIMO lists for snow. Record the relocation on 2026-09-26 as a new site with its own coordinates, sensor height and zero-depth distance. Re-measure the zero-depth distance before and after every season.
7. **Publication targets, in order of fit:**
   - (a) your own GitHub Pages, which Avalanche Canada already reads
   - (b) Windy Stations: free, light, and you keep ownership
   - (c) CWOP, which feeds NOAA MADIS and Synoptic: free, but it uses an APRS packet format and offers no snow-depth field for automated stations
   - (d) Zenodo for yearly archived snapshots with a DOI [unverified]

   Weather Underground needs a broad licence grant and adds little. WIS2, CoCoRaHS and PCDS are not realistic routes; see F.
8. **Next steps:**
   - Write `variables` and the crosswalk into SCHEMA.md.
   - Add `datapackage.json`.
   - Decide the level names.
   - Draft a permission request to MoTI. Julian or MRBS sends it; I have contacted no one.
   - Record the zero-depth distance at the new site.

## A. Naming and units

CF Standard Name Table **v95 (2026-09-16)** [verified: https://cfconventions.org/Data/cf-standard-names/current/src/cf-standard-name-table.xml]. CF Conventions current release **1.13 (2025-12-17)** [verified: https://cfconventions.org/conventions.html].

| Variable | CF standard_name | Canonical unit | Suggested published unit | cell_methods (raw ~17 min / hourly) |
|---|---|---|---|---|
| Snow depth (HS) | `surface_snow_thickness` | m | cm, rounded to whole cm (WMO: "must be reported in centimetres… rounded to the nearest centimetre") | `time: point` / `time: mean` (or `time: median`) |
| Distance sensor→surface | **none** (no "distance to surface" name exists; `height` is "vertical distance above the surface", which is the wrong reference) | — | mm or cm, with `long_name` only | `time: point` |
| Air temperature | `air_temperature` | K | degC (add `units_metadata = "temperature: on_scale"`, which CF now strongly recommends) | `time: point` / `time: mean`, `time: maximum`, `time: minimum` |
| Relative humidity | `relative_humidity` | 1 | % (UDUNITS `percent`) | `time: point` / `time: mean` |
| Station pressure (BMP390) | `surface_air_pressure` (pressure at the lower boundary of the atmosphere, i.e. station level) | Pa | hPa | `time: point` / `time: mean` |
| Sea-level pressure | `air_pressure_at_mean_sea_level` (alias `air_pressure_at_sea_level`) | Pa | hPa | only if derived; reduction from 1,150 m is model-dependent [unverified]. **Recommend: publish station pressure only.** |
| Battery voltage | **none** | — | V | `time: point` |
| Wind speed (3rd party) | `wind_speed` | m s-1 | km h-1 (what Canadian users read; UDUNITS-valid) [unverified for user preference] | `time: mean` |
| Wind direction | `wind_from_direction` | degree | degree | `time: mean` (vector mean) |
| Wind gust | `wind_speed_of_gust` ("can be indicated by a cell_methods of maximum for the time-interval") | m s-1 | km h-1 | `time: maximum` |
| Precipitation | `precipitation_amount` (kg m-2) or `lwe_thickness_of_precipitation_amount` (m) | — | mm, using the `lwe_thickness…` name | `time: sum` |
| Station elevation (metadata) | `surface_altitude` | m | m | — |
| Quality flag variable | `quality_flag` / `status_flag` | 1 | — | linked through `ancillary_variables` |

All the names, units and descriptions above are [verified: CF table v95 XML, link above]. Snow-depth reporting rule [verified: https://wgms.ch/downloads/WMO_8_II-2023_en.pdf, Vol. II §2.1.1]. The "distance" and "battery" names were checked by searching the full table XML: none exist [verified].

**Usual practice for variables without a standard name** is to omit `standard_name` and give a clear `long_name` and `units`. CF only requires `standard_name` where one exists [verified: https://cfconventions.org/Data/cf-conventions/cf-conventions-1.11/cf-conventions.html §3.3].

**Recommended naming scheme.** Keep the long format and add one column.

```
time_utc, station_id, site_id, variable, statistic, value, unit, quality, approval, qualifiers
2026-10-02T14:00:00Z, powometer, shames-top-2, snow_depth, mean, 37, cm, good, unreviewed,
```

`variable` values: `snow_depth`, `distance_to_surface`, `air_temperature`, `relative_humidity`, `air_pressure` (station level), `battery_voltage`, `wind_speed`, `wind_direction`, `wind_gust`, `precipitation`. They are lower-case, English and close to the CF names, so a non-programmer can read them. The `variables` table in SCHEMA.md / datapackage.json carries `standard_name`, `units` (UDUNITS strings such as `cm`, `degC`, `%`, `hPa`, `V`, `km h-1`, `mm`), `cell_methods` per statistic, and a one-line description. Using the CF name itself as the `variable` value (e.g. `surface_snow_thickness`) is equally valid but less readable. The mapping table gives the CF link without that cost.

## B. Time conventions

- **UTC.** WIGOS makes "Reference time — time base to which date and time stamps refer" (element 7-10) mandatory, so declare it [verified: Manual on WIGOS attachment, WIGOS Metadata Standard interim v1.02, https://old.wmo.int/wiswiki/tiki-download_file.php%3FfileId=3167]. CF time units end in `UTC`/`Z` [verified: CF 1.11 §4.4]. ECCC hourly archive times are local standard time (LST) [unverified: a secondary search summary said LST, and the ECCC page summary contradicted it], which shows why stating the time base matters.
- **Format:** RFC 3339, the internet profile of ISO 8601, e.g. `2026-10-02T14:00:00Z`. "Z" denotes UTC offset 00:00 [verified: https://www.rfc-editor.org/rfc/rfc3339]. ACDD requires ISO 8601 extended format for `time_coverage_*` [verified: https://wiki.esipfed.org/Attribute_Convention_for_Data_Discovery_1-3].
- **Start or end of interval:** CF does not mandate either. It recommends explicit `time_bounds`, and in its station example 7.5 "the time axis values coincide with the end of each interval" [verified: https://cfconventions.org/Data/cf-conventions/cf-conventions-1.13/cf-conventions.html §7.3, Example 7.5]. WaterML 2.0 encodes the same idea per series as `interpolationType` `AveragePrec` / `MaxPrec` ("average value over the preceding interval") [verified: OGC 10-126r4 Table 6, https://docs.ogc.org/is/10-126r4/10-126r4.pdf]. **Recommendation:** stamp each hourly value at the end of its hour. State "hour ending" in SCHEMA.md. Optionally add a `time_start_utc` column. For raw readings, the timestamp is the measurement instant (`time: point`).
- **Pitfall:** pandas `resample` labels and closes bins on the **left** by default for hourly frequencies [verified: https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.resample.html].

## C. Quality labels

**C1. Existing schemes**

| Scheme | Codes | Notes |
|---|---|---|
| WaterML 2.0 quality (OGC 10-126r4) | Good, Suspect, Estimate, Poor, Unchecked, Missing | Registered by WMO as a quality-flag system for WIGOS element 8-04 [verified: https://codes.wmo.int/wmdr/QualityFlagSystem; https://codes.wmo.int/wmdr/WaterML2_0]. Separate per-point `qualifier` field for anything else. Series `status` (e.g. "Validated", "Provisional") is defined, but WaterML "does not currently define a code list" for it [verified: OGC 10-126r4 §9.4.1.8.2, §9.12.3.2] |
| WMO BUFR 0 33 020 | 0 good, 1 inconsistent, 2 doubtful, 3 wrong, 4 not checked, 5 has been changed, 6 estimated, 7 missing | The other WMO-registered flag system [verified: https://codes.wmo.int/bufr4/codeflag/0-33-020] |
| IOOS QARTOD | 1 pass, 2 not evaluated, 3 suspect, 4 fail, 9 missing | Simple and widely used in ocean data [verified via search summary of https://repository.library.noaa.gov/view/noaa/24982; full manual not opened] |
| ECCC DMS (used for BC CRMP partner data) | Qa: -10 suppressed, -1 missing, 0 error, 10 doubtful, 20 inconsistent, 100 accepted. Description flags: 1 derived, 2 estimated, 3 adjusted, 4 incomplete, 5 trace, 6 multiple | [verified: Weick & Gardner 2025, *Quality control of automated hydrometeorological data in BC*, CRMP, Tables 1a/1b, http://library.nrs.gov.bc.ca/digipub/MR134-HCQ.pdf]. Same report lists MoTI SAWS confidence levels and Rio Tinto codes |
| USGS approval states | working → analyzed → approved. Public label: Provisional, Provisional, Approved | [verified: USGS OSW memo SW 2017.10, https://water.usgs.gov/osw/time-series-guidance/SW_2017.10+GW_2017.03+WQ_2017.07.pdf] |
| PCDS | Portal says data are "raw form without any guarantee of quality assurance review… preliminary" | [verified: https://www.uvic.ca/pcic/data-analysis-tools/data-portal/station-data/index.php]. No PCIC flag list found |
| CSA R102:22 | *Data qualification for Canadian automated hydrometeorological monitoring stations* | Exists, Canadian national standard. Content not read (sold through CSA store) [verified existence: https://scc-ccn.ca/areas-work/climate-and-sustainability/extreme-weather/climate-data] |

**C2. Recommended minimal scheme and crosswalk** (publish this table in SCHEMA.md)

| `quality` (project) | Meaning | WaterML 2.0 | QARTOD | BUFR 0 33 020 | ECCC DMS Qa |
|---|---|---|---|---|---|
| `good` | Passed automatic QC (and review, if approved) | Good | 1 | 0 | 100 |
| `unchecked` | No QC applied (e.g. raw level) | Unchecked | 2 | 4 | — |
| `suspect` | Failed a soft test, or flagged by hand | Suspect | 3 | 2 | 10 |
| `poor` | Rejected: sensor fault, out of range, disturbed | Poor | 4 | 3 | 0 |
| `estimate` | Gap-filled or interpolated | Estimate | (3)* | 6 | 100 + desc. 2 |
| `missing` | No value | Missing | 9 | 7 | -1 |

\* QARTOD has no "estimated" code.

`approval`: `unreviewed` → USGS working (public "Provisional"), `in_review` → analyzed ("Provisional"), `approved` → approved.

`qualifiers` is a semicolon-separated list of project codes. Map `adjusted` (offset or drift correction) to BUFR 5 / ECCC description 3, and `gap_filled` to ECCC 2. The others (`site_visit`, `snow_on_sensor`, `relocated`…) stay project-specific.

`level`: "provisional" means "not yet approved" in USGS/WaterML usage. Using it as a *processing level* will confuse outside readers. Suggest `raw` / `qc` / `corrected`, which ACDD's `processing_level` is meant to describe [verified: ACDD 1.3 link above].

## D. Station metadata

**WIGOS mandatory elements** (selection, numbered as in the standard) [verified: WIGOS Metadata Standard interim v1.02, link in B; current edition is WMO-No. 1192, not read]:
- 1-01 observed variable
- 1-03 temporal extent
- 2-02 network affiliation
- 3-03 name
- 3-04 type
- 3-06 unique identifier
- 3-07 geospatial location "at the time of observation"
- 3-09 operating status
- 5-01 source
- 5-02 method
- 6-06 sampling interval
- 6-08 schedule
- 7-03 reporting period
- 7-07/7-08 data format and version
- 7-09 aggregation period
- 7-10 reference time
- 7-13 latency
- 8-03/8-04 quality flag and flagging system
- 9-01 owner
- 9-02 data policy
- 10-01 contact

Conditional elements: 4-01 surface cover, 4-03 topography, 5-15 instrument exposure. Optional element: 4-04 events at the station.

Canada adopted WIGOS metadata unchanged as **CSA R100:20**, which tells operators to upload to OSCAR/Surface [verified: https://scc-ccn.ca/standards/notices-of-intent/csa-group/canadian-metadata-standard-hydrometeorological-monitoring]. Registering in OSCAR/Surface is done through national focal points, not volunteers [unverified].

**CF/ACDD global attributes** for a sidecar:
- CF: `title`, `institution`, `source`, `history`, `references`, `comment`, `featureType: timeSeries` [verified: CF 1.11 §2.6.2, §9]
- ACDD: `summary`, `keywords`, `Conventions`, `id`, `license`, `creator_*`, `publisher_*`, `processing_level`, `geospatial_lat/lon_*`, `time_coverage_start/end/resolution`, `standard_name_vocabulary` [verified: ACDD 1.3]

**Volunteer checklist**, one row per site period (new row on 2026-09-26):

- [ ] Station ID, site ID, name, owner (MRBS), contact, licence
- [ ] Lat/lon (WGS84, 5 decimals), elevation (m), GPS method; valid-from/valid-to dates
- [ ] Sensor models and serials: MB7374, SHT31, BMP390, logger, firmware version
- [ ] Sensor heights above ground: ultrasonic, T/RH. Radiation shield type (passive or aspirated)
- [ ] **Zero-depth distance** (sensor to bare ground), measured at install, at season start and at season end
- [ ] Target surface under the sensor (natural ground / turf / plastic target), vegetation, levelness
- [ ] Slope and aspect of the target area, local exposure (ridge, clearing, distance and height of obstacles), prevailing winter wind
- [ ] Sampling: interval (~17 min), single reading or median of a burst, temperature compensation method
- [ ] Transmission: Iridium, interval, which variables
- [ ] Event log: visits, relocations, sensor swaps, firmware changes, clock fixes
- [ ] Photos: four compass directions, plus one looking down at the target, at each visit

**CIMO Guide (WMO-No. 8, Vol. II, 2023, ch. 2) points for ultrasonic snow depth** [all verified: https://wgms.ch/downloads/WMO_8_II-2023_en.pdf, §2.2 and §2.3.2]:
- HS = zero-depth distance − distance to target. The raw sonic reading is corrected for the speed of sound by √(T/273.15) using concurrent air temperature. The MB7374 does this internally with its own sensor [unverified, vendor claim], so its correction depends on that sensor's temperature, not the SHT31's.
- Expected uncertainty: sonic ~2 cm. Buy and install for ≤ ±2 cm, preferably ±1 cm. The response area is a cone, and the sensor "usually measure[s] the distance to the highest obstacle" in it.
- Siting: record slope, aspect, surface type, prevailing wind and obstacles in metadata. In alpine terrain, choose flat sheltered ground and avoid ridges, slopes and over-exposed spots. Keep obstacles ≥ 2× their height away, though exceptions are accepted for the mount itself.
- Mount: rigid, so wind does not move the sensor. Horizontal arm to clear the pole, angled 30–45° in heavy-snow sites.
- Target: flat and stable, either mown grass or an artificial target (turf or perforated plastic). Minimize vegetation.
- QC: median of frequent readings over ~5 min. Range check from 0 − 2×(stated uncertainty) up to ~120 % of the maximum expected HS. Step-change check.
- Main error sources:
  - zero-depth drift (settling, frost heave). Verify it before and after each season and note changes in the metadata.
  - wrong temperature correction (radiative heating of an unshielded sensor)
  - frost or snow on the transducer
  - uneven melt in the footprint
  - mount motion
  - logger and power faults

## E. File formats

- CSV + JSON + Parquet is reasonable for open publication. PCDS itself serves "NetCDF, CSV or MS Excel" [verified: PCDS portal link above]. NetCDF-CF is the norm in climate science, and WaterML 2.0 in hydrology and the WMO hydrological system [verified existence: OGC 10-126r4]. Neither is needed until a named institution asks. Generating NetCDF-CF from Parquet is a few lines of xarray [unverified effort estimate].
- **Lightest way to make CSV self-describing.** Pick one:
  - **Frictionless Data Package v2** (`datapackage.json`, released 2024-06-26). It holds field names, types, missing values, package-level licences and contributors [verified: https://datapackage.org/]. Add CF attributes (`standard_name`, `cell_methods`) as extra field properties [unverified that validators accept custom properties silently].
  - **CSVW**, a W3C Recommendation from 2015-12-17. A `<file>.csv-metadata.json` file with `tableSchema.columns` (`name`, `titles`, `datatype`, `propertyUrl`) [verified: https://www.w3.org/TR/tabular-metadata/]. It is more formal and has less tooling [unverified].

  Either way, put a short human-readable header in SCHEMA.md and keep the units in the `unit` column, as the design already does.
- CSA R103:23 (*Protocols for sharing automated hydrometeorological monitoring stations data and metadata*) is the Canadian standard on this topic. Not read; sold by CSA [verified existence: SCC page above].

## F. Publication options

| Target | Accepts a volunteer station in BC? | Requirements / method | Cost | Effort | Terms | Fit |
|---|---|---|---|---|---|---|
| **Own GitHub Pages** | n/a | Already planned | free | done | CC BY 4.0, your choice | Best: Avalanche Canada already reads it |
| **PCIC / PCDS (CRMP)** | No route found. CRMP is an agreement among named parties (BC ministries incl. Transportation and Transit, ECCC, BC Hydro, CRD, Metro Vancouver, Rio Tinto). The agreement was renewed from 2026-04-01 to 2033-03-31 | Partners follow CSA standards and the BC QC guide | — | High (institutional) | Each party keeps IP. Licences are granted only among parties | Low now. Possible later via a partner [verified: https://www2.gov.bc.ca/gov/content/environment/research-monitoring-reporting/monitoring/climate-related-monitoring; https://www.uvic.ca/pcic/_assets/docs/crmp_agreement.pdf] |
| **Windy Stations** | Yes, any PWS. Register at stations.windy.com | REST API (GET or JSON POST) with a station password. The legacy API shuts down end of 2026, so use the new one | Free ("in principle, free of charge") | Low: a small step in the GitHub Action | Operator grants Windyty use "by any means and for any purposes". Data "fully owned by the Provider (Station Operator)". Choose an "Open" / "Only Windy" / "Private" sharing mode | Good visibility to skiers. No snow-depth field seen in the API excerpts [unverified]. Upload from the pipeline, not the station [verified: https://account.windy.com/agreements/windy-stations-terms-of-use; https://community.windy.com/topic/8168/report-your-weather-station-data-to-windy] |
| **CWOP → NOAA MADIS** (→ Synoptic/MesoWest) | Yes. CWOP counts "world-wide" stations, sign-up is by e-mail, and Canadian stations are present [unverified: Canada specifically] | APRS-IS packets (wind, temperature, rain, pressure, humidity) | Free | Medium: APRS format, ID registration | Data go into MADIS and are used by NWS and others | Moderate. CWOP-snow is a *manual* form, US-oriented (ZIP, US time zones) [verified: http://www.wxqa.com/, http://www.wxqa.com/SIGN-UP.html, http://www.wxqa.com/snowSIGN-UP.html]. Synoptic's provider docs say PWS should join CWOP, or contact them for networks (HTTP pull or S3 push) [verified: https://docs.synopticdata.com/providers/] |
| **Weather Underground PWS** | Yes | Upload API | Free | Low | Contributor grants TWC a "royalty-free… worldwide, perpetual, irrevocable, non-exclusive… sublicense" licence. PWS data feed reuse is personal and non-commercial only | Poor: broad licence grant, no snow, no benefit over Windy [verified: https://trust.weather.com/en-US/privacy/terms-of-use] |
| **WMO WIS2** | No. WIS2 Nodes are run by NMHSs/DCPCs and approved by the national Permanent Representative | wis2box, BUFR, WIGOS IDs | — | Very high | — | Only via ECCC [verified via search summary of WMO WIS2 guide: https://github.com/wmo-im/wis2-guide/blob/main/guide/sections/part2/wis2node.adoc] |
| **ECCC / CoCoRaHS Canada** | CoCoRaHS takes **manual** gauge readings, 6–10 AM, with an approved 4" gauge. No automated stations | Web form | Gauge cost | Daily human effort | — | Not a fit for this station [verified: https://cocorahs.org/Canada]. No ECCC volunteer program for automated stations found |
| **Avalanche Canada** | Already reads the sheet. MIN is for manual field observations and "should not be used for promoting commercial ventures or personal projects" | MIN app/web. Data requests via research@avalanche.ca | Free | Low | Data-sharing policy covers *their* data, not contributions | Keep the current direct feed and tell them about format changes [verified: https://avalanche.ca/mountain-information-network/submission-guidelines; https://avalanche.ca/data-sharing-policy]. Their map shows third-party stations (e.g. Kananaskis) [unverified, secondary source] |
| **CAA InfoEx** | Industry members only (ski areas, guides, highways) | Subscriber exchange | Membership | — | Confidential | Possible indirectly if Shames Mountain ski area subscribes [unverified] |
| **Zenodo** | Yes | Upload or GitHub release integration, DOI | Free | Low | Choose CC BY 4.0 | Good for yearly citable archives [unverified] |

## G. Licensing

- **Own data under CC BY 4.0 and code under MIT** is standard and compatible with GitHub Pages, Windy (non-exclusive, and the operator keeps ownership), CWOP and Zenodo [verified: Windy terms above]. Weather Underground's licence grant is legally possible but broad.
- **BC MoTI data**:
  - The station-location dataset is under the **Open Government Licence – BC v2.0**. It allows copying, adaptation and redistribution for any lawful purpose with the statement "Contains information licensed under the Open Government Licence – British Columbia" [verified: https://catalogue.data.gov.bc.ca/dataset/17e35288-4b46-42c6-bd98-78d8443aa2a7 (API record); https://www2.gov.bc.ca/gov/content?id=A519A56BC2BF44E4A008B33FCF527F61].
  - The **observation archive (PAWS) is "Access Only"**: "reproduction is not permitted without written permission" [verified: catalogue record 5fc4f181-a796-4449-a152-2fa95523ed32; https://www2.gov.bc.ca/gov/content?id=1AAACC9C65754E4D89A118B875E0FBDA].
  - PCDS data are "subject to the terms of use of each source organization" [verified: PCDS portal]. The CRMP agreement keeps IP with each party and grants reuse licences only among parties [verified: CRMP agreement §4].
  - **Consequence:** republishing MoTI hourly values in CC BY 4.0 files is not covered by any licence I found. Options, in order of preference:
    1. Ask MoTI for written permission. Their province form is the "Copyright Permission Request"; the contact is TTWebmaster@gov.bc.ca [verified: https://www2.gov.bc.ca/gov/content/transportation/transportation-environment/weather-network-program].
    2. Meanwhile, publish only own data in the CC BY files, and either show MoTI values on the site without offering a download, or link to the source.

    Whether display-only is acceptable under "Access Only" is a legal question [unverified].
  - If permission or OGL-BC applies, keep MoTI rows labelled with their own licence and attribution. Do not relabel them CC BY. OGL-BC has no explicit CC BY compatibility clause [verified: OGL-BC page].
- **Avalanche Canada API terms:** not found. Treat values fetched from their API like the MoTI data above.

## What I could not determine

- PCDS variable names, flag codes and time convention, and whether PCIC would host a non-CRMP volunteer network.
- Full Windy upload parameter list, including whether snow depth is accepted. The API reference page did not render.
- Content of CSA R101/R102/R103, which is paywalled. These are the Canadian standards most directly on point.
- Current WIGOS Metadata Standard (WMO-No. 1192) text: the WMO library blocked automated download, so I used the 2016 interim version. WMO-No. 8 2024 edition was blocked for the same reason, so I used the 2023 Vol. II.
- Whether ECCC hourly archive timestamps are LST or UTC: the sources conflicted.
- Avalanche Canada's terms for its weather-station API and how third-party stations get onto its map.
- MB7374 internal temperature-compensation details from a MaxBotix primary datasheet: only vendor and reseller pages were seen.
- Canadian Avalanche Association OGRS (2016) rules for automated HS reporting. It is cited by the BC QC guide but was not found online.
