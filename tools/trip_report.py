"""Read a trip file (the app's TripLog, docs/REDESIGN_V2.md s.7.7) and measure it.

    py tools/trip_report.py trip-20261112-193000.csv

Phase 0 of the redesign: every line of the latency budget as a MEASURED
number, on Leo's car --

  link     replies and rpm readings per second, the round trip and its spread,
           how often the car repeats a stale value and how often it really
           refreshes it
  audio    the route (CarPlay / speaker / ...), its latency, the hardware
           buffer, the queue: what the sound lags its render by
  gears    the car's rpm per km/h in each gear, learned from rpm / speed
  motion   which way the car's 'forward' points in the phone's frame (found by
           regressing the accelerometer on the speed's own change), how well
           the accelerometer explains that change, its noise at rest
  played   what the app played against what the engine was really doing at the
           moment the sound came out: error, wobble, and the lag that aligns
           them best

The trip file is the regression input of the v2 link; `load()` is its reader.
"""

from __future__ import annotations

import math
import sys

PID_LEN = {0x04: 1, 0x05: 1, 0x0B: 1, 0x0C: 2, 0x0D: 1, 0x0F: 1, 0x10: 2, 0x11: 1,
           0x1F: 2, 0x33: 1, 0x42: 2, 0x43: 2, 0x45: 1, 0x49: 1, 0x4A: 1, 0x5A: 1,
           0x5C: 1, 0x00: 4, 0x20: 4, 0x40: 4}
PEDALS = (0x5A, 0x49, 0x4A, 0x45, 0x11)


def parse_pids(reply):
    """Mode-01 values out of an adapter's reply (lines separated by '|')."""
    out = {}
    for line in reply.replace(">", "").split("|"):
        h = line.strip().replace(" ", "").upper()
        if not h or any(c not in "0123456789ABCDEF" for c in h):
            continue
        i = h.find("41")
        if i < 0 or i % 2:
            continue
        i += 2
        while i + 2 <= len(h):
            pid = int(h[i:i + 2], 16)
            ln = PID_LEN.get(pid)
            if ln is None or i + 2 + 2 * ln > len(h):
                break
            data = [int(h[i + 2 + 2 * k:i + 4 + 2 * k], 16) for k in range(ln)]
            out.setdefault(pid, data)          # the first module to answer
            i += 2 + 2 * ln
    return out


def load(path):
    """-> dict of lists: obd, imu, gps, audio, played, events, meta."""
    t = {"obd": [], "imu": [], "gps": [], "audio": [], "played": [], "events": [],
         "meta": {}}
    with open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline().strip()
        if not first.startswith("# enginesim-trip"):
            raise ValueError("not a trip file: %r" % first[:40])
        for raw in fh:
            p = raw.rstrip("\n").split(",")
            if len(p) < 3:
                continue
            try:
                ts = float(p[1])
            except ValueError:
                continue
            k = p[0]
            if k == "O" and len(p) >= 5:
                t["obd"].append((float(p[2]), ts, p[3], ",".join(p[4:])))
            elif k == "I" and len(p) >= 11:
                t["imu"].append((ts,) + tuple(float(x) for x in p[2:11]))
            elif k == "G" and len(p) >= 4:
                t["gps"].append((ts,) + tuple(float(x) for x in p[2:6]))
            elif k == "A" and len(p) >= 9:
                t["audio"].append((ts,) + tuple(float(x) for x in p[2:9]) + (",".join(p[9:]),))
            elif k == "P" and len(p) >= 9:
                t["played"].append((ts,) + tuple(float(x) for x in p[2:9]))
            elif k == "E":
                t["events"].append((ts, ",".join(p[2:])))
            elif k == "M":
                for kv in ",".join(p[2:]).split(";"):
                    if "=" in kv:
                        a, b = kv.split("=", 1)
                        t["meta"][a] = b
    return t


def pct(xs, q):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(int(q * len(s)), len(s) - 1)]


def interp(ts, vs, t):
    """Linear interpolation on sorted ts (clamped)."""
    if not ts:
        return float("nan")
    lo, hi = 0, len(ts) - 1
    if t <= ts[0]:
        return vs[0]
    if t >= ts[-1]:
        return vs[-1]
    while hi - lo > 1:
        m = (lo + hi) // 2
        if ts[m] <= t:
            lo = m
        else:
            hi = m
    f = (t - ts[lo]) / max(ts[hi] - ts[lo], 1e-9)
    return vs[lo] + (vs[hi] - vs[lo]) * f


def readings(trip):
    """Per channel [(t_taken, value)]: taken = the middle of the request's
    round trip; rpm also flags repeats of the previous value."""
    rpm, ped, spd, mapk, rtt = [], [], [], [], []
    last = None
    for sent, got, req, reply in trip["obd"]:
        if not req.startswith("01"):
            continue
        v = parse_pids(reply)
        if not v:
            continue
        taken = 0.5 * (sent + got)
        rtt.append(got - sent)
        if 0x0C in v:
            r = (v[0x0C][0] * 256 + v[0x0C][1]) / 4.0
            rpm.append((taken, r, last is not None and r == last))
            last = r
        for p in PEDALS:
            if p in v:
                ped.append((taken, v[p][0] / 255.0))
                break
        if 0x0D in v:
            spd.append((taken, float(v[0x0D][0])))
        if 0x0B in v:
            mapk.append((taken, float(v[0x0B][0])))
    return rpm, ped, spd, mapk, rtt


def gear_clusters(rpm, spd):
    """rpm per km/h plateaus (the gears), highest first, with their share."""
    if not spd or not rpm:
        return []
    rt = [r[0] for r in rpm]
    rv = [r[1] for r in rpm]
    ratios = []
    for t, v in spd:
        if v >= 10:
            ratios.append(interp(rt, rv, t) / (v + 0.5))
    cl = []                                   # [centre, count]
    for r in ratios:
        for c in cl:
            if abs(c[0] - r) / c[0] < 0.07:
                c[0] += (r - c[0]) / (c[1] + 1)
                c[1] += 1
                break
        else:
            cl.append([r, 1])
    n = max(sum(c[1] for c in cl), 1)
    return sorted([(c[0], c[1] / n) for c in cl if c[1] / n > 0.02], reverse=True)


def motion_fit(trip, rpm, spd, gears):
    """The car's forward axis in the phone's frame.  In gear the rpm is a fine
    speedometer (v = rpm / k_gear), so the speed's change over 0.4 s windows
    is regressed on the mean user acceleration over the same window: the
    solution's direction is 'forward', its length the scale (1 if the gear
    ratios and the accelerometer agree), R^2 how much of the car's
    acceleration the phone actually feels.  Also the noise at rest.
    -> (axis, scale, r2, windows, rest_rms) or None."""
    imu = trip["imu"]
    if len(imu) < 500 or not gears or len(rpm) < 20 or not spd:
        return None
    it = [x[0] for x in imu]
    st = [x[0] for x in spd]
    sv = [x[1] for x in spd]
    ks = [g[0] for g in gears]
    fine = []                                  # (t, v m/s, gear ratio)
    for t, r, stale in rpm:
        if stale or r < 900:
            continue
        v = interp(st, sv, t) + 0.5
        if v < 8:
            continue
        k = min(ks, key=lambda kk: abs(r - kk * v))
        if abs(r - k * v) / r > 0.08:
            continue
        fine.append((t, r / k / 3.6, k))
    b = [0.0] * 3
    rows = []
    j0 = 0
    for i in range(len(fine)):
        t0, v0, k0 = fine[i]
        j = i + 1
        while j < len(fine) and fine[j][0] - t0 < 0.4:
            j += 1
        if j >= len(fine):
            break
        t1, v1, k1 = fine[j]
        if k1 != k0 or t1 - t0 > 0.8:
            continue
        while j0 < len(it) and it[j0] < t0:
            j0 += 1
        j1 = j0
        acc = [0.0, 0.0, 0.0]
        while j1 < len(it) and it[j1] < t1:
            acc[0] += imu[j1][1]; acc[1] += imu[j1][2]; acc[2] += imu[j1][3]
            j1 += 1
        n = j1 - j0
        if n < 10:
            continue
        am = [x / n * 9.80665 for x in acc]    # user acceleration is in g
        dv = (v1 - v0) / (t1 - t0)
        rows.append((am, dv))
        for p_ in range(3):
            b[p_] += am[p_] * dv
    if len(rows) < 30:
        return None
    # 'forward' is the direction the acceleration CORRELATES with the speed's
    # change along (sum of a * dv).  A full 3-axis least squares is not used:
    # on a straight road every sample lies along one line, the normal matrix
    # is rank 1 and the other two components come out as amplified noise.
    bl = math.sqrt(sum(x * x for x in b))
    if bl < 1e-9:
        return None
    axis = [x / bl for x in b]
    sxx = sxy = syy = 0.0
    for am, dv in rows:
        al = sum(am[k] * axis[k] for k in range(3))
        sxx += al * al
        sxy += al * dv
        syy += dv * dv
    wl = sxy / max(sxx, 1e-12)                  # dv per unit of felt acceleration
    r2 = (sxy * sxy) / max(sxx * syy, 1e-12)
    rest = [math.sqrt(x[1] ** 2 + x[2] ** 2 + x[3] ** 2) * 9.80665
            for x in imu[::5] if interp(st, sv, x[0]) < 0.5]
    rest_rms = math.sqrt(sum(x * x for x in rest) / len(rest)) if len(rest) > 20 else float("nan")
    return axis, wl, r2, len(rows), rest_rms


def played_vs_truth(trip, rpm, out_latency):
    """What was played (the projected car rpm, before the map) against the
    engine's rpm at the time the sound came out.  The truth is the readings
    themselves, placed at the time the car took them and interpolated (which
    the app, living in real time, could not do)."""
    pl = trip["played"]
    good = [(t, r) for t, r, stale in rpm if not stale]
    if len(pl) < 100 or len(good) < 20:
        return None
    gt = [g[0] for g in good]
    gv = [g[1] for g in good]
    rows = [(p[0], p[3]) for p in pl if gt[0] + 1 < p[0] < gt[-1] - 1]
    if len(rows) < 100:
        return None
    best = None
    for lag_ms in range(-100, 501, 10):
        s = 0.0
        for t, v in rows[::3]:
            s += (v - interp(gt, gv, t + out_latency - lag_ms / 1000.0)) ** 2
        if best is None or s < best[0]:
            best = (s, lag_ms)
    e2 = w2 = 0.0
    n = 0
    for i in range(4, len(rows) - 4):
        t, v = rows[i]
        m = sum(rows[i + k][1] for k in range(-4, 5)) / 9.0
        e2 += (v - interp(gt, gv, t + out_latency)) ** 2
        w2 += (v - m) ** 2
        n += 1
    return math.sqrt(e2 / n), math.sqrt(w2 / n), best[1]


def report(path):
    trip = load(path)
    rpm, ped, spd, mapk, rtt = readings(trip)
    out = []
    P = out.append
    P("trip: %s" % path)
    if trip["meta"]:
        P("  " + "  ".join("%s=%s" % kv for kv in sorted(trip["meta"].items()) if kv[0] != "wall"))
    span = (rpm[-1][0] - rpm[0][0]) if len(rpm) > 1 else 0.0
    P("  %.0f s of rpm; lines: obd %d, imu %d, gps %d, played %d, audio %d, events %d" % (
        span, len(trip["obd"]), len(trip["imu"]), len(trip["gps"]), len(trip["played"]),
        len(trip["audio"]), len(trip["events"])))
    P("")
    P("LINK")
    if rpm and span > 0:
        fresh = [r for r in rpm if not r[2]]
        P("  replies with data   %.1f /s" % (len(rtt) / span))
        P("  rpm readings        %.1f /s   (%.0f %% repeat the previous value)" % (
            len(rpm) / span, 100.0 * (len(rpm) - len(fresh)) / len(rpm)))
        P("  round trip          median %.0f ms, 5-95 %% %.0f-%.0f ms" % (
            pct(rtt, 0.5) * 1e3, pct(rtt, 0.05) * 1e3, pct(rtt, 0.95) * 1e3))
        gaps = [b[0] - a[0] for a, b in zip(fresh, fresh[1:]) if b[0] - a[0] < 1.0]
        if gaps:
            P("  a NEW rpm value     every %.0f ms (median; 5 %% %.0f ms)" % (
                pct(gaps, 0.5) * 1e3, pct(gaps, 0.05) * 1e3))
        P("  reading's age on arrival  ~%.0f ms (half a round trip) + the ECU's own refresh" % (
            pct(rtt, 0.5) * 500.0))
        if ped:
            P("  pedal readings      %.1f /s" % (len(ped) / span))
        if spd:
            P("  speed readings      %.1f /s" % (len(spd) / span))
    else:
        P("  no rpm readings")
    P("")
    P("AUDIO")
    L = 0.06
    if trip["audio"]:
        a = trip["audio"][-1]
        route_ms, io_ms, depth = a[1], a[2], a[3]
        block = float(trip["meta"].get("block", 512) or 512)
        rate = float(trip["meta"].get("rate", 32000) or 32000)
        q_ms = depth * block / rate * 1e3
        L = (route_ms + io_ms + q_ms) / 1e3
        P("  route               %s" % (a[8] or "?"))
        P("  route latency       %.0f ms" % route_ms)
        P("  hardware buffer     %.0f ms" % io_ms)
        P("  render queue        %.0f ms (%d blocks)" % (q_ms, depth))
        P("  render -> sound     %.0f ms" % (L * 1e3))
        P("  underruns %d, starvations %d, render load %.0f %%" % (a[4], a[5], a[6] * 100))
        routes = sorted(set(x[8] for x in trip["audio"]))
        if len(routes) > 1:
            P("  routes seen: " + " | ".join(routes))
    else:
        P("  no audio lines (assuming %.0f ms)" % (L * 1e3))
    P("")
    P("GEARS (rpm per km/h)")
    gears = gear_clusters(rpm, spd)
    for i, (k, share) in enumerate(gears):
        P("  %d: %6.1f   (%.0f %% of the moving time)" % (i + 1, k, share * 100))
    if not gears:
        P("  not enough speed readings")
    P("")
    P("MOTION")
    mf = motion_fit(trip, rpm, spd, gears)
    if mf:
        axis, gain, r2, n, rest = mf
        P("  forward axis in the phone's frame  (%.2f, %.2f, %.2f)" % tuple(axis))
        P("  the accelerometer explains %.0f %% of the speed's change (R^2, %d windows)" % (
            r2 * 100, n))
        P("  scale %.2f (1.0 = the gear ratios and the accelerometer agree)" % gain)
        P("  noise at rest       %.2f m/s^2 rms" % rest)
    else:
        P("  not enough data (needs the phone's motion lines and driving in gear)")
    P("")
    P("PLAYED vs THE ENGINE at the moment the sound came out")
    pv = played_vs_truth(trip, rpm, L)
    if pv:
        P("  error %.0f rpm rms, wobble %.1f rpm, residual lag %d ms" % pv)
    else:
        P("  not enough data")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    print(report(sys.argv[1]))
