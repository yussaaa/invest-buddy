import { useState } from 'react'
import { ThumbsUp, ThumbsDown, CheckCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { api } from '../../lib/api'

interface FeedbackButtonsProps {
  runId: string
  userId: string
}

type FeedbackState = 'idle' | 'submitting' | 'submitted' | 'error'

export default function FeedbackButtons({ runId, userId }: FeedbackButtonsProps) {
  const [state, setState] = useState<FeedbackState>('idle')
  const [chosen, setChosen] = useState<1 | -1 | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  async function handleFeedback(score: 1 | -1) {
    if (state === 'submitting' || state === 'submitted') return
    setState('submitting')
    setChosen(score)
    try {
      await api.feedback.submit(runId, userId, score)
      setState('submitted')
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Submission failed')
      setState('error')
    }
  }

  if (state === 'submitted') {
    return (
      <div className="flex items-center gap-2 text-green-400 text-sm">
        <CheckCircle size={16} />
        <span>Thanks for your feedback!</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground uppercase tracking-wide">Was this analysis helpful?</p>
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          onClick={() => handleFeedback(1)}
          disabled={state === 'submitting'}
          className={cn(
            'gap-2',
            chosen === 1
              ? 'border-green-500 bg-green-500/20 text-green-300 hover:bg-green-500/30 hover:text-green-300'
              : 'hover:border-green-500/60 hover:text-green-300'
          )}
        >
          <ThumbsUp size={15} />
          Helpful
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => handleFeedback(-1)}
          disabled={state === 'submitting'}
          className={cn(
            'gap-2',
            chosen === -1
              ? 'border-red-500 bg-red-500/20 text-red-300 hover:bg-red-500/30 hover:text-red-300'
              : 'hover:border-red-500/60 hover:text-red-300'
          )}
        >
          <ThumbsDown size={15} />
          Not helpful
        </Button>
      </div>

      {state === 'error' && (
        <p className="text-xs text-red-400">{errorMsg}</p>
      )}
    </div>
  )
}
