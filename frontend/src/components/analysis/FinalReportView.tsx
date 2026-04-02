import { CheckCircle, AlertTriangle, Clock } from 'lucide-react'
import type { FinalReport } from '../../lib/types'

// ── Confidence meter ─────────────────────────────────────────────────────────

function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color =
    value >= 0.7 ? 'bg-green-500' : value >= 0.4 ? 'bg-yellow-500' : 'bg-red-500'
  const label =
    value >= 0.7 ? 'text-green-400' : value >= 0.4 ? 'text-yellow-400' : 'text-red-400'

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between items-center">
        <span className="text-xs text-slate-400 font-medium uppercase tracking-wide">Overall Confidence</span>
        <span className={['text-sm font-bold', label].join(' ')}>{pct}%</span>
      </div>
      <div className="h-2 rounded-full bg-slate-700 overflow-hidden">
        <div
          className={['h-full rounded-full transition-all duration-700', color].join(' ')}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── Markdown-like renderer ────────────────────────────────────────────────────

function RenderMarkdown({ text }: { text: string }) {
  const lines = text.split('\n')

  return (
    <div className="space-y-2 text-sm text-slate-300 leading-relaxed">
      {lines.map((line, i) => {
        if (line.startsWith('## ')) {
          return (
            <h3 key={i} className="text-base font-semibold text-slate-100 mt-4 mb-1 border-b border-slate-700 pb-1">
              {line.slice(3)}
            </h3>
          )
        }
        if (line.startsWith('# ')) {
          return (
            <h2 key={i} className="text-lg font-bold text-white mt-5 mb-1">
              {line.slice(2)}
            </h2>
          )
        }
        if (line.startsWith('- ') || line.startsWith('* ')) {
          return (
            <div key={i} className="flex gap-2">
              <span className="text-slate-500 mt-1">•</span>
              <span>{line.slice(2)}</span>
            </div>
          )
        }
        if (line.trim() === '') {
          return <div key={i} className="h-1" />
        }
        return <p key={i}>{line}</p>
      })}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface FinalReportViewProps {
  report: FinalReport
}

export default function FinalReportView({ report }: FinalReportViewProps) {
  return (
    <div className="flex flex-col gap-5">
      {/* Executive Summary */}
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-3">
          Executive Summary
        </h2>
        <p className="text-slate-200 text-sm leading-relaxed">{report.summary}</p>
      </div>

      {/* Confidence + timestamp row */}
      <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 flex flex-col gap-3">
        <ConfidenceMeter value={report.overall_confidence} />
        {report.data_as_of && (
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Clock size={12} />
            <span>Data as of {new Date(report.data_as_of).toLocaleString()}</span>
          </div>
        )}
      </div>

      {/* Key Positives + Key Risks */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Positives */}
        <div className="rounded-xl border border-green-500/30 bg-green-500/5 p-5">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle size={15} className="text-green-400" />
            <h3 className="text-sm font-semibold text-green-300 uppercase tracking-wide">Key Positives</h3>
          </div>
          <ul className="space-y-2">
            {report.key_positives.map((item, i) => (
              <li key={i} className="flex gap-2 text-sm text-slate-300">
                <span className="text-green-500 mt-0.5 shrink-0">▲</span>
                <span>{item}</span>
              </li>
            ))}
            {report.key_positives.length === 0 && (
              <li className="text-sm text-slate-500 italic">None identified</li>
            )}
          </ul>
        </div>

        {/* Risks */}
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-5">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={15} className="text-red-400" />
            <h3 className="text-sm font-semibold text-red-300 uppercase tracking-wide">Key Risks</h3>
          </div>
          <ul className="space-y-2">
            {report.key_risks.map((item, i) => (
              <li key={i} className="flex gap-2 text-sm text-slate-300">
                <span className="text-red-500 mt-0.5 shrink-0">▼</span>
                <span>{item}</span>
              </li>
            ))}
            {report.key_risks.length === 0 && (
              <li className="text-sm text-slate-500 italic">None identified</li>
            )}
          </ul>
        </div>
      </div>

      {/* Detailed Analysis */}
      {report.detailed_analysis && (
        <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-5">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-4">
            Detailed Analysis
          </h2>
          <RenderMarkdown text={report.detailed_analysis} />
        </div>
      )}
    </div>
  )
}
