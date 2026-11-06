import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

function I({ size = 18, children, ...rest }: P & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
      {children}
    </svg>
  );
}

export const Logo = (p: P) => <I {...p}><rect x="3" y="3" width="18" height="18" rx="4" /><circle cx="9" cy="9" r="2" /><path d="M21 15l-5-5L5 21" /></I>;
export const Chevron = (p: P) => <I {...p}><path d="M7 10l5 5 5-5" /></I>;
export const Search = (p: P) => <I {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></I>;
export const Refresh = (p: P) => <I {...p}><path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 4v5h-5" /></I>;
export const Chat = (p: P) => <I {...p}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></I>;
export const Close = (p: P) => <I {...p}><path d="M6 6l12 12M18 6L6 18" /></I>;
export const Left = (p: P) => <I {...p}><path d="M15 5l-7 7 7 7" /></I>;
export const Right = (p: P) => <I {...p}><path d="M9 5l7 7-7 7" /></I>;
export const Info = (p: P) => <I {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></I>;
export const Trash = (p: P) => <I {...p}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></I>;
export const Folder = (p: P) => <I {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></I>;
export const Plus = (p: P) => <I {...p}><path d="M12 5v14M5 12h14" /></I>;
export const Dots = (p: P) => <I {...p} fill="currentColor" stroke="none"><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></I>;
export const Check = (p: P) => <I {...p}><path d="M5 12l5 5 9-10" /></I>;
export const Heart = ({ filled, ...p }: P & { filled?: boolean }) => <I {...p} fill={filled ? "currentColor" : "none"}><path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.5-7 10-7 10z" /></I>;
export const Star = ({ filled, ...p }: P & { filled?: boolean }) => <I {...p} fill={filled ? "currentColor" : "none"}><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z" /></I>;
export const Play = (p: P) => <I {...p} fill="currentColor" stroke="none"><path d="M7 4l13 8-13 8z" /></I>;
export const Pause = (p: P) => <I {...p} fill="currentColor" stroke="none"><rect x="6" y="4" width="4" height="16" /><rect x="14" y="4" width="4" height="16" /></I>;
export const Image = (p: P) => <I {...p} strokeWidth={1.5}><path d="M3 17l5-6 4 4 3-3 6 5" /><circle cx="16" cy="7" r="2" /></I>;
export const Arrow = (p: P) => <I {...p}><path d="M5 12h14M13 6l6 6-6 6" /></I>;
export const Shield = (p: P) => <I {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /></I>;
export const Copy = (p: P) => <I {...p}><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" /></I>;
export const Zoom = (p: P) => <I {...p}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5M8 11h6M11 8v6" /></I>;
export const Sliders = (p: P) => <I {...p}><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></I>;

// SF Symbols benzeri kenar çubuğu/araç simgeleri
export const Photos = (p: P) => <I {...p} strokeWidth={1.7}><rect x="3" y="5" width="15" height="13" rx="2.5" /><path d="M7 2.8h11.5A2.5 2.5 0 0 1 21 5.3V15" /><path d="M5 15l3.5-3.5 3 3 2-2 3.5 3.5" /><circle cx="13.5" cy="9" r="1.3" /></I>;
export const Film = (p: P) => <I {...p} strokeWidth={1.7}><rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="M7 5v14M17 5v14M3 9.5h4M3 14.5h4M17 9.5h4M17 14.5h4" /></I>;
export const People = (p: P) => <I {...p} strokeWidth={1.7}><circle cx="9" cy="8.5" r="3.2" /><path d="M3 19c.8-3.4 3.2-5.2 6-5.2s5.2 1.8 6 5.2" /><circle cx="17" cy="9.5" r="2.4" /><path d="M16.2 14.1c2.4.2 4 1.8 4.8 4.4" /></I>;
export const Pin = (p: P) => <I {...p} strokeWidth={1.7}><path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></I>;
export const Sparkles = (p: P) => <I {...p} strokeWidth={1.7}><path d="M10 3.5l1.6 4.4 4.4 1.6-4.4 1.6L10 15.5l-1.6-4.4L4 9.5l4.4-1.6z" /><path d="M18 14l.8 2.2 2.2.8-2.2.8L18 20l-.8-2.2-2.2-.8 2.2-.8z" /></I>;
export const Gear = (p: P) => <I {...p} strokeWidth={1.6}><path d="M19.07 9.81 L21.46 10.38 L21.46 13.62 L19.07 14.19 L18.55 15.45 L19.84 17.54 L17.54 19.84 L15.45 18.55 L14.19 19.07 L13.62 21.46 L10.38 21.46 L9.81 19.07 L8.55 18.55 L6.46 19.84 L4.16 17.54 L5.45 15.45 L4.93 14.19 L2.54 13.62 L2.54 10.38 L4.93 9.81 L5.45 8.55 L4.16 6.46 L6.46 4.16 L8.55 5.45 L9.81 4.93 L10.38 2.54 L13.62 2.54 L14.19 4.93 L15.45 5.45 L17.54 4.16 L19.84 6.46 L18.55 8.55 Z" strokeLinejoin="round" /><circle cx="12" cy="12" r="3" /></I>;
export const Clock = (p: P) => <I {...p} strokeWidth={1.7}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></I>;
export const Stack = (p: P) => <I {...p} strokeWidth={1.7}><rect x="7.5" y="7.5" width="13" height="13" rx="2.5" /><path d="M16.5 4.5V4a1.5 1.5 0 0 0-1.5-1.5H5A1.5 1.5 0 0 0 3.5 4v10A1.5 1.5 0 0 0 5 15.5h.5" /></I>;
export const Tray = (p: P) => <I {...p} strokeWidth={1.7}><path d="M3.5 13.5l2.4-7.3A1.8 1.8 0 0 1 7.6 5h8.8a1.8 1.8 0 0 1 1.7 1.2l2.4 7.3V18a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z" /><path d="M3.5 13.5h4.7l1.3 2h5l1.3-2h4.7" /></I>;
export const FolderTree = (p: P) => <I {...p} strokeWidth={1.7}><path d="M3.5 7a2 2 0 0 1 2-2h3.8l1.8 2h7.4a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" /><path d="M8 11.5h8M8 15h5" /></I>;
export const Filter = (p: P) => <I {...p} strokeWidth={1.8}><path d="M4 6h16M7 12h10M10 18h4" /></I>;
export const Grid = (p: P) => <I {...p} strokeWidth={1.7}><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" /><rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /></I>;
export const Ellipsis = (p: P) => <I {...p} fill="currentColor" stroke="none"><circle cx="6" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="18" cy="12" r="1.6" /></I>;
export const PersonPlus = (p: P) => <I {...p} strokeWidth={1.7}><circle cx="10" cy="8" r="3.6" /><path d="M3.5 19.5c1-3.6 3.6-5.3 6.5-5.3 1.5 0 2.9.4 4 1.3" /><path d="M18 14.5v6M15 17.5h6" /></I>;
export const Share = (p: P) => <I {...p} strokeWidth={1.7}><path d="M12 3.5v11M8 7.5l4-4 4 4" /><path d="M7 10.5H6a1.5 1.5 0 0 0-1.5 1.5v7A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5v-7a1.5 1.5 0 0 0-1.5-1.5h-1" /></I>;
export const ArrowUp = (p: P) => <I {...p} strokeWidth={2.4}><path d="M12 19V5M6 11l6-6 6 6" /></I>;
export const Compose = (p: P) => <I {...p} strokeWidth={1.7}><path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6" /><path d="M17.5 3.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z" /></I>;
export const Speaker = (p: P) => <I {...p} strokeWidth={1.8}><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor" /><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" /></I>;
export const SpeakerOff = (p: P) => <I {...p} strokeWidth={1.8}><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" fill="currentColor" /><path d="M16 9.5l5 5M21 9.5l-5 5" /></I>;
export const Expand = (p: P) => <I {...p} strokeWidth={1.8}><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></I>;
export const Scissors = (p: P) => <I {...p} strokeWidth={1.7}><circle cx="6" cy="6.5" r="2.5" /><circle cx="6" cy="17.5" r="2.5" /><path d="M8.2 7.8L20 17M8.2 16.2L20 7" /></I>;
export const Camera = (p: P) => <I {...p} strokeWidth={1.7}><path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.5-2h6l1.5 2h2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" /><circle cx="12" cy="12.5" r="3.3" /></I>;
export const Quote = (p: P) => <I {...p} strokeWidth={1.7}><path d="M4 6h16M4 11h16M4 16h10" /></I>;
export const MemoryIcon = (p: P) => <I {...p} strokeWidth={1.7}><rect x="3.5" y="4.5" width="17" height="15" rx="3" /><path d="M10.2 9.2v5.6l4.6-2.8z" fill="currentColor" /></I>;
export const AlbumIcon = (p: P) => <I {...p} strokeWidth={1.7}><rect x="3.5" y="6.5" width="17" height="13" rx="2.5" /><path d="M6 4h12" /><path d="M7 15.5l3-3 2.5 2.5 1.5-1.5 3 2" /></I>;
export const Broom = (p: P) => <I {...p} strokeWidth={1.7}><path d="M14.5 3.5l-4 7" /><path d="M8.2 10.2l6.3 3.6-2.3 4.1c-.6 1-2 1.6-3.3 1.2l-3.6-1.1c-.7-.2-1-1-.6-1.6z" /><path d="M7 17l1.5-2.5M9.8 18.3l1.3-2.3" /></I>;
export const SmartAlbum = (p: P) => <I {...p} strokeWidth={1.7}><rect x="3.5" y="6.5" width="17" height="13" rx="2.5" /><path d="M6 4h12" /><path d="M12 9.8l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" /></I>;
