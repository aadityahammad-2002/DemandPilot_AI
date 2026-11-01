import {defineConfig,devices} from '@playwright/test'
import {fileURLToPath} from 'node:url'
const backendPython = process.platform === 'win32'
  ? '"'+fileURLToPath(new URL('../backend/.venv/Scripts/python.exe', import.meta.url))+'"'
  : 'python'
export default defineConfig({
  testDir:'./e2e',timeout:45_000,workers:1,retries:0,
  use:{baseURL:'http://127.0.0.1:5173',trace:'retain-on-failure'},
  projects:[{name:'chromium',use:{...devices['Desktop Chrome']}}],
  webServer:[
    {command:'npm run dev',url:'http://127.0.0.1:5173',reuseExistingServer:true,timeout:90_000},
    {command:backendPython+' -m uvicorn app.integration.api:create_app --factory --host 127.0.0.1 --port 8000',
     cwd:fileURLToPath(new URL('../backend/', import.meta.url)),url:'http://127.0.0.1:8000/health',reuseExistingServer:true,timeout:90_000},
  ],
})
