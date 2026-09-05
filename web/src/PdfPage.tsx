import { useEffect, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfPage({ id, page }: { id: string; page: number }) {
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [rendered, setRendered] = useState(false);
  const [width, setWidth] = useState(600);
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const node = container.current!;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(150, entry.contentRect.width - 40)),
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setDocument(null);
    setError("");
    let active = true;
    const task = getDocument({
      url: `/api/papers/${id}/file`,
      cMapUrl: "/pdfjs/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "/pdfjs/standard_fonts/",
      wasmUrl: "/pdfjs/wasm/",
    });
    task.promise
      .then((pdf) => {
        if (active) setDocument(pdf);
      })
      .catch(() => {
        if (active)
          setError("原 PDF 暂时无法打开，请检查数据服务或原文件是否完整。");
      });
    return () => {
      active = false;
      void task.destroy();
    };
  }, [id]);

  useEffect(() => {
    setRendered(false);
    if (container.current) container.current.scrollTop = 0;
    if (!document) return;
    setError("");
    let active = true;
    let renderTask:
      | ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]>
      | undefined;
    document
      .getPage(page + 1)
      .then((pdfPage) => {
        if (!active || !canvas.current) return;
        const original = pdfPage.getViewport({ scale: 1 });
        const viewport = pdfPage.getViewport({ scale: width / original.width });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const node = canvas.current;
        node.width = Math.floor(viewport.width * ratio);
        node.height = Math.floor(viewport.height * ratio);
        node.style.width = `${viewport.width}px`;
        node.style.height = `${viewport.height}px`;
        renderTask = pdfPage.render({
          canvas: node,
          viewport,
          transform: [ratio, 0, 0, ratio, 0, 0],
        });
        return renderTask.promise;
      })
      .then(() => {
        if (active) setRendered(true);
      })
      .catch(() => {
        if (active) setError("这一页暂时无法显示，请重新打开论文。");
      });
    return () => {
      active = false;
      renderTask?.cancel();
    };
  }, [document, page, width]);

  return (
    <div className="pdf-surface" ref={container}>
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : (
        <>
          {!rendered && (
            <p className="pdf-loading" role="status">
              正在打开原文…
            </p>
          )}
          <canvas
            ref={canvas}
            style={{ visibility: rendered ? "visible" : "hidden" }}
            aria-label={`原 PDF 第 ${page + 1} 页`}
          />
        </>
      )}
    </div>
  );
}
