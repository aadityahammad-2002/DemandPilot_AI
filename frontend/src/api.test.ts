import {describe,it,expect,vi,afterEach} from 'vitest'
import {request,route,APIError,fmtNumber} from './api'
afterEach(()=>vi.unstubAllGlobals())
describe('trusted local API client',()=>{
  it('calls without authentication or stale authorization headers',async()=>{
    const fetch = vi.fn().mockImplementation(async () =>
  new Response(JSON.stringify({ sku_count: 3 }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  })
)
    vi.stubGlobal('fetch',fetch)
    expect((await request<{sku_count:number}>('/api/v1/overview')).sku_count).toBe(3)
    const options=fetch.mock.calls[0][1] as RequestInit
    expect(new Headers(options.headers).has('Authorization')).toBe(false)
    await request('/api/v1/skus',{headers:{Authorization:'Bearer stale-token'}})
    expect(new Headers((fetch.mock.calls[1][1] as RequestInit).headers).has('Authorization')).toBe(false)
  })
  it('keeps network and API errors distinct from zero results',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(request('/api/v1/inventory')).rejects.toThrow('unavailable')
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({error:{message:'Database unavailable'}}),{status:503})))
    await expect(request('/api/v1/overview')).rejects.toBeInstanceOf(APIError)
  })
  it('preserves run-aware query parameters and missing values',()=>{
    expect(route('forecasts',{run_id:'JOB-123',store_id:'STORE-1',sku_id:null})).toContain('run_id=JOB-123')
    expect(route('forecasts',{sku_id:null})).not.toContain('sku_id=')
    expect(fmtNumber(null)).toBe('—')
  })
})

describe('backend source-run query contract',()=>{
  it('passes actual run identifiers and inclusive dates without silently replacing them',()=>{
    const url=route('recommendations',{forecast_run_id:'FC-RUN-123',from_date:'2026-01-01',to_date:'2026-01-31',outcome:'zero'})
    expect(url).toContain('forecast_run_id=FC-RUN-123')
    expect(url).toContain('from_date=2026-01-01')
    expect(url).toContain('to_date=2026-01-31')
    expect(url).toContain('outcome=zero')
  })
})
