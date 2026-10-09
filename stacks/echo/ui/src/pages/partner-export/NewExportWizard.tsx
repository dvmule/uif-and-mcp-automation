import { usePartnerExportWizardStore } from '@/store/partnerExportWizardStore'
import { Step1Source } from './wizard/Step1Source'
import { Step2FormatMapping } from './wizard/Step2FormatMapping'
import { Step3Delivery } from './wizard/Step3Delivery'
import { Step4Schedule } from './wizard/Step4Schedule'
import { ReviewPanel } from './wizard/ReviewPanel'

const STEPS = ['Source', 'Format & Mapping', 'Delivery', 'Schedule', 'Review'] as const

export function NewExportWizard() {
  const step = usePartnerExportWizardStore(s => s.step)
  const setStep = usePartnerExportWizardStore(s => s.setStep)

  const stepClass = (i: number) =>
    i === step ? 'bg-indigo-600 text-white' : i < step ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'
  return (
    <div className="px-6 py-6">
      <h1 className="text-xl font-semibold text-slate-900 mb-4">New export</h1>
      <ol className="flex gap-1 mb-6 text-sm">
        {STEPS.map((label, i) => (
          <li key={label} className={`flex-1 px-3 py-2 rounded ${stepClass(i)}`}><span className="text-xs">Step {i + 1}</span><div className="text-sm font-medium">{label}</div></li>
        ))}
      </ol>

      <div className="bg-white border rounded-md p-4">
        {[<Step1Source key={0} />, <Step2FormatMapping key={1} />, <Step3Delivery key={2} />, <Step4Schedule key={3} />, <ReviewPanel key={4} />][step]}
      </div>

      <div className="flex justify-between mt-4">
        <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)} className="px-3 py-1.5 text-sm border rounded-md disabled:opacity-30">Back</button>
        {step < 4 && (
          <button type="button" onClick={() => setStep(step + 1)} className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700">Next</button>
        )}
      </div>
    </div>
  )
}
