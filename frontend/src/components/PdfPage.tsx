import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Minus, Plus } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Box } from '../lib/types'
import { Skeleton } from './ui'

/** Renders a server-rasterised PDF page with highlight rectangles taken from real word coordinates.
 *  (PDF points -> CSS px by the displayed width / page width ratio.) */
export function PdfViewer({ docId, pageCount, page, onPage, boxes, kind, pageSize }: {
  docId: number; pageCount: number; page: number; onPage: (p: number) => void; boxes: Box[]; kind: 'raw' | 'edited'; pageSize?: { w: number; h: number }
}) {
  const [zoom, setZoom] = useState(1)
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  const imgRef = useRef<HTMLImageElement>(null)
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null)
  const wrap = useRef<HTMLDivElement>(null)

  useEffect(() => { setLoaded(false); setFailed(false) }, [docId, page])
  const meta = useQuery({ queryKey: ['doctext', docId], queryFn: () => api.get<any>(`/api/documents/${docId}/text`), staleTime: 60_000 })
  const pg = meta.data?.pages.find((p: any) => p.page_no === page)
  const pts = pageSize || (pg?.width ? { w: pg.width, h: pg.height } : { w: 595, h: 842 })
  const scale = dims ? dims.w / pts.w : 1
  const mine = boxes.filter((b) => b.page === page)

  useEffect(() => {
    if (loaded && mine.length && wrap.current && dims) {
      const y = mine[0].y0 * scale
      wrap.current.scrollTo({ top: Math.max(0, y - 80), behavior: 'smooth' })
    }
  }, [loaded, mine.length, boxes, dims, scale])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-1">
          <button className="btn-ghost !px-2 !py-1" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1} aria-label="Previous page"><ChevronLeft size={14} /></button>
          <span className="tabular-nums text-muted">Page {page} / {pageCount}</span>
          <button className="btn-ghost !px-2 !py-1" onClick={() => onPage(Math.min(pageCount, page + 1))} disabled={page >= pageCount} aria-label="Next page"><ChevronRight size={14} /></button>
        </div>
        <div className="flex items-center gap-1">
          <button className="btn-ghost !px-2 !py-1" onClick={() => setZoom((z) => Math.max(0.6, +(z - 0.2).toFixed(1)))} aria-label="Zoom out"><Minus size={14} /></button>
          <span className="w-10 text-center tabular-nums text-muted">{Math.round(zoom * 100)}%</span>
          <button className="btn-ghost !px-2 !py-1" onClick={() => setZoom((z) => Math.min(2.4, +(z + 0.2).toFixed(1)))} aria-label="Zoom in"><Plus size={14} /></button>
        </div>
      </div>
      <div ref={wrap} className="relative min-h-0 flex-1 overflow-auto rounded-xl border bg-black/20 p-2">
        {!loaded && !failed && <Skeleton className="h-96 w-full" />}
        {failed && <p className="p-6 text-center text-sm text-danger">The page could not be rendered.</p>}
        <div className="relative mx-auto" style={{ width: `${zoom * 100}%`, display: loaded ? 'block' : 'none' }}>
          <img ref={imgRef} src={`/api/documents/${docId}/pages/${page}/image?zoom=1.6`} alt={`${kind === 'raw' ? 'RAW' : 'Edited'} document page ${page}`} className="block w-full rounded bg-white"
            onLoad={(e) => { setLoaded(true); setDims({ w: e.currentTarget.clientWidth, h: e.currentTarget.clientHeight }) }} onError={() => setFailed(true)} draggable={false} />
          {loaded && dims && mine.map((b, i) => (
            <div key={i} className={`hl-box ${kind}`} style={{ left: `${(b.x0 / pts.w) * 100 - 0.3}%`, top: `${(b.y0 / pts.h) * 100 - 0.2}%`, width: `${((b.x1 - b.x0) / pts.w) * 100 + 0.6}%`, height: `${((b.y1 - b.y0) / pts.h) * 100 + 0.4}%` }} />
          ))}
        </div>
      </div>
    </div>
  )
}
