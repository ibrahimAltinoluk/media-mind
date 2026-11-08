import { api } from "./api";
import type { AssetItem } from "./types";

/** Asset ayrıntısından görüntüleyici öğesi üretir (kümeden fotoğraf açmak için). */
export async function assetItem(repoId: string, assetId: number): Promise<AssetItem> {
  const d = await api.asset(repoId, assetId);
  const f = d.files.find((x) => x.state === "present") ?? d.files[0];
  return {
    file_id: f.id, rel_path: f.rel_path, tracked: f.tracked, size: d.size, asset_id: d.id, blake3: d.blake3, kind: d.kind, mime: d.mime,
    width: d.width, height: d.height, duration_ms: d.duration_ms, taken_at: d.taken_at, taken_at_source: d.taken_at_source,
    camera_model: d.camera_model, rating: d.rating, favorite: d.favorite, lat: d.lat, lon: d.lon, place: d.place, region: d.region,
    country_code: d.country_code, copies: 1, in_dup: 0,
  };
}

