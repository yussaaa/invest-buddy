import { useState } from 'react'
import { ThumbsUp, ThumbsDown, CheckCircle } from 'lucide-react'
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
      <p className="text-xs text-slate-400 uppercase tracking-wide">Was this analysis helpful?</p>
      <div className="flex items-center gap-3">
        <button
          onClick={() => handleFeedback(1)}
          disabled={state === 'submitting'}
          className={[
            'flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition-colors',
            chosen === 1
              ? 'border-green-500 bg-green-500/20 text-green-300'
              : 'border-slate-700 bg-slate-800 text-slate-300 hover:border-green-500/60 hover:text-green-300',
            state === 'submitting' ? 'opacity-50 cursor-not-allowed' : '',
          ].join(' ')}
        >
          <ThumbsUp size={15} />
          Helpful
        </button>

        <button
          onClick={() => handleFeedback(-1)}
          disabled={state === 'submitting'}
          className={[
            'flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition-colors',
            chosen === -1
              ? 'border-red-500 bg-red-500/20 text-red-300'
              : 'border-slate-700 bg-slate-800 text-slate-300 hover:border-red-500/60 hover:text-red-300',
            state === 'submitting' ? 'opacity-50 cursor-not-allowed' : '',
          ].join(' ')}
        >
          <ThumbsDown size={15} />
          Not helpful
        </button>
      </div>

      {state === 'error' && (
        <p className="text-xs text-red-400">{errorMsg}</p>
      )}
    </div>
  )
}
