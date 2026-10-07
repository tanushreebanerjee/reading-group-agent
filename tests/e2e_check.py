"""End-to-end acceptance check against the synthetic fixture.

python tests/e2e_check.py --phase 3            # ask mode, speed 4
python tests/e2e_check.py --phase 5            # engaged mode, speed 2, reveal/dismiss, labels, tune

Runs `python -m assistant` on tests/fixtures/synthetic.wav, connects to the
display websocket like the room screen would (pressing Reveal / Dismiss in
phase 5), then checks events.jsonl against the ground truth in
tests/fixtures/synthetic_script.md and synthetic_turns.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from log.events import read_events  # noqa: E402
from tests.fixtures.make_synthetic import parse_script  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


async def ws_client(port: int, phase: int, seen: list, stop: asyncio.Event):
    import websockets

    url = f"ws://127.0.0.1:{port}/ws"
    for _ in range(600):  # the app may spend a while transcribing / warming up
        try:
            ws = await websockets.connect(url)
            break
        except OSError:
            await asyncio.sleep(0.5)
        if stop.is_set():
            return
    else:
        return
    acted = set()
    n_actions = 0
    async with ws:
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            msg = json.loads(raw)
            msg["_wall"] = time.time()
            seen.append(msg)
            if phase == 5 and msg["type"] == "hand_raised" and msg.get("id") not in acted:
                acted.add(msg["id"])
                # 1st hand: reveal, 2nd: dismiss, later: leave pending (logged as ignored)
                action = {0: "reveal", 1: "dismiss"}.get(n_actions)
                n_actions += 1
                if action:
                    await asyncio.sleep(1.0)
                    await ws.send(json.dumps({"action": action}))


def run_app(args, meeting_dir: Path) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "assistant", "--paper", args.paper, "--replay", str(FIX / "synthetic.wav"),
           "--speed", str(args.speed), "--mode", args.mode, "--meeting-dir", str(meeting_dir),
           "--hold", str(args.hold), "--port", str(args.port)]
    print("$", " ".join(cmd), flush=True)
    log = open(meeting_dir / "app.log", "w")
    return subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env={**os.environ})


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def check_ask(events, meta, turns, max_latency) -> list[tuple[bool, str]]:
    res = []
    answers = [e for e in events if e["kind"] == "answer"]
    by_turn = {t["turn"]: t for t in turns}
    asks = [e for e in meta["events"] if e["kind"] == "ask"]
    matched = set()
    for ev in asks:
        t = by_turn[ev["turn"]]
        cands = [a for a in answers if t["start"] - 3 <= a["q_start"] <= t["end"] + 3]
        if not cands:
            res.append((False, f"{ev['id']}: no answer for question at {t['start']:.0f}s"))
            continue
        a = cands[0]
        matched.add(a["id"])
        # each expected term may list alternatives: "15.656|15.66"
        groups = [t.split("|") for t in ev["expect_terms"]]
        found = [next((alt for alt in g if norm(alt) in norm(a["text"])), None) for g in groups]
        res.append((all(found), f"{ev['id']}: expected {ev['expect_terms']} found {found}"))
        res.append((bool(a["cited"]), f"{ev['id']}: cited={a['cited']}"))
        fw = a.get("first_words_s")
        res.append((fw is not None and fw <= max_latency,
                    f"{ev['id']}: first words on screen {fw}s after question end (<= {max_latency}s); "
                    f"complete answer {a['latency_s']:.1f}s"))
        res.append((True, f"{ev['id']}: Q={a['question']!r}\n        A={a['text']!r}"))
    extra = [a for a in answers if a["id"] not in matched]
    res.append((not extra, f"no spurious answers ({len(extra)} extra: {[a['question'] for a in extra]})"))
    return res


def check_engaged(events, meta, turns, ws_msgs) -> list[tuple[bool, str]]:
    res = []
    by_turn = {t["turn"]: t for t in turns}
    hands = [e for e in events if e["kind"] == "hand"]
    statuses = {e["id"]: e["status"] for e in events if e["kind"] == "hand_status"}
    for ev in [e for e in meta["events"] if e["kind"] in ("contradiction", "gap")]:
        t0 = by_turn[ev["turn"]]["start"]
        hit = [h for h in hands if h["trigger"] == ev["kind"] and t0 <= h["t"] <= t0 + 90]
        trig = [e for e in events if e["kind"] == "trigger" and e["trigger"] == ev["kind"]
                and t0 <= e["t"] <= t0 + 90]
        detail = (f"hand {hit[0]['id']} at {hit[0]['t']:.0f}s (planted at {t0:.0f}s, conf {hit[0]['confidence']:.2f})"
                  if hit else f"no hand; triggers in window: {[(e['id'], e['confidence'], e['outcome']) for e in trig]}")
        res.append((bool(hit), f"{ev['id']} ({ev['kind']}): {detail}"))
        if hit:
            res.append((True, f"        reason: {hit[0]['reason']!r}\n        prepared: {hit[0]['text']!r}"))
    fp = [h for h in hands if not any(
        h["trigger"] == ev["kind"] and by_turn[ev["turn"]]["start"] <= h["t"] <= by_turn[ev["turn"]]["start"] + 90
        for ev in meta["events"] if ev["kind"] in ("contradiction", "gap"))]
    res.append((len(fp) <= 1, f"false-positive hands: {len(fp)} {[(h['id'], h['trigger'], h['reason'][:80]) for h in fp]}"))
    revealed = [m for m in ws_msgs if m["type"] == "hand_revealed"]
    raised_msgs = [m for m in ws_msgs if m["type"] == "hand_raised"]
    leaked = [m for m in raised_msgs if "text" in m or "reason" in m]
    res.append((not leaked, "hand_raised messages carry only the trigger type (no content)"))
    res.append(("revealed" in statuses.values() and bool(revealed), f"reveal works (statuses {statuses})"))
    if len(hands) >= 2:
        res.append(("dismissed" in statuses.values(), "dismiss works"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, choices=[3, 5], required=True)
    ap.add_argument("--paper", default="papers/test.pdf")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--hold", type=float, default=4)
    ap.add_argument("--max-latency", type=float, default=5.0)
    ap.add_argument("--meeting-dir", default=None)
    args = ap.parse_args()
    args.mode = "ask" if args.phase == 3 else "engaged"
    args.speed = args.speed or (4.0 if args.phase == 3 else 2.0)

    meta, _ = parse_script(FIX / "synthetic_script.md")
    turns = json.loads((FIX / "synthetic_turns.json").read_text())
    meeting_dir = Path(args.meeting_dir or ROOT / "meetings" / f"e2e_phase{args.phase}_{time.strftime('%Y%m%d_%H%M%S')}")
    meeting_dir.mkdir(parents=True, exist_ok=True)

    proc = run_app(args, meeting_dir)
    seen: list = []

    async def drive():
        stop = asyncio.Event()
        client = asyncio.create_task(ws_client(args.port, args.phase, seen, stop))
        while proc.poll() is None:
            await asyncio.sleep(0.5)
        stop.set()
        await client

    asyncio.run(drive())
    print(f"app exited with {proc.returncode}; log: {meeting_dir / 'app.log'}")
    events = read_events(meeting_dir / "events.jsonl")

    results = check_ask(events, meta, turns, args.max_latency)
    if args.phase == 5:
        results += check_engaged(events, meta, turns, seen)
    log_md = meeting_dir / "log.md"
    results.append((log_md.exists(), f"log.md written ({log_md})"))
    results.append((any(m["type"] == "answer_done" for m in seen), "display received answers over websocket"))

    ok = all(r for r, _ in results)
    for r, msg in results:
        print(("PASS " if r else "FAIL ") + msg)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
