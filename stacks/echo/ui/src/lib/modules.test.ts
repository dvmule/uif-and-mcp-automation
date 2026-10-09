import { describe, it, expect } from 'vitest'
import { MODULES, getModuleByRoutePrefix, type Module } from './modules'

describe('modules registry', () => {
  it('exposes the five v1 modules in order', () => {
    const liveIds = MODULES.filter((m) => m.status === 'live').map((m) => m.id)
    expect(liveIds).toEqual([
      'home',
      'data-sync',
      'partner-export',
      'data-quality',
      'catalog',
    ])
  })

  it('marks future modules as soon', () => {
    const soonIds = MODULES.filter((m) => m.status === 'soon').map((m) => m.id)
    expect(soonIds).toEqual(['streaming', 'file-ingest', 'reverse-etl'])
  })

  it('every module has label, icon, route', () => {
    MODULES.forEach((m: Module) => {
      expect(m.id).toBeTruthy()
      expect(m.label).toBeTruthy()
      expect(m.icon).toBeTruthy()
      expect(m.route).toMatch(/^\//)
    })
  })

  it('home route is /', () => {
    const home = MODULES.find((m) => m.id === 'home')!
    expect(home.route).toBe('/')
  })

  it('data-sync route is /m/data-sync', () => {
    const ds = MODULES.find((m) => m.id === 'data-sync')!
    expect(ds.route).toBe('/m/data-sync')
  })

  it('getModuleByRoutePrefix matches /m/data-sync/syncs to data-sync', () => {
    const m = getModuleByRoutePrefix('/m/data-sync/syncs')
    expect(m?.id).toBe('data-sync')
  })

  it('getModuleByRoutePrefix matches / to home', () => {
    const m = getModuleByRoutePrefix('/')
    expect(m?.id).toBe('home')
  })

  it('getModuleByRoutePrefix matches /m/partner-export to partner-export', () => {
    const m = getModuleByRoutePrefix('/m/partner-export')
    expect(m?.id).toBe('partner-export')
  })

  it('getModuleByRoutePrefix returns home for unknown routes', () => {
    const m = getModuleByRoutePrefix('/settings/connections')
    expect(m?.id).toBe('home')
  })
})
