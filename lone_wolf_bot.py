"""
Lone Wolf Labs bot v6 - SAVAGE KILLER
for Builders - Mech Royale (mechfight/1).

Strategy:
- Hunt the weakest, closest enemy.
- Close range aggressively and strafe while shooting.
- Use gun, laser, lightning, and mortar whenever effective.
- Take cubes and second weapons aggressively.
- Escape the zone only when genuinely necessary.
- Retreat only below 18% HP or when heavily outgunned.
"""

from __future__ import annotations

import array
import json
import math
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

import mechfight as mf


def _wrap_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def _dist2d(a: List[float], b: List[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Terrain:
    def __init__(self, map_dir: str) -> None:
        self.ok = False
        try:
            base = os.path.join(map_dir, "v2")
            with open(os.path.join(base, "world.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)["terrain"]
            self.min_x = float(meta["minX"])
            self.min_y = float(meta["minY"])
            self.step = float(meta["step"])
            self.nx = int(meta["nx"])
            self.ny = int(meta["ny"])
            data = array.array("h")
            with open(os.path.join(base, str(meta.get("file", "terrain.i16"))), "rb") as f:
                data.fromfile(f, self.nx * self.ny)
            if sys.byteorder == "big":
                data.byteswap()
            self.data = data
            self.ok = True
        except Exception:
            self.ok = False

    def ground(self, x: float, y: float) -> Optional[float]:
        if not self.ok:
            return None
        i = max(0, min(self.nx - 1, int(round((x - self.min_x) / self.step))))
        j = max(0, min(self.ny - 1, int(round((y - self.min_y) / self.step))))
        return self.data[j * self.nx + i] / 100.0


class Geo:
    """Building footprints (v2/world.json) with a 3D line-of-sight test: does a ray pass through a building?"""

    def __init__(self, map_dir: str) -> None:
        self.ok = False
        self.prisms: List[Tuple[float, float, float, float, float, float, List[float]]] = []
        try:
            with open(os.path.join(map_dir, "v2", "world.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            for p in data.get("prisms", []):
                base = float(p.get("base", 0.0))
                top = float(p.get("top", 10.0))
                if top - base < 1.5:
                    continue
                ring = p.get("ring", [])
                if len(ring) < 6:
                    continue
                xs, ys = ring[0::2], ring[1::2]
                self.prisms.append((min(xs), min(ys), max(xs), max(ys), base, top, ring))
            self.ok = len(self.prisms) > 0
        except Exception:
            self.ok = False

    @staticmethod
    def _inside(px: float, py: float, ring: List[float]) -> bool:
        n = len(ring) // 2
        ins = False
        j = n - 1
        for i in range(n):
            xi, yi = ring[2 * i], ring[2 * i + 1]
            xj, yj = ring[2 * j], ring[2 * j + 1]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi):
                ins = not ins
            j = i
        return ins

    def blocked(self, ax: float, ay: float, az: float, bx: float, by: float, bz: float) -> bool:
        lo_x, hi_x = min(ax, bx), max(ax, bx)
        lo_y, hi_y = min(ay, by), max(ay, by)
        dx, dy = bx - ax, by - ay
        for (x0, y0, x1, y1, base, top, ring) in self.prisms:
            if x1 < lo_x or x0 > hi_x or y1 < lo_y or y0 > hi_y:
                continue
            ts = [0.0, 1.0]
            n = len(ring) // 2
            for i in range(n):
                j = i - 1
                cx, cy, ex, ey = ring[2 * j], ring[2 * j + 1], ring[2 * i], ring[2 * i + 1]
                fx, fy = ex - cx, ey - cy
                d = dx * fy - dy * fx
                if abs(d) < 1e-12:
                    continue
                tt = ((cx - ax) * fy - (cy - ay) * fx) / d
                uu = ((cx - ax) * dy - (cy - ay) * dx) / d
                if 0.0 <= tt <= 1.0 and 0.0 <= uu <= 1.0:
                    ts.append(tt)
            ts.sort()
            for k in range(len(ts) - 1):
                t0, t1 = ts[k], ts[k + 1]
                if t1 - t0 < 1e-9:
                    continue
                tm = 0.5 * (t0 + t1)
                if not self._inside(ax + dx * tm, ay + dy * tm, ring):
                    continue
                za = az + (bz - az) * t0
                zb = az + (bz - az) * t1
                if max(za, zb) >= base and min(za, zb) <= top:
                    return True
        return False


class Bot:
    name = "lone-wolf-v8-savage-plus"

    def __init__(self, hello: mf.Hello) -> None:
        cfg = hello.get("config", {})
        loot_cfg = cfg.get("loot", {})
        self.pickup_radius = float(loot_cfg.get("pickup_radius", 4.0))
        self.upgrades_cfg = cfg.get("upgrades", [])
        self.upgrade_priority = {}

        for u in self.upgrades_cfg:
            uid = str(u.get("id", ""))
            effects = u.get("effects", [])
            score = 0
            for ef in effects:
                path = str(ef.get("path", ""))
                if "damage" in path or "rate" in path or "cooldown" in path:
                    score += 35
                if "speed" in path or "boost" in path or "energy" in path:
                    score += 25
                if "armor" in uid or "rear" in path or "max_hp" in path or "regen" in path:
                    score += 20
                if "sensor" in uid or "vision" in path or "sound" in path:
                    score += 15
            self.upgrade_priority[uid] = score

        self.rng = random.Random(int(hello.get("seed", 1234567)))
        self.last_damage_t = -999.0
        self.last_damage_bearing = None
        self.laser_charging = [False, False]
        self.terrain = Terrain(str(hello.get("map", {}).get("dir", "/arena")))
        self.skip_pickups = {}
        self.loot_mem = None
        self.last_pos = None
        self.stuck_ticks = 0
        self._strafe_dir = self.rng.choice([-1.0, 1.0])
        self._strafe_until = 0.0
        self._hunt_target_id = None
        self._hunt_until = 0.0
        self.geo = Geo(str(hello.get("map", {}).get("dir", "/arena")))
        self.unstick_until = -999.0
        self.unstick_sign = 1.0
        self.skip_crates: Dict[int, float] = {}
        self.crate_id: Optional[int] = None
        self.crate_since = 0.0

    def _pick_upgrade(self, offer: Dict[str, Any]) -> int:
        choices = offer.get("choices", [])
        if not choices:
            return 0

        best_idx = 0
        best_score = -10**9

        for idx, ch in enumerate(choices):
            uid = str(ch.get("id", ""))
            desc = str(ch.get("desc", "")).lower()
            s = self.upgrade_priority.get(uid, 0)

            if "damage" in desc or "rate" in desc or "cooldown" in desc:
                s += 30
            if "speed" in desc or "energy" in desc or "boost" in desc:
                s += 20
            if "armor" in desc or "hp" in desc or "regen" in desc:
                s += 15
            if "vision" in desc or "sensor" in desc or "sound" in desc:
                s += 10

            if s > best_score:
                best_score = s
                best_idx = idx

        return best_idx

    def _zone_target(self, obs: mf.Observation, x: float, y: float) -> Tuple[float, float]:
        zone = obs["zone"]
        t = float(obs["t"])
        shrink_start = float(zone.get("shrink_start", t))
        shrink_end = float(zone.get("shrink_end", t + 1.0))
        cur_c = zone.get("center", [0.0, 0.0])
        cur_r = float(zone.get("radius", 500.0))
        nxt_c = zone.get("next_center", cur_c)
        nxt_r = float(zone.get("next_radius", cur_r))

        if t >= shrink_start:
            if shrink_end > shrink_start:
                frac = max(0.0, min(1.0, (t - shrink_start) / (shrink_end - shrink_start)))
            else:
                frac = 1.0
            cx = cur_c[0] + (nxt_c[0] - cur_c[0]) * frac
            cy = cur_c[1] + (nxt_c[1] - cur_c[1]) * frac
            rad = cur_r + (nxt_r - cur_r) * frac
        else:
            cx, cy, rad = cur_c[0], cur_c[1], cur_r

        safe_r = max(5.0, rad * 0.82)
        dx = nxt_c[0] - cx
        dy = nxt_c[1] - cy
        dist = math.hypot(dx, dy)

        if dist > 1e-6:
            tx = cx + (dx / dist) * min(dist * 0.75, safe_r * 0.55)
            ty = cy + (dy / dist) * min(dist * 0.75, safe_r * 0.55)
        else:
            tx, ty = cx, cy

        my_now = math.hypot(x - cx, y - cy)
        my_next = math.hypot(x - nxt_c[0], y - nxt_c[1])

        if my_now < safe_r and my_next > nxt_r * 0.90:
            return float(nxt_c[0]), float(nxt_c[1])

        return float(tx), float(ty)

    def _fire_decision(
        self,
        slot: int,
        weapon: Optional[mf.WeaponObs],
        dist: float,
        has_target: bool,
        energy: float,
        target_has_mortar: bool,
    ) -> bool:
        if weapon is None or not has_target:
            self.laser_charging[slot] = False
            return False

        kind = weapon.get("kind")
        stats = weapon.get("stats", {})
        max_range = float(stats.get("range", 200.0))

        if kind == "gun":
            ammo = float(weapon.get("ammo", 0.0))
            reload_left = float(weapon.get("reload", 0.0))
            if reload_left > 0.0 or ammo <= 0.0:
                return False
            return dist <= max_range * 0.98

        if kind == "lightning":
            if energy < 10.0 or dist > min(50.0, max_range):
                return False
            return float(weapon.get("cooldown", 0.0)) <= 0.05

        if kind == "mortar":
            if dist < 18.0 or dist > max_range:
                return False
            return float(weapon.get("cooldown", 0.0)) <= 0.05

        if kind == "laser":
            charge_time = float(weapon.get("charge", 0.0))
            full_charge = float(stats.get("charge", 0.75))

            if dist > max_range:
                self.laser_charging[slot] = False
                return False

            target_charge = full_charge * (0.80 if target_has_mortar else 0.95)

            if not self.laser_charging[slot]:
                if float(weapon.get("cooldown", 0.0)) <= 0.05 and energy > 12.0:
                    self.laser_charging[slot] = True
                    return True
                return False

            if charge_time >= target_charge:
                self.laser_charging[slot] = False
                return False

            return True

        return False

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = float(me["pos"][0]), float(me["pos"][1]), float(me["pos"][2])
        hp = float(me.get("hp", 1400.0))
        max_hp = max(1.0, float(me.get("max_hp", 1400.0)))
        hp_ratio = hp / max_hp
        energy = float(me.get("energy", 0.0))
        in_zone = bool(me.get("in_zone", True))
        t = float(obs["t"])
        my_stats = me.get("stats", {})
        muzzle_z = z + float(my_stats.get("muzzle_height", 7.0))

        for ev in obs.get("events", []):
            if ev.get("type") == "damaged":
                self.last_damage_t = t
                if ev.get("bearing") is not None:
                    self.last_damage_bearing = float(ev["bearing"])
            elif ev.get("type") == "take_failed":
                self.skip_pickups[int(ev.get("id", -1))] = t + 25.0

        if self.last_pos is not None:
            moved = math.hypot(x - self.last_pos[0], y - self.last_pos[1])
            if moved < 0.12:
                self.stuck_ticks += 1
            else:
                self.stuck_ticks = 0
        else:
            self.stuck_ticks = 0

        self.last_pos = (x, y)

        act: mf.Action = {"tick": obs["tick"]}

        if obs.get("offer"):
            act["pick"] = self._pick_upgrade(obs["offer"])  # type: ignore[arg-type]

        weapons = me.get("weapons", [None, None])
        gun_slot = weapons[0] if len(weapons) > 0 and weapons[0] and weapons[0].get("kind") == "gun" else None

        bullet_speed = 252.0
        if gun_slot is not None:
            bullet_speed = max(1.0, float(gun_slot.get("stats", {}).get("speed", 252.0)))
            ammo = float(gun_slot.get("ammo", 0.0))
            mag = float(gun_slot.get("stats", {}).get("mag", 12.0))
            reload_left = float(gun_slot.get("reload", 0.0))

            if reload_left <= 0.0 and ammo <= max(1.0, mag * 0.20):
                act["reload"] = True

        pickups = obs.get("pickups", [])

        # Grab any second weapon immediately.
        if len(weapons) > 1 and weapons[1] is None:
            for p in pickups:
                if p.get("kind") in ("laser", "lightning", "mortar"):
                    if _dist2d(p["pos"], [x, y]) <= self.pickup_radius:
                        act["take"] = p["id"]
                        break

        mechs = obs.get("mechs", [])
        enemies = [m for m in mechs if m.get("kind") == "mech"]

        zone = obs["zone"]
        zone_center = zone.get("center", [0.0, 0.0])
        zone_radius = float(zone.get("radius", 500.0))

        nearest_distance = min(
            (math.hypot(m["pos"][0] - x, m["pos"][1] - y) for m in enemies),
            default=999.0,
        )

        recently_hit = (t - self.last_damage_t) < 4.0
        critical_hp = hp_ratio < 0.18

        # Choose a savage target:
        # weakest first, then closest, then cube carrier.
        target = None

        if enemies:
            def kill_score(m: Dict[str, Any]) -> float:
                d = math.dist(m["pos"], [x, y, z])
                enemy_hp = float(m.get("hp", 1400.0))
                enemy_max_hp = max(1.0, float(m.get("max_hp", 1400.0)))
                hp_frac = enemy_hp / enemy_max_hp
                cubes = int(m.get("cubes", 0))

                # Lower is better.
                return d + hp_frac * 55.0 - cubes * 6.0

            target = min(enemies, key=kill_score)

            # Stick with a target briefly so we do not flip-flop.
            if self._hunt_target_id is None or t > self._hunt_until:
                self._hunt_target_id = target.get("id")
                self._hunt_until = t + 2.0

            for m in enemies:
                if m.get("id") == self._hunt_target_id:
                    target = m
                    break

        # Loot aggressively, even when an enemy is moderately close.
        loot_target = None
        loot_dist = 0.0

        if len(weapons) > 1 and weapons[1] is None and in_zone:
            held = {w.get("kind") for w in weapons if w}
            best_score = -1e9

            for p in pickups:
                pk = p.get("kind")

                if pk not in ("laser", "lightning", "mortar") or pk in held:
                    continue

                if self.skip_pickups.get(int(p["id"]), -1.0) > t:
                    continue

                ground = self.terrain.ground(float(p["pos"][0]), float(p["pos"][1]))

                if ground is not None and float(p["pos"][2]) - ground > 2.0:
                    continue

                d = _dist2d(p["pos"], [x, y])

                if d > 250.0:
                    continue

                value = {"laser": 55.0, "lightning": 35.0, "mortar": 15.0}[pk]
                score = value - 0.22 * d

                if nearest_distance < 45.0:
                    score -= 25.0

                if score > best_score:
                    best_score = score
                    loot_target = p
                    loot_dist = d

        if loot_target is not None:
            self.loot_mem = {
                "id": int(loot_target["id"]),
                "kind": loot_target["kind"],
                "pos": [
                    float(loot_target["pos"][0]),
                    float(loot_target["pos"][1]),
                    float(loot_target["pos"][2]),
                ],
                "seen": t,
            }
        elif self.loot_mem is not None:
            mem = self.loot_mem
            mem_dist = _dist2d(mem["pos"], [x, y])

            gone = (
                len(weapons) <= 1
                or weapons[1] is not None
                or not in_zone
                or t - mem["seen"] > 7.0
                or mem_dist < 35.0
                or self.skip_pickups.get(mem["id"], -1.0) > t
            )

            if gone:
                self.loot_mem = None
            else:
                loot_target = mem
                loot_dist = mem_dist

        if loot_target is not None and self.stuck_ticks >= 10:
            self.skip_pickups[int(loot_target["id"])] = t + 12.0
            self.loot_mem = None
            loot_target = None

        zx, zy = self._zone_target(obs, x, y)

        # ---- v8: supply drops (+3 cubes, full heal), cubes (+4% damage/HP each, first upgrade at 1), crates ----
        my_id = me.get("id")
        drop_goal: Optional[Tuple[float, float]] = None
        drop_goal_dist = 0.0
        drop_hold = False
        cube_goal: Optional[Tuple[float, float]] = None
        crate_goal: Optional[Tuple[float, float, float, float, int]] = None
        crate_hold = False
        try:
            best = 1e9
            for dr in obs.get("drops", []):
                dd = _dist2d(dr["pos"], [x, y])
                holder = dr.get("holder")
                if dd <= 110.0 and float(dr.get("lands_in", 99.0)) <= 14.0 and (holder is None or holder == my_id) and dd < best:
                    best = dd
                    drop_goal = (float(dr["pos"][0]), float(dr["pos"][1]))
                    drop_goal_dist = dd
            best = 1e9
            for cb in obs.get("cubes", []):
                dd = _dist2d(cb["pos"], [x, y])
                g = self.terrain.ground(float(cb["pos"][0]), float(cb["pos"][1]))
                if g is not None and float(cb["pos"][2]) - g > 2.5:
                    continue
                if dd <= 35.0 and dd < best:
                    best = dd
                    cube_goal = (float(cb["pos"][0]), float(cb["pos"][1]))
            best = 1e9
            for cr in obs.get("crates", []):
                if self.skip_crates.get(int(cr["id"]), -1.0) > t:
                    continue
                dd = _dist2d(cr["pos"], [x, y])
                g = self.terrain.ground(float(cr["pos"][0]), float(cr["pos"][1]))
                if g is not None and float(cr["pos"][2]) - g > 3.0:
                    continue
                if dd <= 45.0 and dd < best:
                    best = dd
                    crate_goal = (float(cr["pos"][0]), float(cr["pos"][1]), float(cr["pos"][2]), dd, int(cr["id"]))
        except Exception:
            drop_goal, cube_goal, crate_goal = None, None, None

        # Default movement: toward zone target.
        move_x = zx - x
        move_y = zy - y
        move_mode = "zone_advance"

        my_zone_distance = math.hypot(x - zone_center[0], y - zone_center[1])

        # Only escape zone when it truly matters.
        if not in_zone or my_zone_distance >= zone_radius * 0.92:
            move_x = zx - x
            move_y = zy - y
            move_mode = "zone_escape"

        elif critical_hp and enemies:
            # Retreat only when nearly dead.
            closest = min(enemies, key=lambda m: math.hypot(m["pos"][0] - x, m["pos"][1] - y))
            away_x = x - closest["pos"][0]
            away_y = y - closest["pos"][1]
            away_len = max(1e-6, math.hypot(away_x, away_y))

            move_x = (away_x / away_len) * 80.0 + (zx - x) * 0.45
            move_y = (away_y / away_len) * 80.0 + (zy - y) * 0.45
            move_mode = "critical_retreat"

        elif loot_target is not None and nearest_distance >= 60.0:
            # v8: get the second weapon BEFORE chasing anyone (v6 dropped the laser the moment an enemy came into view)
            move_x = float(loot_target["pos"][0]) - x
            move_y = float(loot_target["pos"][1]) - y
            move_mode = "loot_rush"

        elif drop_goal is not None and nearest_distance >= 60.0 and not recently_hit:
            move_x = drop_goal[0] - x
            move_y = drop_goal[1] - y
            move_mode = "drop_run"
            if drop_goal_dist < 3.0:
                drop_hold = True            # stand in the ring, alone, for 4 s
                move_mode = "drop_capture"

        elif cube_goal is not None and nearest_distance >= 60.0:
            move_x = cube_goal[0] - x
            move_y = cube_goal[1] - y
            move_mode = "cube_grab"

        elif crate_goal is not None and nearest_distance >= 70.0:
            if self.crate_id != crate_goal[4]:
                self.crate_id = crate_goal[4]
                self.crate_since = t
            if t - self.crate_since > 8.0:
                self.skip_crates[crate_goal[4]] = t + 40.0     # not working (blocked/out of reach): give up on it
                crate_goal = None
            else:
                move_mode = "crate_farm"
                if crate_goal[3] > 22.0:
                    move_x = crate_goal[0] - x
                    move_y = crate_goal[1] - y
                else:
                    crate_hold = True

        elif target is not None:
            # Hunt aggressively: close distance, strafe, keep shooting.
            tx, ty, tz = target["pos"]
            target_distance = math.hypot(tx - x, ty - y)
            to_target_yaw = math.atan2(ty - y, tx - x)

            if self.rng.random() < 0.04 or t > self._strafe_until:
                self._strafe_dir = self.rng.choice([-1.0, 1.0])
                self._strafe_until = t + self.rng.uniform(1.0, 2.5)

            strafe_yaw = to_target_yaw + self._strafe_dir * (math.pi * 0.5)

            # Aggressive desired range: get close enough to use every weapon.
            if hp_ratio > 0.55:
                desired_range = 45.0
            elif hp_ratio > 0.30:
                desired_range = 65.0
            else:
                desired_range = 90.0

            my_kinds = {w.get("kind") for w in weapons if w}
            their_kinds = set(target.get("weapons", []))
            if "lightning" in my_kinds and "lightning" not in their_kinds and hp_ratio > 0.40:
                desired_range = min(desired_range, 32.0)     # lightning beats laser, and wins up close
            elif "laser" in my_kinds and "lightning" in their_kinds:
                desired_range = max(desired_range, 105.0)    # lightning beats laser: do not let it get close
            elif "mortar" in my_kinds and "lightning" in their_kinds:
                desired_range = max(desired_range, 90.0)     # mortar beats lightning: stay out of arc range

            radial = 0.0

            if target_distance < desired_range * 0.70:
                radial = -0.45
            elif target_distance > desired_range * 1.20:
                radial = 0.85

            move_x = (
                (zx - x) * 0.30
                + math.cos(to_target_yaw) * radial * 60.0
                + math.cos(strafe_yaw) * 40.0
            )
            move_y = (
                (zy - y) * 0.30
                + math.sin(to_target_yaw) * radial * 60.0
                + math.sin(strafe_yaw) * 40.0
            )

            move_mode = "hunt_and_kill"

        elif loot_target is not None:
            move_x = float(loot_target["pos"][0]) - x
            move_y = float(loot_target["pos"][1]) - y
            move_mode = "loot_rush"

        hold_still = drop_hold or crate_hold
        want_jump = False
        move_norm = math.hypot(move_x, move_y)
        if move_norm > 1e-6 and not hold_still:
            # v8: if we are pressing against a wall, sidestep for a moment and hop, instead of freezing there
            if self.stuck_ticks >= 8 and t >= self.unstick_until:
                self.unstick_sign = -self.unstick_sign
                self.unstick_until = t + 1.2
            if t < self.unstick_until:
                move_x, move_y = -move_y * self.unstick_sign, move_x * self.unstick_sign
                want_jump = True
        move_norm = math.hypot(move_x, move_y)

        if move_norm > 1e-6 and not hold_still:
            act["move"] = [move_x / move_norm, move_y / move_norm]
        else:
            act["move"] = [0.0, 0.0]

        # Boost aggressively when hunting, escaping, or rushing loot.
        should_boost = (
            (not in_zone and energy > 15.0)
            or (my_zone_distance >= zone_radius * 0.90 and energy > 20.0)
            or (move_mode == "hunt_and_kill" and energy > 25.0 and nearest_distance > 55.0)
            or (recently_hit and critical_hp and energy > 25.0)
            or (loot_target is not None and loot_dist > 25.0 and energy > 35.0)
            or (move_mode == "drop_run" and drop_goal_dist > 30.0 and energy > 40.0)
        )

        act["boost"] = bool(should_boost)
        act["jump"] = bool(
            me.get("grounded", False)
            and ((self.rng.random() < 0.03 and target is not None) or want_jump)
        )

        if target is not None and move_mode != "crate_farm":
            tpos = target["pos"]
            tvel = target.get("vel", [0.0, 0.0, 0.0])
            horiz_dist = max(1.0, math.hypot(tpos[0] - x, tpos[1] - y))

            lead_t = min(1.0, horiz_dist / bullet_speed)
            predicted_x = tpos[0] + tvel[0] * lead_t
            predicted_y = tpos[1] + tvel[1] * lead_t

            target_cubes = int(target.get("cubes", 0))
            predicted_z = tpos[2] + 4.5 * (1.0 + 0.06 * min(target_cubes, 12))

            yaw = math.atan2(predicted_y - y, predicted_x - x)
            pitch = math.atan2(predicted_z - muzzle_z, horiz_dist)
            pitch = max(-0.85, min(0.85, pitch))

            act["aim"] = [yaw, pitch]

            target_weapons = target.get("weapons", [])
            target_has_mortar = "mortar" in target_weapons

            f0 = self._fire_decision(
                0,
                weapons[0] if len(weapons) > 0 else None,
                horiz_dist,
                True,
                energy,
                target_has_mortar,
            )

            f1 = self._fire_decision(
                1,
                weapons[1] if len(weapons) > 1 else None,
                horiz_dist,
                True,
                energy,
                target_has_mortar,
            )

            fire = [f0, f1]

            my_aim = me.get("aim", [yaw, pitch])
            aim_error = abs(_wrap_angle(float(my_aim[0]) - yaw))

            for i, weapon in enumerate(weapons[:2]):
                if weapon is not None and weapon.get("kind") == "laser" and self.laser_charging[i]:
                    continue

                if aim_error > 0.06:
                    fire[i] = False

            if self.geo.ok:
                try:
                    if (self.geo.blocked(x, y, muzzle_z, float(tpos[0]), float(tpos[1]), float(tpos[2]) + 4.5)
                            and self.geo.blocked(x, y, muzzle_z, float(tpos[0]), float(tpos[1]), float(tpos[2]) + 8.5)):
                        for i, w in enumerate(weapons[:2]):
                            if w is not None and w.get("kind") != "mortar":     # shells arc over walls
                                fire[i] = False
                                if w.get("kind") == "laser":
                                    self.laser_charging[i] = False
                except Exception:
                    pass

            act["fire"] = fire

            mf.draw.line([x, y, muzzle_z], [predicted_x, predicted_y, predicted_z], "#ff2020")

        elif move_mode == "crate_farm" and crate_goal is not None:
            # break the crate with the gun (1 cube, 50% chance of a weapon)
            self.laser_charging = [False, False]
            cx, cy, cz, cdist, _cid = crate_goal
            yaw = math.atan2(cy - y, cx - x)
            pitch = max(-0.85, min(0.85, math.atan2((cz + 1.5) - muzzle_z, max(1.0, cdist))))
            act["aim"] = [yaw, pitch]
            fire = [False, False]
            w0 = weapons[0] if len(weapons) > 0 else None
            if w0 is not None and w0.get("kind") == "gun" and float(w0.get("ammo", 0.0)) > 0.0 and float(w0.get("reload", 0.0)) <= 0.0:
                aligned = abs(_wrap_angle(float(me.get("aim", [yaw, pitch])[0]) - yaw)) < 0.08
                clear = True
                if self.geo.ok:
                    try:
                        clear = not self.geo.blocked(x, y, muzzle_z, cx, cy, cz + 1.5)
                    except Exception:
                        clear = True
                fire[0] = bool(aligned and clear and cdist <= 120.0)
            act["fire"] = fire

        else:
            self.laser_charging = [False, False]

            if recently_hit and self.last_damage_bearing is not None:
                act["aim"] = [_wrap_angle(self.last_damage_bearing), 0.0]
            elif loot_target is not None:
                act["aim"] = [
                    math.atan2(float(loot_target["pos"][1]) - y, float(loot_target["pos"][0]) - x),
                    0.0,
                ]
            else:
                act["aim"] = [math.atan2(zy - y, zx - x), 0.0]

            act["fire"] = [False, False]

        mf.draw.point([zx, zy, z], "#ffcc00", 6)
        mf.draw.label(
            f"SAVAGE+ v8 | hp {hp:.0f}/{max_hp:.0f} | "
            f"mode {move_mode} | enemies {len(enemies)} | "
            f"nearest {nearest_distance:.0f}"
        )

        return act


mf.run(Bot)
