"""Procedural Clinical Trial Map Generator

Generates a Tiled-format JSON tilemap and a waypoint metadata file
for N patients. Layout:

  Hospital on the left, main horizontal road extending right.
  Vertical side streets branch off at regular intervals.
  Houses placed along side streets (above and below main road).

Tile IDs (Kenney Tiny Town tilemap_packed.png, 12 cols x 11 rows):
  Grass:    1, 2, 3  (variants)
  Road H:   13(top-left), 14(top), 15(top-right),
            25(mid-left), 26(mid), 27(mid-right),
            37(bot-left), 38(bot), 39(bot-right)
  Road V:   Same tiles rotated conceptually — we use
            25/26/27 for vertical segments too (center row)
  House A roof:  49, 50, 51   wall: 61, 64, 63
  House B roof:  53, 54, 55   wall: 65, 68, 67
  Hospital roof: 73,74,75,76  wall: 85,86,87,88
  Hospital alt:  78,79,80     wall: 89,90,92
  Decor: 5,6,7,8,10,19,20,29,30,46,96
"""

import json
import math
import random
import sys
from pathlib import Path

# Tile constants
GRASS = [1, 2, 3]
ROAD_TOP = [13, 14, 15]  # top edge of horizontal road
ROAD_MID = [25, 26, 27]  # middle of road
ROAD_BOT = [37, 38, 39]  # bottom edge of horizontal road
DECOR = [5, 6, 7, 8, 10, 19, 20]

HOUSE_A_ROOF = [49, 50, 51]
HOUSE_A_WALL = [61, 64, 63]
HOUSE_B_ROOF = [53, 54, 55]
HOUSE_B_WALL = [65, 68, 67]

HOSP_ROOF_A = [73, 74, 75, 76]
HOSP_WALL_A = [85, 86, 87, 88]
HOSP_ROOF_B = [78, 79, 80]
HOSP_WALL_B = [89, 90, 92]

# Layout parameters
TILE = 16
ROAD_WIDTH = 3  # 3 tiles tall for horizontal road
STREET_WIDTH = 3  # 3 tiles wide for vertical streets
HOUSE_W = 3  # house is 3 tiles wide
HOUSE_H = 2  # house is 2 tiles tall (roof + wall)
HOSP_W = 5  # hospital is 5 tiles wide
HOSP_H = 2  # hospital is 2 tiles tall
STREET_SPACING = 11  # tiles between side streets (center to center); must be >= 2*HOUSE_W + STREET_WIDTH + 2
HOUSE_OFFSET = 2  # tiles from road edge to house


def generate_map(n_patients: int, seed: int = 42) -> tuple[dict, dict]:
    """Generate tilemap JSON and waypoint metadata for N patients.

    Layout: Hospital in CENTER, main road extends left and right,
    side streets with houses branch off the main road.
    """
    rng = random.Random(seed)

    # Calculate layout dimensions
    n_streets = math.ceil(n_patients / 4)  # 4 houses per street (2 above, 2 below)
    n_streets = max(n_streets, 2)

    # Split streets evenly: left of hospital + right of hospital
    n_left = n_streets // 2
    n_right = n_streets - n_left

    # Map dimensions
    left_wing_w = n_left * STREET_SPACING + 4
    right_wing_w = n_right * STREET_SPACING + 4
    hosp_area_w = HOSP_W + 6  # hospital + margin on each side
    map_w = left_wing_w + hosp_area_w + right_wing_w
    map_w = max(map_w, 30)

    above_road = HOUSE_OFFSET + HOUSE_H + 4
    below_road = HOUSE_OFFSET + HOUSE_H + 4
    map_h = above_road + ROAD_WIDTH + below_road + 2
    map_h = max(map_h, 22)

    # Key Y positions
    road_y = above_road + 1
    road_center_y = road_y + 1

    # Hospital position (CENTER)
    hosp_x = left_wing_w + 3
    hosp_y = road_center_y - 1

    # Initialize layers
    ground = [0] * (map_w * map_h)
    buildings = [0] * (map_w * map_h)
    roofs = [0] * (map_w * map_h)
    decor = [0] * (map_w * map_h)

    def idx(x, y):
        return y * map_w + x

    def set_tile(layer, x, y, tile_id):
        if 0 <= x < map_w and 0 <= y < map_h:
            layer[idx(x, y)] = tile_id

    def get_tile(layer, x, y):
        if 0 <= x < map_w and 0 <= y < map_h:
            return layer[idx(x, y)]
        return 0

    # Fill ground with grass
    for y in range(map_h):
        for x in range(map_w):
            set_tile(ground, x, y, rng.choice(GRASS))

    # Draw main horizontal road (full width)
    road_start_x = 2
    road_end_x = map_w - 3
    for x in range(road_start_x, road_end_x + 1):
        set_tile(ground, x, road_y, 14)
        set_tile(ground, x, road_y + 1, 26)
        set_tile(ground, x, road_y + 2, 38)
    # Caps
    set_tile(ground, road_start_x, road_y, 13)
    set_tile(ground, road_start_x, road_y + 1, 25)
    set_tile(ground, road_start_x, road_y + 2, 37)
    set_tile(ground, road_end_x, road_y, 15)
    set_tile(ground, road_end_x, road_y + 1, 27)
    set_tile(ground, road_end_x, road_y + 2, 39)

    # Place hospital building (centered)
    for i in range(HOSP_W):
        tile = HOSP_ROOF_A[i % len(HOSP_ROOF_A)]
        set_tile(roofs, hosp_x + i, hosp_y, tile)
    for i in range(HOSP_W):
        tile = HOSP_WALL_A[i % len(HOSP_WALL_A)]
        set_tile(buildings, hosp_x + i, hosp_y + 1, tile)

    # ─── Build street positions: left streets + right streets ───
    homes = []
    waypoints = []

    hosp_entrance_x = hosp_x + HOSP_W // 2
    waypoints.append({
        "id": "hosp",
        "x": hosp_entrance_x,
        "y": road_center_y,
        "neighbors": [],
    })

    # Compute street X positions
    # Left streets: from hospital leftward
    left_street_xs = []
    for i in range(n_left):
        sx = hosp_x - 3 - (i + 1) * STREET_SPACING + STREET_SPACING // 2
        left_street_xs.append(sx)
    left_street_xs.reverse()  # closest to hospital first in array

    # Right streets: from hospital rightward
    right_street_xs = []
    for i in range(n_right):
        sx = hosp_x + HOSP_W + 3 + i * STREET_SPACING + STREET_SPACING // 2
        right_street_xs.append(sx)

    all_street_xs = left_street_xs + right_street_xs

    patient_idx = 0
    prev_road_wp_id = "hosp"

    # Sort streets by distance from hospital (alternating left/right)
    street_order = []
    li, ri = 0, 0
    for _ in range(n_streets):
        if li < len(left_street_xs) and ri < len(right_street_xs):
            # Alternate
            if _ % 2 == 0 and ri < len(right_street_xs):
                street_order.append(("right", ri))
                ri += 1
            else:
                street_order.append(("left", li))
                li += 1
        elif ri < len(right_street_xs):
            street_order.append(("right", ri))
            ri += 1
        else:
            street_order.append(("left", li))
            li += 1

    # Build linear chain: hosp -> nearest streets -> farther streets
    # Sort by distance from hospital
    street_defs = []
    for side, idx_in_side in street_order:
        if side == "right":
            sx = right_street_xs[idx_in_side]
        else:
            sx = left_street_xs[idx_in_side]
        street_defs.append((sx, side, idx_in_side))

    # Sort by x for waypoint chaining (left to right)
    street_defs.sort(key=lambda s: s[0])

    prev_main_wp = None  # previous street's main road waypoint

    for si, (street_x, side, _) in enumerate(street_defs):
        if street_x < road_start_x + 2 or street_x > road_end_x - 2:
            continue

        # Draw vertical street (above road)
        for y in range(road_y - HOUSE_OFFSET - HOUSE_H - 1, road_y):
            set_tile(ground, street_x - 1, y, 25)
            set_tile(ground, street_x, y, 26)
            set_tile(ground, street_x + 1, y, 27)
        # Draw vertical street (below road)
        for y in range(road_y + ROAD_WIDTH, road_y + ROAD_WIDTH + HOUSE_OFFSET + HOUSE_H + 1):
            set_tile(ground, street_x - 1, y, 25)
            set_tile(ground, street_x, y, 26)
            set_tile(ground, street_x + 1, y, 27)

        # Intersection
        for ry in [road_y, road_y + 2]:
            set_tile(ground, street_x - 1, ry, 25)
            set_tile(ground, street_x, ry, 26)
            set_tile(ground, street_x + 1, ry, 27)

        # Waypoints
        street_main_id = f"road_{si}"
        street_top_id = f"st_{si}_top"
        street_bot_id = f"st_{si}_bot"

        top_wp_y = road_y - HOUSE_OFFSET
        bot_wp_y = road_y + ROAD_WIDTH + HOUSE_OFFSET - 1

        waypoints.append({
            "id": street_main_id,
            "x": street_x,
            "y": road_center_y,
            "neighbors": [street_top_id, street_bot_id],
        })
        waypoints.append({
            "id": street_top_id,
            "x": street_x,
            "y": top_wp_y,
            "neighbors": [street_main_id],
        })
        waypoints.append({
            "id": street_bot_id,
            "x": street_x,
            "y": bot_wp_y,
            "neighbors": [street_main_id],
        })

        # Chain to previous waypoint on main road
        street_wp = next(w for w in waypoints if w["id"] == street_main_id)
        if prev_main_wp is None:
            # Connect first street to hospital
            waypoints[0]["neighbors"].append(street_main_id)
            street_wp["neighbors"].append("hosp")
        else:
            street_wp["neighbors"].append(prev_main_wp)
            prev = next(w for w in waypoints if w["id"] == prev_main_wp)
            prev["neighbors"].append(street_main_id)
        prev_main_wp = street_main_id

        # Place houses (up to 4 per street)
        house_positions = [
            (street_x - HOUSE_W - 1, road_y - HOUSE_OFFSET - HOUSE_H, "above_left", street_top_id),
            (street_x + 2, road_y - HOUSE_OFFSET - HOUSE_H, "above_right", street_top_id),
            (street_x - HOUSE_W - 1, road_y + ROAD_WIDTH + HOUSE_OFFSET, "below_left", street_bot_id),
            (street_x + 2, road_y + ROAD_WIDTH + HOUSE_OFFSET, "below_right", street_bot_id),
        ]

        for hx, hy, pos_label, parent_wp_id in house_positions:
            if patient_idx >= n_patients:
                break

            if patient_idx % 2 == 0:
                roof_tiles, wall_tiles = HOUSE_A_ROOF, HOUSE_A_WALL
            else:
                roof_tiles, wall_tiles = HOUSE_B_ROOF, HOUSE_B_WALL

            for i, t in enumerate(roof_tiles):
                set_tile(roofs, hx + i, hy, t)
            for i, t in enumerate(wall_tiles):
                set_tile(buildings, hx + i, hy + 1, t)

            home_wp_id = f"h{patient_idx}"
            home_wp_x = hx + 1
            home_wp_y = hy + 2 if "above" in pos_label else hy - 1

            homes.append({
                "patient_idx": patient_idx,
                "x": home_wp_x,
                "y": home_wp_y,
                "house_x": hx,
                "house_y": hy,
                "waypoint_id": home_wp_id,
            })

            waypoints.append({
                "id": home_wp_id,
                "x": home_wp_x,
                "y": home_wp_y,
                "neighbors": [parent_wp_id],
            })
            parent = next(w for w in waypoints if w["id"] == parent_wp_id)
            parent["neighbors"].append(home_wp_id)

            patient_idx += 1

        if patient_idx >= n_patients:
            break

    # Also connect hospital to nearest street on the other side if not connected
    # (ensure full connectivity: left streets ← hosp → right streets)
    hosp_wp = waypoints[0]
    if len(hosp_wp["neighbors"]) == 1 and len(street_defs) > 1:
        # Only connected to one side; find nearest street on other side
        connected_x = None
        for w in waypoints:
            if w["id"] == hosp_wp["neighbors"][0]:
                connected_x = w["x"]
                break
        for w in waypoints:
            if w["id"].startswith("road_") and w["id"] != hosp_wp["neighbors"][0]:
                if connected_x is not None and (
                    (w["x"] < hosp_entrance_x and connected_x > hosp_entrance_x) or
                    (w["x"] > hosp_entrance_x and connected_x < hosp_entrance_x)
                ):
                    hosp_wp["neighbors"].append(w["id"])
                    w["neighbors"].append("hosp")
                    break

    # Scattered decor
    for _ in range(map_w * map_h // 12):
        dx = rng.randint(0, map_w - 1)
        dy = rng.randint(0, map_h - 1)
        if (get_tile(ground, dx, dy) in GRASS
                and get_tile(buildings, dx, dy) == 0
                and get_tile(roofs, dx, dy) == 0):
            set_tile(decor, dx, dy, rng.choice(DECOR))

    # Build Tiled JSON
    tilemap = {
        "compressionlevel": -1,
        "height": map_h,
        "width": map_w,
        "infinite": False,
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "tileheight": TILE,
        "tilewidth": TILE,
        "tiledversion": "1.9.0",
        "type": "map",
        "version": "1.9",
        "nextlayerid": 5,
        "nextobjectid": 1,
        "tilesets": [{
            "columns": 12,
            "firstgid": 1,
            "image": "../kenney/tiny-town/Tilemap/tilemap_packed.png",
            "imageheight": 176,
            "imagewidth": 192,
            "margin": 0,
            "name": "tiny-town",
            "spacing": 0,
            "tilecount": 132,
            "tileheight": TILE,
            "tilewidth": TILE,
        }],
        "layers": [
            _make_layer("Ground", 1, map_w, map_h, ground),
            _make_layer("Buildings", 2, map_w, map_h, buildings),
            _make_layer("Roofs", 3, map_w, map_h, roofs),
            _make_layer("Decor", 4, map_w, map_h, decor),
        ],
    }

    # Build metadata
    meta = {
        "n_patients": n_patients,
        "map_width": map_w,
        "map_height": map_h,
        "tile_size": TILE,
        "hospital": {
            "x": hosp_x + HOSP_W // 2,
            "y": hosp_y + 1,
            "waypoint_id": "hosp",
        },
        "homes": homes,
        "waypoints": waypoints,
    }

    return tilemap, meta


def _make_layer(name: str, layer_id: int, w: int, h: int, data: list) -> dict:
    return {
        "data": data,
        "height": h,
        "width": w,
        "id": layer_id,
        "name": name,
        "type": "tilelayer",
        "visible": True,
        "opacity": 1,
        "x": 0,
        "y": 0,
    }


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "static_dirs" / "assets" / "map"
    out_dir.mkdir(parents=True, exist_ok=True)

    tilemap, meta = generate_map(n)

    tilemap_path = out_dir / "clinical_trial.json"
    meta_path = out_dir / "map_meta.json"

    tilemap_path.write_text(json.dumps(tilemap), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Generated map for {n} patients: {meta['map_width']}x{meta['map_height']} tiles")
    print(f"  Tilemap: {tilemap_path}")
    print(f"  Metadata: {meta_path}")
    print(f"  Homes: {len(meta['homes'])}, Waypoints: {len(meta['waypoints'])}")


if __name__ == "__main__":
    main()
