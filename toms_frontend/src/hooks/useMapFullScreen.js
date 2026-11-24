import { useLayoutEffect, useState } from "react";

/**
 * The frame a full-screen map must fill: beside the sidebar, below the top bar.
 *
 * Both are measured from the live layout rather than hardcoded, because the
 * shell changes at narrow widths and the top bar's height is not fixed.
 */
export function measureMapFrame() {
  const sidebar = document.querySelector(".sidebar");
  const topbar = document.querySelector(".topbar");
  // On a phone the sidebar is an off-canvas drawer, so it must not push the
  // map over: it only counts while it takes its own column.
  const sidebarStyle = sidebar ? window.getComputedStyle(sidebar) : null;
  const sidebarInFlow =
    Boolean(sidebar) &&
    sidebarStyle.position !== "fixed" &&
    sidebarStyle.display !== "none";
  return {
    left: sidebarInFlow ? sidebar.getBoundingClientRect().width : 0,
    top: topbar ? topbar.getBoundingClientRect().bottom : 0,
  };
}

/**
 * Full screen for any of the maps.
 *
 * The offsets are measured as the toggle is decided, so `frame` is already
 * correct in the render that first paints the map full screen - the sidebar and
 * top bar are never briefly covered. Escape leaves it, the page behind stops
 * scrolling, and a resize re-measures before paint.
 */
export default function useMapFullScreen() {
  const [fullScreen, setFullScreen] = useState(false);
  const [frame, setFrame] = useState({ left: 0, top: 0 });

  function toggleFullScreen() {
    if (!fullScreen) setFrame(measureMapFrame());
    setFullScreen((value) => !value);
  }

  useLayoutEffect(() => {
    if (!fullScreen) return undefined;
    document.body.classList.add("map-fullscreen-open");

    function onResize() {
      setFrame(measureMapFrame());
    }

    onResize();
    window.addEventListener("resize", onResize);

    function onKey(event) {
      if (event.key === "Escape") setFullScreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("map-fullscreen-open");
      window.removeEventListener("resize", onResize);
      document.removeEventListener("keydown", onKey);
    };
  }, [fullScreen]);

  return { fullScreen, frame, toggleFullScreen };
}
