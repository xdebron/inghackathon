import type { ReactNode } from 'react'

// Ajan cevapları için küçük, güvenli Markdown görüntüleyici: HTML enjekte etmez, React öğeleri üretir.
// Destek: # başlıklar, **kalın**, *italik*, `kod`, - / 1. listeler, | tablolar |, --- ayraç, ⚠️ gibi düz metin.

function inline(s: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index))
    const t = m[0]
    const k = `${key}-${i++}`
    if (t.startsWith('**')) out.push(<strong key={k}>{t.slice(2, -2)}</strong>)
    else if (t.startsWith('`')) out.push(<code key={k}>{t.slice(1, -1)}</code>)
    else out.push(<em key={k}>{t.slice(1, -1)}</em>)
    last = m.index + t.length
  }
  if (last < s.length) out.push(s.slice(last))
  return out
}

const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())

export function MiniMarkdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const k = `b${i}`
    if (!line.trim()) { i++; continue }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const level = Math.min(h[1].length + 2, 6)
      const Tag = `h${level}` as 'h3'
      blocks.push(<Tag key={k} className="md-h">{inline(h[2], k)}</Tag>)
      i++
      continue
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { blocks.push(<hr key={k} />); i++; continue }
    if (line.trim().startsWith('|')) {
      const rows: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(lines[i++])
      const body = rows.filter((r) => !/^[\s|:-]+$/.test(r))   // '|---|:--:|' ayırıcı satırı
      const [head, ...rest] = body
      if (!head) continue
      blocks.push(
        <table key={k} className="md-table">
          <thead><tr>{cells(head).map((c, j) => <th key={j}>{inline(c, `${k}h${j}`)}</th>)}</tr></thead>
          <tbody>{rest.map((r, ri) => <tr key={ri}>{cells(r).map((c, j) => <td key={j}>{inline(c, `${k}${ri}${j}`)}</td>)}</tr>)}</tbody>
        </table>,
      )
      continue
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line)
      const items: string[] = []
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ''))
      const L = ordered ? 'ol' : 'ul'
      blocks.push(<L key={k} className="md-list">{items.map((it, j) => <li key={j}>{inline(it, `${k}${j}`)}</li>)}</L>)
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*([-*•]|\d+[.)])\s+|\s*-{3,}\s*$)/.test(lines[i])) para.push(lines[i++])
    blocks.push(<p key={k}>{para.map((p, j) => <span key={j}>{inline(p, `${k}${j}`)}{j < para.length - 1 && <br />}</span>)}</p>)
  }
  return <div className="md">{blocks}</div>
}
