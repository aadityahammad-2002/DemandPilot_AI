import {test,expect} from '@playwright/test'

// These are real-API acceptance tests. They require an already prepared local
// PostgreSQL database and running FastAPI; they never fabricate business data.
test.beforeEach(async({request})=>{
  const r=await request.get('http://127.0.0.1:8000/api/v1/overview')
  if(!r.ok())throw new Error(`Real local API unavailable: ${r.status()}`)
  const v=await r.json()
  if(!(v.sku_count>0&&v.store_count>0&&v.forecast_records>0))
    throw new Error('Populate the approved small development dataset before the browser acceptance tests')
})

test('welcome and dashboard selection work without login',async({page})=>{
 await page.goto('/')
 await expect(page.getByText('Multi-Store Demand Forecasting & Replenishment Intelligence')).toBeVisible()
 await page.getByRole('button',{name:/Manage Retail Operations/}).click()
 await expect(page.getByRole('heading',{name:'Overview'})).toBeVisible()
 await expect(page.getByText('Loaded stores')).toBeVisible()
 await page.getByRole('button',{name:/Developer/}).first().click()
 await expect(page.getByText('About This Dashboard')).toBeVisible()
 await expect(page.getByText(/not explicitly required by the original YottaFlex AI challenge/)).toBeVisible()
})

test('manager recommendation reaches same technical decision',async({page})=>{
 await page.goto('/operations/recommendations')
 const row=page.locator('.yf-table tbody tr').first()
 await expect(row).toBeVisible()
 const firstId=await row.locator('td').first().innerText()
 await row.click()
 await expect(page.getByRole('dialog',{name:'Recommendation detail'})).toBeVisible()
 await page.getByRole('button',{name:/View Technical Explanation/}).click()
 await expect(page).toHaveURL(/\/developer\/replenishment/)
 await expect(page.getByText(firstId).first()).toBeVisible()
})

test('recommendation drawer renders complete persisted decision trace',async({page,request})=>{
  await page.goto('/operations/recommendations')

  const row=page.locator('.yf-table tbody tr').first()
  await expect(row).toBeVisible()

  const recommendationId=(await row.locator('td').first().innerText()).trim()

  const apiResponse=await request.get(
    'http://127.0.0.1:8000/api/v1/recommendations/'+encodeURIComponent(recommendationId)
  )

  expect(apiResponse.ok()).toBeTruthy()

  const detail=await apiResponse.json()

  expect(detail.recommendation.recommendation_id).toBe(recommendationId)

  const expectedTrace=detail.recommendation.decision_trace

  expect(expectedTrace).toBeTruthy()
  expect(Object.keys(expectedTrace).length).toBeGreaterThan(0)

  await row.click()

  const drawer=page.getByRole('dialog',{name:'Recommendation detail'})

  await expect(drawer).toBeVisible()
  await expect(drawer.getByText(recommendationId)).toBeVisible()
  await expect(drawer.getByText('Technical provenance')).toBeVisible()

  await drawer.getByText('Expand full recorded trace').click()

  const traceElement=drawer.locator('pre.yf-json')

  await expect(traceElement).toBeVisible()

  const renderedTrace=await traceElement.innerText()

  expect(JSON.parse(renderedTrace)).toEqual(expectedTrace)
})
test('technical walkthrough is functional and schema is source-backed',async({page})=>{
 await page.goto('/developer')
 await expect(page.getByText('Explore How YottaFlex Works')).toBeVisible()
 await page.getByRole('button',{name:'Next'}).click()
 await expect(page.getByRole('heading',{name:'Data Generation and Validation'})).toBeVisible()
 await page.getByRole('button',{name:/Open section/}).click()
 await expect(page).toHaveURL(/\/developer\/generation/)
 await page.goto('/developer/schema')
 await expect(page.getByText('Ten required retail entities')).toBeVisible()
 await expect(page.getByText('SQL type')).toBeVisible()
})

test('historical and technical views never show auth prompt',async({page})=>{
 await page.goto('/developer/pipeline')
 await expect(page.getByText('Execution history')).toBeVisible()
 await expect(page.getByRole('textbox',{name:/API access token/i})).toHaveCount(0)
 await page.goto('/operations/stores')
 await expect(page.getByText('Inventory snapshots')).toBeVisible()
})

test('source run selection and date reset preserve populated recommendations',async({page})=>{
 await page.goto('/operations/recommendations')
 const selector=page.getByLabel('Forecast run')
 await expect(selector).toBeVisible()
 const options=selector.locator('option')
 await expect(options).not.toHaveCount(0)
 const sourceValues=await options.evaluateAll(nodes=>nodes.map(n=>(n as HTMLOptionElement).value).filter(Boolean))
 for(const runId of sourceValues)expect(runId).not.toMatch(/^JOB-/)
 const firstRow=page.locator('.yf-table tbody tr').first()
 await expect(firstRow).toBeVisible()
 await page.getByLabel('From date').fill('2030-01-01')
 await page.getByLabel('To date').fill('2030-01-31')
 await expect(page.getByText('No records match the current selection.')).toBeVisible()
 await page.getByRole('button',{name:'Reset filters'}).click()
 await expect(firstRow).toBeVisible()
})

test('wide table does not trap page touchpad scrolling',async({page})=>{
 await page.goto('/operations/stores')
 const table=page.locator('.yf-table-scroll').first()
 await expect(table).toBeVisible()
 const overscroll=await table.evaluate(el=>getComputedStyle(el).overscrollBehaviorY)
 expect(overscroll).not.toBe('contain')
 expect(overscroll).not.toBe('none')
})
