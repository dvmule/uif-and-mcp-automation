import { useState } from 'react'
import { toast } from 'sonner'

interface Props { value: string; label: string }

export function CopyLink({ value, label }: Props) {
  const [copied, setCopied] = useState(false)
  const handle = async () => {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    toast.success(`Copied ${label} path to clipboard`)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <button onClick={handle} type="button" className={`text-label font-semibold ${copied ? 'text-ink-mute' : 'text-accent hover:text-ink'}`}>
      {copied ? 'Copied ✓' : 'Copy'}
    </button>
  )
}
