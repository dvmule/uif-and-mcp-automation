import { Toaster as SonnerToaster } from 'sonner'

export function Toaster() {
  return (
    <SonnerToaster
      position="bottom-right"
      duration={3000}
      toastOptions={{
        style: {
          background: '#0D004D',
          color: '#FFFFFF',
          border: '1px solid #512EAB',
          fontFamily: '"Plus Jakarta Sans", Helvetica, Verdana, sans-serif',
        },
        className: 'pearson-toast',
      }}
    />
  )
}
