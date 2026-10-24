// OpenStreetMap vector tiles from OpenFreeMap: free, no key, no rate limit, about a second for a venue's
// surroundings. Same tile maths as roomtobreathe/surroundings/tiles.py, which decodes them in the engine.
const TILEJSON = "https://tiles.openfreemap.org/planet", TILE_ZOOM = 14;
let template = null;

export function tilesAround(lat, lon, radiusM, z = TILE_ZOOM) {
  const n = 2 ** z, rad = (d) => (d * Math.PI) / 180;
  const tx = (lo) => ((lo + 180) / 360) * n;
  const ty = (la) => ((1 - Math.asinh(Math.tan(rad(la))) / Math.PI) / 2) * n;
  const dlat = radiusM / 111320, dlon = radiusM / (111320 * Math.cos(rad(lat)));
  const out = [];
  for (let x = Math.floor(tx(lon - dlon)); x <= Math.floor(tx(lon + dlon)); x++)
    for (let y = Math.floor(ty(lat + dlat)); y <= Math.floor(ty(lat - dlat)); y++) out.push({ z, x, y });
  return out;
}

// Tiles covering ``radiusM`` around a point, as {z, x, y, data: Uint8Array}.
export async function fetchTiles(lat, lon, radiusM, onStatus) {
  onStatus?.("Downloading the streets around the venue (OpenStreetMap vector tiles)…");
  if (!template) template = (await (await fetch(TILEJSON)).json()).tiles[0];
  const tiles = await Promise.all(tilesAround(lat, lon, radiusM).map(async (t) => {
    const r = await fetch(template.replace("{z}", t.z).replace("{x}", t.x).replace("{y}", t.y));
    if (!r.ok) throw new Error(`map tile ${t.z}/${t.x}/${t.y}: ${r.status}`);
    return { ...t, data: new Uint8Array(await r.arrayBuffer()) };
  }));
  return { tiles, missing: [] };
}
