import L from "leaflet";
import "leaflet.markercluster";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import { useEffect, useRef } from "react";
import { thumbUrl, type MapPoint } from "../api";

/** Harita döşemeleri: OpenStreetMap (anahtarsız, kişisel kullanıma uygun). Fotoğraflar gönderilmez; yalnızca görülen alan istenir.
 * Koyu temada döşemeler CSS ile koyulaştırılır (.map.dark). */
function tiles() {
  return L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap katkıda bulunanlar", maxZoom: 19,
  });
}

const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t === "dark" || (t !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
};

/**
 * Fotoğraf haritası: yakın noktalar küçük resimli kümelere toplanır (Fotoğraflar uygulaması gibi).
 * browse: kümeye/noktaya tıklayınca onSelect(o kümedeki noktalar). pick: haritaya tıklayınca onPick(enlem, boylam).
 */
export function MapView({ repoId, points, onSelect, pick, onPick, height = "100%" }: {
  repoId: string;
  points: MapPoint[];
  onSelect?: (pts: MapPoint[]) => void;
  pick?: { lat: number; lon: number } | null;
  onPick?: (lat: number, lon: number) => void;
  height?: number | string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.MarkerClusterGroup | null>(null);
  const pickMarker = useRef<L.CircleMarker | null>(null);
  const cb = useRef({ onSelect, onPick });
  cb.current = { onSelect, onPick };

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, worldCopyJump: true }).setView([39, 35], 5);
    tiles().addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => cb.current.onPick?.(e.latlng.lat, e.latlng.lng));
    map.current = m;
    const ro = new ResizeObserver(() => m.invalidateSize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
    };
  }, []);

  // Noktalar → küçük resimli kümeler
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    layer.current?.remove();
    if (!points.length) return;
    const icon = (blake: string, n: number) => L.divIcon({
      className: "map-pin",
      html: `<div class="map-thumb"><img src="${thumbUrl(repoId, blake, "s")}" alt="">${n > 1 ? `<span>${n > 999 ? "999+" : n}</span>` : ""}</div>`,
      iconSize: [52, 52], iconAnchor: [26, 26],
    });
    const group = L.markerClusterGroup({
      showCoverageOnHover: false, maxClusterRadius: 60, spiderfyOnMaxZoom: false, zoomToBoundsOnClick: false,
      iconCreateFunction: (c) => {
        const ms = c.getAllChildMarkers();
        return icon((ms[0].options as { blake?: string }).blake ?? "", ms.length);
      },
    });
    const markers = points.map((p) => {
      const mk = L.marker([p[0], p[1]], { icon: icon(p[2], 1), blake: p[2] } as L.MarkerOptions);
      (mk as unknown as { point: MapPoint }).point = p;
      mk.on("click", () => cb.current.onSelect?.([p]));
      return mk;
    });
    group.addLayers(markers);
    group.on("clusterclick", (e: L.LeafletEvent & { layer: L.MarkerCluster }) => {
      const c = e.layer;
      const pts = c.getAllChildMarkers().map((mk: L.Marker) => (mk as unknown as { point: MapPoint }).point);
      // Yakınlaştırılabiliyorsa yakınlaş, en yakında ise içindekileri göster
      if (m.getZoom() < m.getMaxZoom() - 2 && c.getBounds().getNorthEast().distanceTo(c.getBounds().getSouthWest()) > 50) c.zoomToBounds({ padding: [40, 40] });
      cb.current.onSelect?.(pts);
    });
    group.addTo(m);
    layer.current = group;
    if (!pick) m.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 13 });
  }, [points, repoId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Seçme kipi: seçilen nokta
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    pickMarker.current?.remove();
    pickMarker.current = null;
    if (pick) {
      pickMarker.current = L.circleMarker([pick.lat, pick.lon], { radius: 9, color: "#fff", weight: 3, fillColor: "#007aff", fillOpacity: 1 }).addTo(m);
    }
  }, [pick]);

  return <div ref={el} className={isDark() ? "map dark" : "map"} style={{ height, width: "100%" }} />;
}
