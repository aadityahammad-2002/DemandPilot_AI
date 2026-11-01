import {describe,it,expect} from 'vitest'
import {sourceRunId,replenishmentJobId,type RunCatalog} from './selection'
const catalog:RunCatalog={
 replenishment_runs:[{job_id:'JOB-RP-A',run_id:'RP-RUN-A',forecast_run_id:'FC-RUN-A',recommendation_count:36}],
 forecast_runs:[{run_id:'FC-RUN-A',job_id:'JOB-A',from_date:'2026-01-01',to_date:'2026-02-01',forecast_count:50,has_recommendations:true}],
 latest_forecast_run_id:'FC-RUN-A',latest_replenishment_forecast_run_id:'FC-RUN-A',
 coverage:{recommendations:{from_date:'2026-01-10',to_date:'2026-01-20'}}
}
describe('persisted source run selection',()=>{
 it('resolves pipeline-job identities to business run identities',()=>{
  expect(sourceRunId('JOB-A',catalog)).toBe('FC-RUN-A')
  expect(sourceRunId('FC-RUN-A',catalog)).toBe('FC-RUN-A')
 })
 it('uses the latest linked run when selection is empty',()=>{
  expect(sourceRunId('',catalog)).toBe('FC-RUN-A')
  expect(sourceRunId('',null)).toBe('')
 })
 it('does not silently map unknown run IDs to unrelated data',()=>{
  expect(sourceRunId('FC-RUN-MISSING',catalog)).toBe('FC-RUN-MISSING')
 })
})

describe('replenishment job/source compatibility',()=>{
 it('chooses only a purchasing job linked to the selected source run',()=>{
  expect(replenishmentJobId('','FC-RUN-A',catalog)).toBe('JOB-RP-A')
  expect(replenishmentJobId('','FC-RUN-OTHER',catalog)).toBe('')
 })
})
