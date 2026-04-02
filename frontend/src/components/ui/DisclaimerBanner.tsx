import { useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'

export default function DisclaimerBanner() {
  const [dismissed, setDismissed] = useState(false)

  if (dismissed) return null

  return (
    <Alert className="border-yellow-500/40 bg-yellow-500/10 [&>svg]:text-yellow-400">
      <AlertTriangle size={16} />
      <AlertDescription className="flex items-start gap-3 text-yellow-200">
        <span className="flex-1 text-sm">
          <span className="font-semibold">Disclaimer: </span>
          This analysis is for informational purposes only and does not constitute financial advice.
          Always do your own research and consult a qualified financial advisor before making investment decisions.
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setDismissed(true)}
          className="shrink-0 h-6 w-6 text-yellow-400 hover:text-yellow-200 hover:bg-yellow-500/20"
          aria-label="Dismiss disclaimer"
        >
          <X size={16} />
        </Button>
      </AlertDescription>
    </Alert>
  )
}
