import { useEffect, useState } from "react";

// The site is laptop-first, but plenty of callers will open it on a phone.
// This flips layouts to a single stacked column / smaller type below `bp`.
// Desktop rendering is byte-for-byte unchanged (the hook returns false there).
export function useIsMobile(bp = 760): boolean {
  const [m, setM] = useState(
    () => typeof window !== "undefined" && window.innerWidth < bp
  );
  useEffect(() => {
    const onResize = () => setM(window.innerWidth < bp);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [bp]);
  return m;
}
