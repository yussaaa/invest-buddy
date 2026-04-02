import { useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'

export default function DisclaimerBanner() {
  const [dismissed, setDismissed] = useState(false)

  if (dismissed) return null

  return (
    <div className="flex items-start gap-3 rounded-lg border border-yellow-500/40 bg-yellow-500/10 px-4 py-3">
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-yellow-400" />
      <p className="flex-1 text-sm text-yellow-200">
        <span className="font-semibold">Disclaimer: </span>
        This analysis is for informational purposes only and does not constitute financial advice.
        Always do your own research and consult a qualified financial advisor before making investment decisions.
      </p>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-yellow-400 hover:text-yellow-200 transition-colors"
        aria-label="Dismiss disclaimer"
      >
        <X size={16} />
      </button>
    </div>
  )
}
