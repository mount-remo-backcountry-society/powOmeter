"""The processing sequence, in one place (used by `build` and `approve`).

  1. assemble   SD + radio readings into one table (raw values)
  2. qc.run     automatic checks on the measured values
  3. corrections, phase "measured": manual corrections to measured values
  4. derive     snow depth = mount reference - (corrected) distance
  5. corrections, phase "derived": manual corrections to snow depth
  6. approval   approved periods published from their snapshots

Steps 3-4 are in this order so that a correction on the distance always
reaches the snow depth derived from it (independent code review F2).
"""
from __future__ import annotations

from . import approval, assemble, corrections, qc, radio
from .config import Config


def process(cfg: Config, messages=None, with_approval: bool = True, sd_report: list | None = None):
    """Return (observations, matched correction ids, approval alarms).
    `sd_report` receives one dict per SD LOG file (see sd.load)."""
    messages = radio.load_all() if messages is None else messages
    obs = assemble.merge(assemble.sd_points(cfg, messages, sd_report), assemble.radio_points(cfg, messages))
    obs = qc.run(obs, cfg)
    matched: set[str] = set()
    obs = corrections.apply(obs, cfg, "measured", matched)
    steps = qc.visit_steps(obs, cfg)
    obs = qc.derive_snow_depth(obs, cfg, steps)
    obs = corrections.apply(obs, cfg, "derived", matched)
    alarms = [f"visit {s['date']}: distance changed {s['step_m']:+.2f} m but mounts.yaml has no re-mount; "
              "snow depth after it is labelled estimate. Add a mount (or confirm it was snowfall)"
              for s in steps]
    if with_approval:
        obs, ap_alarms = approval.apply(obs, cfg)
        alarms += ap_alarms
    else:
        obs = obs.assign(approval="working")
    return obs, matched, alarms
