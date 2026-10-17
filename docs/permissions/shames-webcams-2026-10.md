# Permission: Shames Mountain webcams

**Owner:** Shames Mountain.

**Images:** the two webcams in `config/webcams.yaml` (chairlift, parking lot).

**Granted:** in writing, received 2026-10-05 by the maintainer for the Mount
Remo Backcountry Society.

**This file is a record, not Shames Mountain's text.** Shames Mountain asked
that its reply not be given to AI tools. The maintainer wrote this summary of
its terms, and keeps the original reply outside this repository. Do not ask
for it, paste it into an AI assistant or add it here.

**What it covers** (checked against the original by the maintainer,
2026-10-16):
- showing the newest image of each camera on the station page, loaded from
  Shames Mountain's own address;
- keeping shrunk copies of the frames on the published site for the 3-day
  time lapse. They are never stored in git.
- the same use in powWX, the maintainer's separate forecast project.

**Conditions:**
1. Shames Mountain is credited for the images.
2. It is stated that Shames Mountain is not liable.

**How POW-O-METER meets them:** `credit` in `config/webcams.yaml` gives both
and is shown under the webcams on the station page. The terms of use
(`site/terms.html`, sections 5 and 6) repeat them.

**If Shames Mountain withdraws permission:** set `enabled: false` in
`config/webcams.yaml`. The next publish run shows no webcams and keeps no
frames.
