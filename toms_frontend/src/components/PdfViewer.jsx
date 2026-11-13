/**
 * In-app PDF reader built on pdf.js.
 *
 * Django sends `X-Frame-Options: DENY` with every response, so a PDF cannot be
 * shown in an <iframe> ("refused to connect"). Browsers also refuse to render a
 * PDF inline in a frame on many phones. Instead of loosening that header, the
 * document is fetched with the session cookie, parsed by pdf.js and painted on
 * canvases: one code path for desktop and mobile, with zoom, rotation and a
 * download that still uses the server's own file.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { api } from "../api/client.js";
import { Button } from "./ui.jsx";

GlobalWorkerOptions.workerSrc = workerSrc;

// Served by the pdf.js asset plugin in vite.config.js (dev and build alike).
const PDFJS_BASE = `${import.meta.env.BASE_URL}pdfjs/`;

// pdf.js 4.x uses Promise.withResolvers; older Safari on phones lacks it.
if (typeof Promise.withResolvers !== "function") {
  Promise.withResolvers = function withResolvers() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };
}

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 3;

function messageFor(error) {
  if (error?.status === 401) return "Your session has expired. Sign in again to view this PDF.";
  if (error?.status === 403) return "You do not have permission to view this PDF.";
  if (error?.status === 404) return "This PDF could not be found.";
  return error?.message || "The PDF could not be loaded.";
}

export default function PdfViewer({ path, title = "PDF" }) {
  const [pdf, setPdf] = useState(null);
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [fit, setFit] = useState(true);
  const [fitScale, setFitScale] = useState(0);
  const [rotation, setRotation] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [attempt, setAttempt] = useState(0);

  const scrollRef = useRef(null);
  const canvasRefs = useRef([]);

  // Fetch the document through the authenticated API.
  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    let loaded = null;

    setLoading(true);
    setError("");
    setPdf(null);
    setPageCount(0);
    setFit(true);
    setZoom(1);
    setRotation(0);

    (async () => {
      try {
        const response = await api.download(path);
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        const data = await blob.arrayBuffer();
        loaded = await getDocument({
          data,
          isEvalSupported: false,
          standardFontDataUrl: `${PDFJS_BASE}standard_fonts/`,
          cMapUrl: `${PDFJS_BASE}cmaps/`,
          cMapPacked: true,
        }).promise;
        if (cancelled) return;
        setPdf(loaded);
        setPageCount(loaded.numPages);
        setDownloadUrl(objectUrl);
      } catch (err) {
        if (!cancelled) setError(messageFor(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (loaded) loaded.destroy().catch(() => {});
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, attempt]);

  // Fit-to-width follows the viewport, including a phone rotating.
  useEffect(() => {
    if (!pdf) return undefined;
    const container = scrollRef.current;
    if (!container) return undefined;

    let cancelled = false;
    async function measure() {
      const page = await pdf.getPage(1);
      if (cancelled) return;
      const base = page.getViewport({ scale: 1, rotation });
      const available = Math.max(220, container.clientWidth - 26);
      setFitScale(available / base.width);
    }
    measure().catch(() => {});

    const observer = new ResizeObserver(() => {
      measure().catch(() => {});
    });
    observer.observe(container);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [pdf, rotation]);

  const scale = fit ? fitScale : zoom;

  // Paint every page whenever the document, scale or rotation changes.
  useEffect(() => {
    if (!pdf || !scale) return undefined;
    let cancelled = false;
    const tasks = [];

    async function renderAll() {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      for (let number = 1; number <= pageCount; number += 1) {
        if (cancelled) return;
        const page = await pdf.getPage(number);
        if (cancelled) return;
        const viewport = page.getViewport({ scale, rotation });
        const canvas = canvasRefs.current[number - 1];
        if (!canvas) continue;

        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;

        const task = page.render({
          canvasContext: canvas.getContext("2d"),
          viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        });
        tasks.push(task);
        try {
          await task.promise;
        } catch (err) {
          if (err?.name !== "RenderingCancelledException") throw err;
        }
      }
    }

    renderAll().catch((err) => {
      if (!cancelled && err?.name !== "RenderingCancelledException") {
        setError("The PDF could not be rendered.");
      }
    });

    return () => {
      cancelled = true;
      tasks.forEach((task) => task.cancel());
    };
  }, [pdf, pageCount, scale, rotation]);

  const percent = Math.round((scale || 1) * 100);

  function zoomBy(delta) {
    setFit(false);
    setZoom((current) => {
      const base = fit ? fitScale || 1 : current;
      return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round((base + delta) * 100) / 100));
    });
  }

  const pages = useMemo(() => Array.from({ length: pageCount }, (_, index) => index), [pageCount]);

  if (loading) {
    return (
      <div className="state">
        <span className="spinner" aria-hidden="true" /> Loading PDF…
      </div>
    );
  }

  if (error) {
    return (
      <div className="pdf-error">
        <p>{error}</p>
        <Button variant="ghost" size="sm" onClick={() => setAttempt((value) => value + 1)}>
          Try again
        </Button>
      </div>
    );
  }

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => zoomBy(-0.25)}
          disabled={scale <= MIN_ZOOM}
          title="Zoom out"
        >
          −
        </Button>
        <span className="pdf-zoom" aria-live="polite">
          {percent}%
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => zoomBy(0.25)}
          disabled={scale >= MAX_ZOOM}
          title="Zoom in"
        >
          +
        </Button>
        <Button
          variant={fit ? "primary" : "ghost"}
          size="sm"
          onClick={() => setFit(true)}
          title="Fit the page width to this screen"
        >
          Fit width
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setRotation((value) => (value + 90) % 360)}
          title="Rotate the pages"
        >
          Rotate
        </Button>
        <span className="pdf-count">
          {pageCount} page{pageCount === 1 ? "" : "s"}
        </span>
        <span className="spacer" />
        {downloadUrl && (
          <a className="btn btn-ghost btn-sm" href={downloadUrl} download={`${title}.pdf`}>
            Download
          </a>
        )}
      </div>

      <div className="pdf-scroll" ref={scrollRef}>
        {pages.length ? (
          <div className="pdf-pages">
            {pages.map((index) => (
              <canvas
                key={index}
                ref={(element) => {
                  canvasRefs.current[index] = element;
                }}
                className="pdf-page"
                aria-label={`${title} — page ${index + 1} of ${pageCount}`}
              />
            ))}
          </div>
        ) : (
          <div className="state">This document has no pages.</div>
        )}
      </div>
    </div>
  );
}
