import { CheckCircle, AlertTriangle, Clock } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import type { FinalReport } from '../../lib/types'
import Markdown from '@/components/ui/Markdown'

// -- Confidence meter ---------------------------------------------------------

function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const indicatorColor =
    value >= 0.7 ? 'bg-green-500' : value >= 0.4 ? 'bg-yellow-500' : 'bg-red-500'
  const labelColor =
    value >= 0.7 ? 'text-green-400' : value >= 0.4 ? 'text-yellow-400' : 'text-red-400'

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between items-center">
        <span className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Overall Confidence</span>
        <span className={cn('text-sm font-bold', labelColor)}>{pct}%</span>
      </div>
      <Progress value={pct} indicatorClassName={indicatorColor} />
    </div>
  )
}

// -- Main component -----------------------------------------------------------

interface FinalReportViewProps {
  report: FinalReport
}

export default function FinalReportView({ report }: FinalReportViewProps) {
  return (
    <div className="flex flex-col gap-5">
      {/* Executive Summary */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
            Executive Summary
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-foreground/90 text-sm leading-relaxed">{report.summary}</p>
        </CardContent>
      </Card>

      {/* Confidence + timestamp row */}
      <Card>
        <CardContent className="pt-6 flex flex-col gap-3">
          <ConfidenceMeter value={report.overall_confidence} />
          {report.data_as_of && (
            <>
              <Separator />
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Clock size={12} />
                <span>Data as of {new Date(report.data_as_of).toLocaleString()}</span>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Key Positives + Key Risks */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Positives */}
        <Card className="border-green-500/30 bg-green-500/5">
          <CardContent className="pt-5">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle size={15} className="text-green-400" />
              <h3 className="text-sm font-semibold text-green-300 uppercase tracking-wide">Key Positives</h3>
            </div>
            <ul className="space-y-2">
              {report.key_positives.map((item, i) => (
                <li key={i} className="flex gap-2 text-sm text-foreground/80">
                  <span className="text-green-500 mt-0.5 shrink-0">&#9650;</span>
                  <span>{item}</span>
                </li>
              ))}
              {report.key_positives.length === 0 && (
                <li className="text-sm text-muted-foreground italic">None identified</li>
              )}
            </ul>
          </CardContent>
        </Card>

        {/* Risks */}
        <Card className="border-red-500/30 bg-red-500/5">
          <CardContent className="pt-5">
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle size={15} className="text-red-400" />
              <h3 className="text-sm font-semibold text-red-300 uppercase tracking-wide">Key Risks</h3>
            </div>
            <ul className="space-y-2">
              {report.key_risks.map((item, i) => (
                <li key={i} className="flex gap-2 text-sm text-foreground/80">
                  <span className="text-red-500 mt-0.5 shrink-0">&#9660;</span>
                  <span>{item}</span>
                </li>
              ))}
              {report.key_risks.length === 0 && (
                <li className="text-sm text-muted-foreground italic">None identified</li>
              )}
            </ul>
          </CardContent>
        </Card>
      </div>

      {/* Detailed Analysis */}
      {report.detailed_analysis && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              Detailed Analysis
            </CardTitle>
          </CardHeader>
          <Separator />
          <CardContent className="pt-4">
            <Markdown text={report.detailed_analysis} />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
