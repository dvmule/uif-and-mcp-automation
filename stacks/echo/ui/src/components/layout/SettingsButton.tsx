import { useNavigate } from 'react-router-dom'
import { Settings } from 'lucide-react'

export function SettingsButton() {
  const navigate = useNavigate()
  return (
    <button
      type="button"
      aria-label="Settings"
      onClick={() => navigate('/settings')}
      className="rounded p-1 text-white/80 transition-colors hover:bg-white/10 hover:text-white"
    >
      <Settings size={16} strokeWidth={1.9} />
    </button>
  )
}
