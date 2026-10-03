# Independent review brief: POW-O-METER data platform v2

This brief is for an independent reviewer (another AI model or a person) who
has **not** seen the conversation in which the design was developed. It is
deliberately neutral: it says what to check, not what to conclude.

## Your task

Review `docs/DATA_PIPELINE_DESIGN.md` critically and independently, and
judge whether it is a sound plan for its stated goal and requirements. The
design was written by an AI assistant together with the project owner. Treat
every claim in it as something to verify, not as fact. You are free to
conclude that parts of it, or the whole approach, should be different.

**Do not modify any files.** This is a read-only review. You may read
public web documentation (GitHub, RockBLOCK/Ground Control, Avalanche Canada,
BC government data licences) to check claims. Do not send messages, open
accounts, or call any write or submit endpoints.

## Background you need

- The owner, Julian, is a volunteer. He builds and maintains the station for
  the Mount Remo Backcountry Society (MRBS), who own it. He works
  professionally with hydrometric data (Aquarius Time-Series), so he thinks
  in terms of raw records, corrections and provenance. He prefers concise,
  evidence-based answers.
- The station is remote, reached on foot, and cannot be bench-tested.
  Firmware changes are out of scope for this design.
- **powWX** (`C:\Users\Julian.Krick\src\powWX`, public on GitHub as
  `j-krick/powWX`) is Julian's separate, personal, experimental forecasting
  project. It currently reads POW-O-METER data from the Google Sheet.

## Read, in this order

1. `docs/DATA_PIPELINE_DESIGN.md`: the design under review (draft v3,
   2026-10-02).
2. `docs/research/STANDARDS_AND_PUBLISHING.md`: the standards and publishing
   research behind §6.5–§6.6 and §10. It was also written by an AI agent, so
   check it too.
3. `IMPROVEMENTS.md`: history of the firmware and Apps Script problems.
4. `scripts/Google Apps script v2.js`: the current intake and processing
   (running live).
5. `scripts/clean_sd_data.py`, `data/Cleaned/README.md`: the SD processing
   and the timestamp repairs.
6. `data/RockBLOCK/README.md` and `data/PCDS/README.md`: the radio history export and the nearby-station tests.
7. powWX: `README.md`, `powwx/observations.py`, `powwx/obs_logger.py`,
   `config/locations.yaml`, `.github/workflows/`.
8. Anything else in the project you find relevant.

**Do not read or quote** `misc/`, the files in `data/Google Sheet/`, or the
original RockBLOCK export's `Approx Lat/Lng` column in your report. They hold
private correspondence and a private location. (`data/Aquarius/` is work
data; skip it.)

## Requirements the design must meet (the owner's own words, summarised)

- R1: all services free or very cheap
- R2: easily accessible and open source
- R3: someone without much programming knowledge can take over, possibly
  with help from AI
- R4: powWX and POW-O-METER are independent (a change in one must not break
  the other), yet they integrate
- R5: more stations may be added
- R6: ultimate goal: better weather data for ski-hill and backcountry users,
  for trip planning and safety

Also: two separate, standalone sites (station data; forecast), easy to switch
between; the station site can overlay nearby stations.

The owner's direction: **start building soon and settle details along the
way.** The design fixes only what is expensive to change later (§13) and
parks the rest. Judge the core (phases 0–3) most closely. For parked items,
only flag anything that the core would make hard to add later.

## Questions to answer

1. **Fitness for purpose.** Does the design meet R1–R6? Where does it fall
   short, or trade one requirement against another without saying so?
2. **Simpler alternatives.** Is there a meaningfully simpler design that
   meets the requirements as well or better? Is anything over-engineered for
   a one-volunteer project? Is anything under-engineered?
3. **Handover realism (R3).** Walk through what a non-programmer successor
   would actually have to do in a normal season and after a failure. Which
   steps would they get stuck on?
4. **Failure modes.** What happens when each external dependency fails or
   changes (Gmail, Google Apps Script, Google Sheets, GitHub Actions and
   Pages, RockBLOCK, the Avalanche Canada interface)? Is anything a silent
   failure?
5. **Data integrity.** Are the raw-immutable, corrections-as-data and
   merge rules (SD versus radio) sound? Any way the published data could be
   wrong without anyone noticing?
6. **Safety (R6).** Could the published data or site mislead a backcountry
   user, through stale data, wrong units or timezones, or mixed sites? Are the
   proposed safeguards enough?
7. **Privacy and openness.** Is the plan for a public repo with local-only
   sensitive files safe? What could still leak?
8. **Coupling (R4).** Is the one-way dependency between the station project
   and powWX real, or does something still tie them together?
9. **Verification of claims.** Check the claims marked *(unverified)* in
   §15, plus any others you doubt: facts about GitHub, RockBLOCK, the
   Avalanche Canada interface, licences, and the numbers and file:line
   references in the design.
10. **Roadmap.** Is the phase order right? Is anything missing that should
    come first? Is the "fixed now / parked" split right?
11. **Standards and labels.** Are the level / approval / quality labels
    (§6.5), names and units (§6.6) and the correction operation log (§8.2)
    sound and consistent with the cited standards (CF, WMO, WaterML)? Is
    anything misapplied?
12. **Licensing.** Is the handling of BC MoTI "Access Only" data (§10)
    correct and sufficient, including for powWX?
13. **What is missing entirely?**

## Report format

1. **Verdict** in 2–4 sentences: sound, sound with changes, or rethink.
2. **Findings, most important first.** For each: the design section; what
   the issue is; the evidence (file:line, command output, or documentation
   link); the impact against R1–R6; a suggested change. Label each
   **Confirmed** (you verified it) or **Plausible** (reasoned, not verified).
3. **Claims checked.** A list of claims you verified or refuted, with the
   source for each.
4. **What you did not check,** so the owner knows the limits of the review.

Be specific and concise. Disagreement is useful; agreement without evidence
is not.
